from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

from . import __version__
from .config import AppConfig, CONFIG_FILENAME, ConfigError, save_config
from .discovery import (
    CHANNELS,
    SPECS,
    DiscordInstallation,
    app_data_root,
    default_config_path,
    discover_installations,
)
from .native import (
    NativeShimError,
    ensure_native_shim,
    remove_native_shim,
    remove_shared_linux_shim,
    remove_shared_windows_shim,
)


MANIFEST_NAME = "install-manifest.json"
LINUX_DESKTOP_NAMES = {
    channel: f"discord-local-proxy-{channel}.desktop" for channel in CHANNELS
}
WINDOWS_LINK_NAMES = {
    "stable": "Discord (Proxy).lnk",
    "ptb": "Discord PTB (Proxy).lnk",
    "canary": "Discord Canary (Proxy).lnk",
}


class InstallError(RuntimeError):
    """Installation could not be completed safely."""


@dataclass(frozen=True)
class ChannelInstallResult:
    channel: str
    config_path: Path
    shortcut_paths: tuple[Path, ...]
    native_shim: Path | None


@dataclass(frozen=True)
class InstallResult:
    runtime_command: tuple[str, ...]
    channels: tuple[ChannelInstallResult, ...]


@dataclass(frozen=True)
class UninstallResult:
    removed: tuple[Path, ...]
    preserved_configs: tuple[Path, ...]
    warnings: tuple[str, ...]


def install(
    channels: Iterable[str],
    config: AppConfig,
    *,
    installations: Sequence[DiscordInstallation] | None = None,
    native_source: Path | None = None,
) -> InstallResult:
    selected = tuple(dict.fromkeys(channels))
    if not selected:
        raise InstallError("selecione ao menos um canal do Discord")
    unknown = set(selected) - set(CHANNELS)
    if unknown:
        raise InstallError(f"canal desconhecido: {sorted(unknown)[0]}")

    discovered = {
        item.channel: item
        for item in (installations if installations is not None else discover_installations())
    }
    missing = [SPECS[channel].label for channel in selected if channel not in discovered]
    if missing:
        raise InstallError(f"instalação não encontrada: {', '.join(missing)}")

    root = app_data_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with _install_lock(root):
        snapshots: list[tuple[Path, bytes | None, int]] = []
        results: list[ChannelInstallResult] = []
        owned_shortcuts = _manifest_shortcut_paths(root)
        try:
            runtime_path = _runtime_mutation_path(root)
            if runtime_path is not None:
                snapshots.append(_snapshot_file(runtime_path, max_bytes=512 * 1024 * 1024))
            snapshots.append(_snapshot_file(root / MANIFEST_NAME))
            runtime = _install_runtime(root)
            for channel in selected:
                installation = discovered[channel]
                config_path = default_config_path(installation)
                snapshots.append(_snapshot_file(config_path))
                for shortcut in _shortcut_destinations(installation):
                    if _path_lexists(shortcut) and shortcut not in owned_shortcuts:
                        raise InstallError(
                            f"atalho já existe e não pertence a esta ferramenta: {shortcut}"
                        )
                    snapshots.append(_snapshot_file(shortcut, max_bytes=8 * 1024 * 1024))
                if config.voice.enabled:
                    for native_path in _native_owned_paths(installation):
                        snapshots.append(
                            _snapshot_file(native_path, max_bytes=64 * 1024 * 1024)
                        )
                save_config(config_path, config)

                shim: Path | None = None
                if config.voice.enabled:
                    shim = ensure_native_shim(installation, source=native_source)

                shortcuts = _create_shortcuts(
                    installation,
                    runtime,
                    config_path,
                )
                results.append(
                    ChannelInstallResult(
                        channel=channel,
                        config_path=config_path,
                        shortcut_paths=tuple(shortcuts),
                        native_shim=shim,
                    )
                )

            _write_manifest(root, runtime, results)
            return InstallResult(runtime_command=runtime, channels=tuple(results))
        except BaseException:
            for snapshot in reversed(snapshots):
                _restore_snapshot(snapshot)
            raise


