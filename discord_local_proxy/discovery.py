from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .config import CONFIG_FILENAME, ConfigError


CHANNELS = ("stable", "ptb", "canary")


@dataclass(frozen=True)
class ChannelSpec:
    key: str
    label: str
    windows_root: str
    windows_executable: str
    linux_commands: tuple[str, ...]
    linux_config_dir: str
    linux_executable: str
    flatpak_id: str
    icon: str


SPECS: dict[str, ChannelSpec] = {
    "stable": ChannelSpec(
        key="stable",
        label="Discord",
        windows_root="Discord",
        windows_executable="Discord.exe",
        linux_commands=("discord",),
        linux_config_dir="discord",
        linux_executable="Discord",
        flatpak_id="com.discordapp.Discord",
        icon="discord",
    ),
    "ptb": ChannelSpec(
        key="ptb",
        label="Discord PTB",
        windows_root="DiscordPTB",
        windows_executable="DiscordPTB.exe",
        linux_commands=("discord-ptb", "discordptb"),
        linux_config_dir="discordptb",
        linux_executable="DiscordPTB",
        flatpak_id="com.discordapp.DiscordPTB",
        icon="discord-ptb",
    ),
    "canary": ChannelSpec(
        key="canary",
        label="Discord Canary",
        windows_root="DiscordCanary",
        windows_executable="DiscordCanary.exe",
        linux_commands=("discord-canary", "discordcanary"),
        linux_config_dir="discordcanary",
        linux_executable="DiscordCanary",
        flatpak_id="com.discordapp.DiscordCanary",
        icon="discord-canary",
    ),
}


@dataclass(frozen=True)
class DiscordInstallation:
    channel: str
    label: str
    command: tuple[str, ...]
    executable: Path | None
    root: Path | None
    source: str
    icon: str
    supports_udp_shim: bool = True

    @property
    def display_path(self) -> str:
        return str(self.executable or " ".join(self.command))


