from __future__ import annotations

import csv
import io
import os
import signal
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping, Sequence

from .config import (
    AppConfig,
    ConfigError,
    ProxySettings,
    default_config,
    find_adjacent_voice_packet,
    load_config,
)
from .discovery import (
    DiscordInstallation,
    SPECS,
    default_config_path,
    discover_channel,
    installation_for_executable,
    resolve_adjacent_config,
)
from .native import NativeShimError, ensure_native_shim
from .proxy_bridge import LocalProxyBridge, probe_proxy


class LaunchError(RuntimeError):
    """Discord could not be started with the requested isolation."""


@dataclass(frozen=True)
class LaunchPlan:
    installation: DiscordInstallation
    config_path: Path | None
    config: AppConfig
    command: tuple[str, ...]
    environment: dict[str, str]
    native_shim: Path | None

    @property
    def safe_command(self) -> str:
        return " ".join(self.command)


@dataclass(frozen=True)
class LaunchResult:
    pid: int
    exit_code: int | None
    proxy_active: bool
    voice_active: bool


def load_channel_config(
    installation: DiscordInstallation,
    explicit_path: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[AppConfig, Path | None]:
    path = (
        Path(explicit_path).expanduser().absolute()
        if explicit_path
        else resolve_adjacent_config(installation)
    )
    config = default_config() if path is None else load_config(path, environ=environ)
    if config.voice.packet_file is None:
        directories: list[Path] = []
        if installation.executable is not None:
            directories.append(installation.executable.parent)
        if installation.root is not None:
            directories.append(installation.root)
        for directory in dict.fromkeys(directories):
            packet_file = find_adjacent_voice_packet(directory)
            if packet_file is not None:
                config = replace(
                    config,
                    voice=replace(config.voice, packet_file=packet_file),
                )
                break
    return config, path


def prepare_launch(
    channel: str,
    *,
    config_path: Path | None = None,
    installation: DiscordInstallation | None = None,
    bridge_url: str | None = None,
    native_source: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> LaunchPlan:
    env_source = os.environ if environ is None else environ
    installation = installation or discover_channel(channel, environ=env_source)
    if installation is None:
        raise LaunchError(f"{SPECS[channel].label} não foi encontrado")
    config, resolved_config_path = load_channel_config(
        installation, config_path, environ=env_source
    )
    if config.executable is not None:
        installation = installation_for_executable(channel, config.executable)

    native_shim: Path | None = None
    if config.voice.enabled:
        try:
            native_shim = ensure_native_shim(installation, source=native_source)
        except NativeShimError as exc:
            raise LaunchError(str(exc)) from exc

    args = list(installation.command)
    if config.proxy.enabled:
        if not bridge_url:
            raise LaunchError("o proxy está configurado, mas o encaminhador local não foi iniciado")
        args.extend(
            (
                f"--proxy-server={bridge_url}",
                "--proxy-bypass-list=<local>",
                "--disable-quic",
            )
        )

    environment = _clean_environment(env_source)
    if config.proxy.password_env:
        secret_name = config.proxy.password_env.casefold()
        environment = {
            key: value for key, value in environment.items() if key.casefold() != secret_name
        }
    environment["DISCORD_LOCAL_PROXY_VOICE_ENABLED"] = "1" if config.voice.enabled else "0"
    environment["DISCORD_LOCAL_PROXY_VOICE_DELAY_MS"] = str(config.voice.delay_ms)
    if config.voice.packet_file is not None:
        environment["DISCORD_LOCAL_PROXY_VOICE_PACKET_FILE"] = str(
            config.voice.packet_file
        )
    if resolved_config_path is not None:
        environment["DISCORD_LOCAL_PROXY_CONFIG"] = str(resolved_config_path)
    if native_shim is not None and os.name != "nt" and installation.source != "squirrel":
        environment["LD_PRELOAD"] = str(native_shim)

    return LaunchPlan(
        installation=installation,
        config_path=resolved_config_path,
        config=config,
        command=tuple(args),
        environment=environment,
        native_shim=native_shim,
    )


def launch_discord(
    channel: str,
    *,
    config_path: Path | None = None,
    installation: DiscordInstallation | None = None,
    native_source: Path | None = None,
    require_stopped: bool = True,
    wait_for_exit: bool | None = None,
    environ: Mapping[str, str] | None = None,
) -> LaunchResult:
    env_source = os.environ if environ is None else environ
    installation = installation or discover_channel(channel, environ=env_source)
    if installation is None:
        raise LaunchError(f"{SPECS[channel].label} não foi encontrado")
    config, _ = load_channel_config(installation, config_path, environ=env_source)
    if config.executable is not None:
        installation = installation_for_executable(channel, config.executable)
    if require_stopped and discord_is_running(installation):
        raise LaunchError(
            f"{installation.label} já está aberto. Feche-o completamente, inclusive na bandeja, e tente novamente."
        )

    bridge: LocalProxyBridge | None = None
    try:
        bridge_url: str | None = None
        if config.proxy.enabled:
            runtime_proxy = _resolved_runtime_proxy(config.proxy, env_source)
            probe = probe_proxy(runtime_proxy)
            if not probe.ok:
                raise LaunchError(f"o proxy falhou; o Discord não foi iniciado: {probe.message}")
            bridge = LocalProxyBridge(runtime_proxy).start()
            bridge_url = bridge.proxy_url

        plan = prepare_launch(
            channel,
            config_path=config_path,
            installation=installation,
            bridge_url=bridge_url,
            native_source=native_source,
            environ=env_source,
        )
        creation_flags = 0
        popen_kwargs: dict[str, object] = {}
        if os.name == "nt":
            creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            popen_kwargs["creationflags"] = creation_flags
        else:
            popen_kwargs["start_new_session"] = True

        process = subprocess.Popen(
            list(plan.command),
            cwd=_working_directory(plan.installation),
            env=plan.environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            **popen_kwargs,
        )
        should_wait = config.proxy.enabled if wait_for_exit is None else wait_for_exit
        exit_code: int | None = None
        if should_wait:
            exit_code = _wait_for_process_tree(process, plan.installation)
        return LaunchResult(
            pid=process.pid,
            exit_code=exit_code,
            proxy_active=config.proxy.enabled,
            voice_active=config.voice.enabled,
        )
    except OSError as exc:
        raise LaunchError(f"não foi possível iniciar {installation.label}: {exc}") from exc
    finally:
        if bridge is not None:
            bridge.stop()


def build_runtime_flags(config: AppConfig, bridge_url: str | None) -> tuple[str, ...]:
    if not config.proxy.enabled:
        return ()
    if bridge_url is None:
        raise LaunchError("bridge_url é obrigatório quando o proxy está ativo")
    return (
        f"--proxy-server={bridge_url}",
        "--proxy-bypass-list=<local>",
        "--disable-quic",
    )


def discord_is_running(installation: DiscordInstallation) -> bool:
    expected = _expected_process_names(installation)
    if os.name == "nt" or installation.source == "squirrel":
        return _windows_process_running(expected)
    return _linux_process_running(expected, installation.executable)


def _windows_process_running(expected: set[str]) -> bool:
    try:
        result = subprocess.run(
            ["tasklist.exe", "/FO", "CSV", "/NH"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    for row in csv.reader(io.StringIO(result.stdout)):
        if row and row[0].lower() in expected:
            return True
    return False


def _linux_process_running(expected: set[str], executable: Path | None) -> bool:
    proc = Path("/proc")
    if not proc.is_dir():
        return False
    resolved_expected = executable.resolve(strict=False) if executable else None
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            linked = (entry / "exe").resolve(strict=True)
            if resolved_expected is not None and linked == resolved_expected:
                return True
            if linked.name.lower() in expected:
                return True
            command = (entry / "cmdline").read_bytes().split(b"\x00", 1)[0]
            if Path(os.fsdecode(command)).name.lower() in expected:
                return True
        except (OSError, ValueError):
            continue
    return False


def _wait_for_process_tree(
    process: subprocess.Popen[bytes], installation: DiscordInstallation
) -> int:
    try:
        while process.poll() is None:
            time.sleep(0.5)
        exit_code = int(process.returncode or 0)
        # Some package launchers exec a child and return immediately. Keep the
        # loopback proxy alive while a matching Discord process remains.
        grace_deadline = time.monotonic() + 10.0
        while time.monotonic() < grace_deadline:
            if discord_is_running(installation):
                break
            time.sleep(0.25)
        while discord_is_running(installation):
            time.sleep(1.0)
        return exit_code
    except KeyboardInterrupt:
        # Do not kill an unrelated/singleton process. The user explicitly
        # interrupted only the helper, so return and close its local proxy.
        return 130


def _expected_process_names(installation: DiscordInstallation) -> set[str]:
    spec = SPECS[installation.channel]
    return {
        spec.windows_executable.lower(),
        spec.linux_executable.lower(),
        *{name.lower() for name in spec.linux_commands},
    }


def _working_directory(installation: DiscordInstallation) -> str | None:
    if installation.executable:
        return str(installation.executable.parent)
    return None


def _clean_environment(source: Mapping[str, str]) -> dict[str, str]:
    blocked = {
        "ELECTRON_RUN_AS_NODE",
        "NODE_OPTIONS",
        "LD_PRELOAD",
        "DISCORD_LOCAL_PROXY_CONFIG",
        "DISCORD_LOCAL_PROXY_VOICE_ENABLED",
        "DISCORD_LOCAL_PROXY_VOICE_DELAY_MS",
        "DISCORD_LOCAL_PROXY_VOICE_PACKET_FILE",
    }
    blocked_names = {key.casefold() for key in blocked}
    return {key: value for key, value in source.items() if key.casefold() not in blocked_names}


def _resolved_runtime_proxy(
    settings: ProxySettings, environ: Mapping[str, str]
) -> ProxySettings:
    if not settings.password_env:
        return settings
    return replace(
        settings,
        password=settings.resolved_password(environ),
        password_env="",
    )