def uninstall(*, purge_config: bool = False) -> UninstallResult:
    root = app_data_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    removed: list[Path] = []
    preserved: list[Path] = []
    warnings: list[str] = []
    with _install_lock(root):
        owned_shortcuts = _manifest_shortcut_paths(root)
        installations = discover_installations()
        if os.name == "nt":
            known = {(item.channel, item.executable) for item in installations}
            for item in _windows_version_installations():
                if (item.channel, item.executable) not in known:
                    installations.append(item)
        by_channel = {item.channel: item for item in installations}
        for shortcut in owned_shortcuts:
            if _unlink_regular(shortcut):
                removed.append(shortcut)

        for installation in installations:
            try:
                if remove_native_shim(installation):
                    if installation.executable:
                        removed.append(installation.executable.parent / "version.dll")
            except NativeShimError as exc:
                warnings.append(str(exc))

        if os.name != "nt":
            try:
                if remove_shared_linux_shim():
                    removed.append(root / "native" / "libdiscord_udp_shim.so")
            except NativeShimError as exc:
                warnings.append(str(exc))
        else:
            try:
                if remove_shared_windows_shim():
                    removed.append(root / "native" / "version.dll")
            except NativeShimError as exc:
                warnings.append(str(exc))

        for channel in CHANNELS:
            paths = {root / "configs" / f"{channel}.ini"}
            installation = by_channel.get(channel)
            if installation:
                paths.add(default_config_path(installation))
            paths.update(_known_channel_config_paths(channel))
            for path in paths:
                if not path.exists():
                    continue
                if purge_config:
                    if _unlink_regular(path):
                        removed.append(path)
                else:
                    preserved.append(path)

        manifest = root / MANIFEST_NAME
        if _unlink_regular(manifest):
            removed.append(manifest)

        for runtime in (
            root / "discord-local-proxy.pyz",
            root / "discord-local-proxy.exe",
            root / "discord-local-proxy",
        ):
            if not runtime.exists():
                continue
            if _is_current_executable(runtime) and os.name == "nt":
                if _schedule_windows_delete(runtime):
                    warnings.append(f"{runtime} será removido no próximo reinício")
                else:
                    warnings.append(f"não foi possível remover o executável em uso: {runtime}")
            elif _unlink_regular(runtime):
                removed.append(runtime)

    _remove_empty_owned_directories(root)
    return UninstallResult(tuple(removed), tuple(sorted(set(preserved))), tuple(warnings))


def status() -> dict[str, object]:
    root = app_data_root()
    manifest = root / MANIFEST_NAME
    data: dict[str, object] = {
        "installed": False,
        "manifest": str(manifest),
        "channels": [],
    }
    parsed = _load_manifest(manifest)
    if parsed is not None:
        data["installed"] = True
        data["channels"] = [
            item.get("channel")
            for item in parsed.get("channels", [])
            if isinstance(item, dict) and item.get("channel") in CHANNELS
        ]
    return data


def _install_runtime(root: Path) -> tuple[str, ...]:
    if getattr(sys, "frozen", False):
        suffix = ".exe" if os.name == "nt" else ""
        destination = root / f"discord-local-proxy{suffix}"
        source = Path(sys.executable).resolve(strict=True)
        if source != destination.resolve(strict=False):
            _atomic_copy(source, destination, mode=0o700)
        return (str(destination),)

    destination = root / "discord-local-proxy.pyz"
    _build_zipapp(destination)
    return (sys.executable, str(destination))


def _runtime_mutation_path(root: Path) -> Path | None:
    if getattr(sys, "frozen", False):
        suffix = ".exe" if os.name == "nt" else ""
        destination = root / f"discord-local-proxy{suffix}"
        try:
            if Path(sys.executable).resolve(strict=True) == destination.resolve(strict=True):
                return None
        except OSError:
            pass
        return destination
    return root / "discord-local-proxy.pyz"