def discover_installations(
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> list[DiscordInstallation]:
    platform = platform or ("windows" if os.name == "nt" else "linux")
    env = dict(os.environ if environ is None else environ)
    home = Path.home() if home is None else Path(home)
    if platform == "windows":
        return _discover_windows(env)
    if platform == "linux":
        return _discover_linux(env, home)
    raise ConfigError(f"plataforma não suportada: {platform}")


def discover_channel(
    channel: str,
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> DiscordInstallation | None:
    _require_channel(channel)
    return next(
        (
            item
            for item in discover_installations(platform=platform, environ=environ, home=home)
            if item.channel == channel
        ),
        None,
    )


def installation_for_executable(channel: str, executable: Path) -> DiscordInstallation:
    spec = _require_channel(channel)
    path = _validate_executable(Path(executable), expected_names={spec.windows_executable, spec.linux_executable})
    return DiscordInstallation(
        channel=channel,
        label=spec.label,
        command=(str(path),),
        executable=path,
        root=path.parent,
        source="custom",
        icon=spec.icon,
        supports_udp_shim=True,
    )


def default_config_path(installation: DiscordInstallation) -> Path:
    if (os.name == "nt" or installation.source == "squirrel") and installation.root:
        # Root is the stable Squirrel channel folder, beside Update.exe.
        return installation.root / CONFIG_FILENAME
    if installation.root and _is_user_writable_location(installation.root):
        return installation.root / CONFIG_FILENAME
    if installation.executable:
        parent = installation.executable.parent
        if _is_user_writable_location(parent):
            return parent / CONFIG_FILENAME
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / SPECS[installation.channel].linux_config_dir / CONFIG_FILENAME


def app_data_root(*, environ: Mapping[str, str] | None = None, home: Path | None = None) -> Path:
    env = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    if os.name == "nt":
        base = Path(env.get("LOCALAPPDATA", home / "AppData" / "Local"))
    else:
        base = Path(env.get("XDG_DATA_HOME", home / ".local" / "share"))
    return base / "discord-local-proxy"


def resolve_adjacent_config(installation: DiscordInstallation) -> Path | None:
    candidates: list[Path] = []
    if installation.executable:
        candidates.append(installation.executable.parent / CONFIG_FILENAME)
    if installation.root:
        candidates.append(installation.root / CONFIG_FILENAME)
    candidates.append(default_config_path(installation))
    # Preserve compatibility with early launcher builds that stored system
    # package configurations under XDG_DATA_HOME.
    candidates.append(app_data_root() / "configs" / f"{installation.channel}.ini")
    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.expanduser().absolute()
        if candidate not in seen and candidate.is_file():
            return candidate
        seen.add(candidate)
    return None


def _discover_windows(env: Mapping[str, str]) -> list[DiscordInstallation]:
    local = env.get("LOCALAPPDATA")
    if not local:
        return []
    results: list[DiscordInstallation] = []
    for spec in SPECS.values():
        root = Path(local) / spec.windows_root
        executable = _newest_windows_executable(root, spec.windows_executable)
        if executable is None:
            continue
        results.append(
            DiscordInstallation(
                channel=spec.key,
                label=spec.label,
                command=(str(executable),),
                executable=executable,
                root=root,
                source="squirrel",
                icon=str(executable),
                supports_udp_shim=True,
            )
        )
    return results


def _newest_windows_executable(root: Path, executable_name: str) -> Path | None:
    if not root.is_dir():
        return None
    candidates: list[tuple[tuple[int, ...], float, Path]] = []
    for child in root.iterdir():
        match = re.fullmatch(r"app-(\d+(?:\.\d+)*)", child.name, flags=re.IGNORECASE)
        if not match or not child.is_dir():
            continue
        executable = child / executable_name
        if not _is_regular_executable(executable):
            continue
        version = tuple(int(part) for part in match.group(1).split("."))
        try:
            modified = executable.stat().st_mtime
        except OSError:
            modified = 0.0
        candidates.append((version, modified, executable.resolve(strict=True)))
    return max(candidates, default=((), 0.0, None), key=lambda item: (item[0], item[1]))[2]


def _discover_linux(env: Mapping[str, str], home: Path) -> list[DiscordInstallation]:
    config_home = Path(env.get("XDG_CONFIG_HOME", home / ".config"))
    results: list[DiscordInstallation] = []
    flatpak = shutil.which("flatpak", path=env.get("PATH"))
    snap = shutil.which("snap", path=env.get("PATH"))

    for spec in SPECS.values():
        user_executable = config_home / spec.linux_config_dir / spec.linux_executable
        if _is_regular_executable(user_executable):
            resolved = user_executable.resolve(strict=True)
            channel_root = user_executable.parent.resolve(strict=True)
            results.append(
                DiscordInstallation(
                    channel=spec.key,
                    label=spec.label,
                    command=(str(resolved),),
                    executable=resolved,
                    root=channel_root,
                    source="discord-user-update",
                    icon=spec.icon,
                    supports_udp_shim=True,
                )
            )
            continue

        command = _first_which(spec.linux_commands, env.get("PATH"))
        if command:
            resolved = Path(command).resolve(strict=True)
            results.append(
                DiscordInstallation(
                    channel=spec.key,
                    label=spec.label,
                    command=(command,),
                    executable=resolved,
                    root=resolved.parent,
                    source="native-package",
                    icon=spec.icon,
                    supports_udp_shim=True,
                )
            )
            continue

        app_image = _find_app_image(home, spec)
        if app_image:
            results.append(
                DiscordInstallation(
                    channel=spec.key,
                    label=spec.label,
                    command=(str(app_image),),
                    executable=app_image,
                    root=app_image.parent,
                    source="appimage",
                    icon=str(app_image),
                    supports_udp_shim=True,
                )
            )
            continue

        if flatpak and _flatpak_is_installed(flatpak, spec.flatpak_id, env):
            results.append(
                DiscordInstallation(
                    channel=spec.key,
                    label=spec.label,
                    command=(flatpak, "run", spec.flatpak_id),
                    executable=None,
                    root=None,
                    source="flatpak",
                    icon=spec.flatpak_id,
                    supports_udp_shim=False,
                )
            )
            continue

        if snap and spec.key == "stable" and _snap_is_installed(snap, "discord", env):
            results.append(
                DiscordInstallation(
                    channel=spec.key,
                    label=spec.label,
                    command=(snap, "run", "discord"),
                    executable=None,
                    root=None,
                    source="snap",
                    icon="discord",
                    supports_udp_shim=False,
                )
            )
    return results


def _find_app_image(home: Path, spec: ChannelSpec) -> Path | None:
    directory = home / "Applications"
    if not directory.is_dir():
        return None
    tokens = {spec.label.replace(" ", "").lower(), spec.windows_root.lower()}
    candidates: list[Path] = []
    for path in directory.iterdir():
        normalized = path.stem.replace("-", "").replace("_", "").lower()
        if path.suffix.lower() == ".appimage" and any(token in normalized for token in tokens):
            if _is_regular_executable(path):
                candidates.append(path.resolve(strict=True))
    return max(candidates, default=None, key=lambda item: item.stat().st_mtime)


def _flatpak_is_installed(binary: str, app_id: str, env: Mapping[str, str]) -> bool:
    try:
        result = subprocess.run(
            [binary, "info", "--show-ref", app_id],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=dict(env),
            timeout=3,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _snap_is_installed(binary: str, package: str, env: Mapping[str, str]) -> bool:
    try:
        result = subprocess.run(
            [binary, "list", package],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=dict(env),
            timeout=3,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _first_which(names: Sequence[str], path: str | None) -> str | None:
    for name in names:
        result = shutil.which(name, path=path)
        if result:
            return result
    return None


def _validate_executable(path: Path, *, expected_names: Iterable[str]) -> Path:
    if path.name.lower() not in {name.lower() for name in expected_names}:
        raise ConfigError(
            f"executável inesperado: {path.name}; esperado: {', '.join(sorted(expected_names))}"
        )
    if not _is_regular_executable(path):
        raise ConfigError(f"executável do Discord não encontrado ou inválido: {path}")
    return path.resolve(strict=True)


def _is_regular_executable(path: Path) -> bool:
    try:
        info = path.stat()
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode):
        return False
    return os.name == "nt" or os.access(path, os.X_OK)


def _is_user_writable_location(path: Path) -> bool:
    try:
        resolved = path.resolve(strict=True)
        home = Path.home().resolve(strict=True)
        resolved.relative_to(home)
        return os.access(resolved, os.W_OK)
    except (OSError, ValueError):
        return False


def _require_channel(channel: str) -> ChannelSpec:
    try:
        return SPECS[channel]
    except KeyError as exc:
        raise ConfigError(f"canal desconhecido: {channel}") from exc