def _build_zipapp(destination: Path) -> None:
    package = Path(__file__).resolve().parent
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(fd)
    temp = Path(temp_name)
    try:
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "__main__.py",
                "from discord_local_proxy.cli import main\nraise SystemExit(main())\n",
            )
            for source in sorted(package.glob("*.py")):
                archive.write(source, f"discord_local_proxy/{source.name}")
        os.chmod(temp, 0o700)
        os.replace(temp, destination)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def _create_shortcuts(
    installation: DiscordInstallation,
    runtime: tuple[str, ...],
    config_path: Path,
) -> list[Path]:
    arguments = (*runtime[1:], "launch", "--channel", installation.channel, "--config", str(config_path))
    target = runtime[0]
    if os.name == "nt" or installation.source == "squirrel":
        return _create_windows_shortcuts(installation, target, arguments)
    return [_create_linux_desktop(installation, target, arguments)]


def _shortcut_destinations(installation: DiscordInstallation) -> tuple[Path, ...]:
    if os.name == "nt" or installation.source == "squirrel":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise InstallError("APPDATA não está definido")
        start_menu = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        destinations = [start_menu / WINDOWS_LINK_NAMES[installation.channel]]
        desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
        if desktop.is_dir():
            destinations.append(desktop / WINDOWS_LINK_NAMES[installation.channel])
        return tuple(destinations)
    applications = Path(
        os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    ) / "applications"
    return (applications / LINUX_DESKTOP_NAMES[installation.channel],)


def _native_owned_paths(installation: DiscordInstallation) -> tuple[Path, ...]:
    shared = app_data_root() / "native"
    if os.name == "nt":
        if installation.executable is None:
            raise InstallError("instalação Windows sem executável")
        return (
            shared / "version.dll",
            installation.executable.parent / "version.dll",
            installation.executable.parent / "version.dll.discord-local-proxy.sha256",
        )
    if installation.source == "squirrel":
        if installation.executable is None:
            raise InstallError("instalação Windows sem executável")
        return (
            installation.executable.parent / "version.dll",
            installation.executable.parent / "version.dll.discord-local-proxy.sha256",
        )
    return (shared / "libdiscord_udp_shim.so",)


def _create_linux_desktop(
    installation: DiscordInstallation,
    target: str,
    arguments: Sequence[str],
) -> Path:
    applications = Path(
        os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    ) / "applications"
    applications.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = applications / LINUX_DESKTOP_NAMES[installation.channel]
    _require_regular_or_absent(path)
    exec_line = " ".join(_desktop_quote(item) for item in (target, *arguments))
    payload = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={installation.label} (Proxy)\n"
        f"Comment=Inicia {installation.label} com proxy local e compatibilidade de voz\n"
        f"Exec={exec_line}\n"
        f"Icon={installation.icon}\n"
        "Terminal=false\n"
        "Categories=Network;InstantMessaging;\n"
        "StartupNotify=true\n"
        f"StartupWMClass={SPECS[installation.channel].linux_executable}\n"
    ).encode("utf-8")
    _atomic_write(path, payload, mode=0o700)
    return path


def _create_windows_shortcuts(
    installation: DiscordInstallation,
    target: str,
    arguments: Sequence[str],
) -> list[Path]:
    destinations = list(_shortcut_destinations(installation))
    for destination in destinations:
        destination.parent.mkdir(parents=True, exist_ok=True)
        _powershell_create_shortcut(
            destination,
            target,
            subprocess.list2cmdline(list(arguments)),
            str(Path(target).parent),
            installation.icon,
        )
    return destinations


def _powershell_create_shortcut(
    link: Path,
    target: str,
    arguments: str,
    working_directory: str,
    icon: str,
) -> None:
    script = (
        "$ErrorActionPreference='Stop';"
        "$w=New-Object -ComObject WScript.Shell;"
        "$s=$w.CreateShortcut($env:DLP_LINK);"
        "$s.TargetPath=$env:DLP_TARGET;"
        "$s.Arguments=$env:DLP_ARGUMENTS;"
        "$s.WorkingDirectory=$env:DLP_WORKING;"
        "if($env:DLP_ICON){$s.IconLocation=$env:DLP_ICON};"
        "$s.Save()"
    )
    env = dict(os.environ)
    env.update(
        {
            "DLP_LINK": str(link),
            "DLP_TARGET": target,
            "DLP_ARGUMENTS": arguments,
            "DLP_WORKING": working_directory,
            "DLP_ICON": icon,
        }
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=20,
        check=False,
        shell=False,
    )
    if result.returncode != 0 or not link.is_file():
        message = result.stderr.strip().replace("\r", " ").replace("\n", " ")[:512]
        raise InstallError(f"não foi possível criar {link.name}: {message or 'PowerShell falhou'}")


def _all_shortcut_paths() -> Iterator[Path]:
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            start_menu = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            for name in WINDOWS_LINK_NAMES.values():
                yield start_menu / name
        desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
        for name in WINDOWS_LINK_NAMES.values():
            yield desktop / name
    else:
        applications = Path(
            os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
        ) / "applications"
        for name in LINUX_DESKTOP_NAMES.values():
            yield applications / name


def _manifest_shortcut_paths(root: Path) -> set[Path]:
    parsed = _load_manifest(root / MANIFEST_NAME)
    if parsed is None:
        return set()
    expected = set(_all_shortcut_paths())
    owned: set[Path] = set()
    channels = parsed.get("channels", [])
    if not isinstance(channels, list):
        return set()
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        shortcuts = channel.get("shortcuts", [])
        if not isinstance(shortcuts, list):
            continue
        for value in shortcuts:
            if isinstance(value, str) and Path(value) in expected:
                owned.add(Path(value))
    return owned


def _load_manifest(path: Path) -> dict[str, object] | None:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024:
            return None
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict) or parsed.get("format") != 1:
            return None
        return parsed
    except (OSError, ValueError, TypeError):
        return None


def _known_channel_config_paths(channel: str) -> set[Path]:
    spec = SPECS[channel]
    paths: set[Path] = set()
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            paths.add(Path(local) / spec.windows_root / CONFIG_FILENAME)
    else:
        config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        paths.add(config_home / spec.linux_config_dir / CONFIG_FILENAME)
    return paths


def _windows_version_installations() -> list[DiscordInstallation]:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return []
    installations: list[DiscordInstallation] = []
    for spec in SPECS.values():
        root = Path(local) / spec.windows_root
        try:
            children = tuple(root.iterdir())
        except OSError:
            continue
        for child in children:
            if (
                child.is_symlink()
                or not child.is_dir()
                or re.fullmatch(r"app-\d+(?:\.\d+)*", child.name, re.IGNORECASE) is None
            ):
                continue
            executable = child / spec.windows_executable
            executable_is_valid = executable.is_file() and not executable.is_symlink()
            managed_artifact_exists = _path_lexists(child / "version.dll") or _path_lexists(
                child / "version.dll.discord-local-proxy.sha256"
            )
            if not executable_is_valid and not managed_artifact_exists:
                continue
            installations.append(
                DiscordInstallation(
                    channel=spec.key,
                    label=spec.label,
                    command=(str(executable),),
                    executable=(
                        executable.resolve(strict=True)
                        if executable_is_valid
                        else executable.absolute()
                    ),
                    root=root,
                    source="squirrel",
                    icon=str(executable),
                    supports_udp_shim=True,
                )
            )
    return installations


def _write_manifest(
    root: Path,
    runtime: tuple[str, ...],
    channels: Sequence[ChannelInstallResult],
) -> None:
    expected_shortcuts = set(_all_shortcut_paths())
    entries: dict[str, dict[str, object]] = {}
    previous = _load_manifest(root / MANIFEST_NAME)
    if previous is not None and isinstance(previous.get("channels"), list):
        for item in previous["channels"]:
            if not isinstance(item, dict) or item.get("channel") not in CHANNELS:
                continue
            channel = str(item["channel"])
            shortcuts = item.get("shortcuts", [])
            safe_shortcuts = [
                value
                for value in shortcuts
                if isinstance(value, str) and Path(value) in expected_shortcuts
            ] if isinstance(shortcuts, list) else []
            config_path = item.get("config")
            entries[channel] = {
                "channel": channel,
                "config": config_path if isinstance(config_path, str) else "",
                "shortcuts": safe_shortcuts,
            }
    for item in channels:
        entries[item.channel] = {
            "channel": item.channel,
            "config": str(item.config_path),
            "shortcuts": [str(path) for path in item.shortcut_paths],
        }
    payload = json.dumps(
        {
            "format": 1,
            "version": __version__,
            "runtime": list(runtime),
            "channels": [entries[channel] for channel in CHANNELS if channel in entries],
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    _atomic_write(root / MANIFEST_NAME, payload, mode=0o600)


@contextmanager
def _install_lock(root: Path) -> Iterator[None]:
    path = root / ".install.lock"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise InstallError("outra instalação/desinstalação já está em andamento") from exc
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
        yield
    finally:
        os.close(fd)
        try:
            path.unlink()
        except OSError:
            pass


def _desktop_quote(value: str) -> str:
    if not value or any(char in value for char in "\r\n\x00"):
        raise InstallError("argumento inválido para atalho")
    escaped = (
        value.replace("%", "%%")
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("`", "\\`")
        .replace("$", "\\$")
    )
    return f'"{escaped}"'


def _snapshot_file(
    path: Path, *, max_bytes: int = 64 * 1024
) -> tuple[Path, bytes | None, int]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return path, None, 0o600
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
        raise InstallError(f"não é seguro alterar {path}")
    return path, path.read_bytes(), stat.S_IMODE(info.st_mode)


def _restore_snapshot(snapshot: tuple[Path, bytes | None, int]) -> None:
    path, payload, mode = snapshot
    if payload is None:
        _unlink_regular(path)
    else:
        _atomic_write(path, payload, mode=mode)


def _atomic_copy(source: Path, destination: Path, *, mode: int) -> None:
    if source.is_symlink() or not source.is_file():
        raise InstallError(f"origem inválida: {source}")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _require_regular_or_absent(destination)
    fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    temp = Path(temp_name)
    try:
        with source.open("rb") as reader, os.fdopen(fd, "wb", closefd=True) as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        os.chmod(temp, mode)
        os.replace(temp, destination)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        temp.unlink(missing_ok=True)
        raise


def _atomic_write(path: Path, payload: bytes, *, mode: int) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _require_regular_or_absent(path)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        temp.unlink(missing_ok=True)
        raise


def _unlink_regular(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return False
    path.unlink()
    return True


def _path_lexists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def _require_regular_or_absent(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise InstallError(f"destino inseguro: {path}")


def _is_current_executable(path: Path) -> bool:
    if not getattr(sys, "frozen", False):
        return False
    try:
        return Path(sys.executable).resolve(strict=True) == path.resolve(strict=True)
    except OSError:
        return False


def _schedule_windows_delete(path: Path) -> bool:
    try:
        move_file_ex = ctypes.windll.kernel32.MoveFileExW
        move_file_ex.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        move_file_ex.restype = ctypes.c_int
        return bool(move_file_ex(str(path), None, 0x00000004))
    except (AttributeError, OSError):
        return False


def _remove_empty_owned_directories(root: Path) -> None:
    for child in (root / "configs", root / "native"):
        try:
            child.rmdir()
        except OSError:
            pass
    try:
        root.rmdir()
    except OSError:
        pass
