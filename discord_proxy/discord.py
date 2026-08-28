"""Onde o Discord está instalado, em cada plataforma.

A busca é curta de propósito: os caminhos oficiais de cada canal, mais AppImage,
Flatpak e Snap no Linux. Nada de varrer o disco inteiro.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

CHANNELS = ("stable", "ptb", "canary")


@dataclass(frozen=True)
class Channel:
    key: str
    label: str
    windows_dir: str
    windows_exe: str
    linux_commands: tuple[str, ...]
    linux_home: str
    linux_exe: str
    flatpak_id: str
    snap_name: str
    macos_app: str


CHANNEL_SPECS: dict[str, Channel] = {
    "stable": Channel(
        key="stable",
        label="Discord",
        windows_dir="Discord",
        windows_exe="Discord.exe",
        linux_commands=("discord", "Discord"),
        linux_home="discord",
        linux_exe="Discord",
        flatpak_id="com.discordapp.Discord",
        snap_name="discord",
        macos_app="Discord.app",
    ),
    "ptb": Channel(
        key="ptb",
        label="Discord PTB",
        windows_dir="DiscordPTB",
        windows_exe="DiscordPTB.exe",
        linux_commands=("discord-ptb", "DiscordPTB"),
        linux_home="discordptb",
        linux_exe="DiscordPTB",
        flatpak_id="com.discordapp.DiscordPTB",
        snap_name="discord-ptb",
        macos_app="Discord PTB.app",
    ),
    "canary": Channel(
        key="canary",
        label="Discord Canary",
        windows_dir="DiscordCanary",
        windows_exe="DiscordCanary.exe",
        linux_commands=("discord-canary", "DiscordCanary"),
        linux_home="discordcanary",
        linux_exe="DiscordCanary",
        flatpak_id="com.discordapp.DiscordCanary",
        snap_name="discord-canary",
        macos_app="Discord Canary.app",
    ),
}


@dataclass(frozen=True)
class Install:
    """Uma instalação encontrada e pronta para receber o launcher."""

    channel: str
    label: str
    kind: str  # windows | linux | appimage | flatpak | snap | macos
    command: tuple[str, ...]
    executable: Path | None

    @property
    def supports_voice(self) -> bool:
        """Flatpak e Snap isolam bibliotecas externas; o macOS assina o app."""
        return self.kind in {"windows", "linux", "appimage"}

    @property
    def voice_reason(self) -> str:
        if self.supports_voice:
            return ""
        if self.kind == "macos":
            return (
                "no macOS o Discord é assinado e o sistema ignora bibliotecas "
                "injetadas; só o proxy TCP funciona"
            )
        return f"{self.kind} isola bibliotecas externas; só o proxy TCP funciona"

    @property
    def directory(self) -> Path | None:
        return self.executable.parent if self.executable else None


def detect(*, environ: Mapping[str, str] | None = None) -> list[Install]:
    env = os.environ if environ is None else environ
    found: list[Install] = []
    for key in CHANNELS:
        install = detect_channel(key, environ=env)
        if install is not None:
            found.append(install)
    return found


def detect_channel(channel: str, *, environ: Mapping[str, str] | None = None) -> Install | None:
    spec = CHANNEL_SPECS[_normalize(channel)]
    env = os.environ if environ is None else environ
    if os.name == "nt":
        return _detect_windows(spec, env)
    if sys.platform == "darwin":
        return _detect_macos(spec, env)
    return _detect_linux(spec, env)


def install_for_executable(channel: str, executable: Path) -> Install:
    """Uma instalação montada a partir de um caminho escolhido pelo usuário."""
    spec = CHANNEL_SPECS[_normalize(channel)]
    executable = Path(executable).expanduser().resolve(strict=True)
    if not _executable_file(executable):
        raise FileNotFoundError(f"{executable} não é um executável válido")
    if os.name == "nt":
        kind = "windows"
    elif sys.platform == "darwin":
        kind = "macos"
    elif executable.suffix.lower() == ".appimage":
        kind = "appimage"
    else:
        kind = "linux"
    return Install(
        channel=spec.key,
        label=spec.label,
        kind=kind,
        command=(str(executable),),
        executable=executable,
    )


def _detect_windows(spec: Channel, env: Mapping[str, str]) -> Install | None:
    local = env.get("LOCALAPPDATA")
    if not local:
        return None
    root = Path(local) / spec.windows_dir
    executable = _newest_squirrel_executable(root, spec.windows_exe)
    if executable is None:
        return None
    return Install(
        channel=spec.key,
        label=spec.label,
        kind="windows",
        command=(str(executable),),
        executable=executable,
    )


def _newest_squirrel_executable(root: Path, name: str) -> Path | None:
    """O Squirrel guarda cada versão em `app-1.2.3`; queremos a mais recente."""
    if not root.is_dir():
        return None
    best: tuple[tuple[int, ...], Path] | None = None
    try:
        entries = list(root.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.lower().startswith("app-") or not entry.is_dir():
            continue
        candidate = entry / name
        if not _executable_file(candidate):
            continue
        version = _version_key(entry.name[4:])
        if best is None or version > best[0]:
            best = (version, candidate)
    return best[1] if best else None


def _detect_linux(spec: Channel, env: Mapping[str, str]) -> Install | None:
    home = Path(env.get("HOME", str(Path.home())))

    # 1. Pacote oficial (.deb/.tar.gz) — o binário real vive em ~/.config.
    for command in spec.linux_commands:
        resolved = shutil.which(command, path=env.get("PATH"))
        if not resolved:
            continue
        real = _resolve_wrapper(Path(resolved), home, spec)
        return Install(
            channel=spec.key,
            label=spec.label,
            kind="linux",
            command=(str(real),),
            executable=real,
        )

    # 2. AppImage em ~/Applications ou ~/Aplicativos.
    appimage = _find_appimage(home, spec)
    if appimage is not None:
        return Install(
            channel=spec.key,
            label=spec.label,
            kind="appimage",
            command=(str(appimage),),
            executable=appimage,
        )

    # 3. Flatpak e Snap — funcionam, mas sem o ajuste de voz.
    if _flatpak_installed(spec.flatpak_id, env):
        return Install(
            channel=spec.key,
            label=spec.label,
            kind="flatpak",
            command=("flatpak", "run", spec.flatpak_id),
            executable=None,
        )
    if _snap_installed(spec.snap_name, env):
        return Install(
            channel=spec.key,
            label=spec.label,
            kind="snap",
            command=(f"/snap/bin/{spec.snap_name}",),
            executable=None,
        )
    return None


def _detect_macos(spec: Channel, env: Mapping[str, str]) -> Install | None:
    home = Path(env.get("HOME", str(Path.home())))
    for base in (Path("/Applications"), home / "Applications"):
        bundle = base / spec.macos_app
        binary = bundle / "Contents" / "MacOS" / spec.macos_app.removesuffix(".app")
        if _executable_file(binary):
            return Install(
                channel=spec.key,
                label=spec.label,
                kind="macos",
                command=(str(binary),),
                executable=binary,
            )
    return None


def _resolve_wrapper(command: Path, home: Path, spec: Channel) -> Path:
    """`/usr/bin/discord` costuma ser um script que chama ~/.config/discord/Discord."""
    for candidate in (
        home / ".config" / spec.linux_home / spec.linux_exe,
        Path("/usr/share") / spec.linux_home / spec.linux_exe,
        Path("/opt") / spec.linux_home / spec.linux_exe,
    ):
        if _executable_file(candidate):
            return candidate
    return command


def _find_appimage(home: Path, spec: Channel) -> Path | None:
    prefix = spec.linux_exe.lower()
    for directory in (home / "Applications", home / "Aplicativos", home / "Apps"):
        if not directory.is_dir():
            continue
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            name = entry.name.lower()
            if name.endswith(".appimage") and name.startswith(prefix) and _executable_file(entry):
                return entry
    return None


def _flatpak_installed(app_id: str, env: Mapping[str, str]) -> bool:
    if shutil.which("flatpak", path=env.get("PATH")) is None:
        return False
    home = Path(env.get("HOME", str(Path.home())))
    roots = (
        home / ".local" / "share" / "flatpak" / "app" / app_id,
        Path("/var/lib/flatpak/app") / app_id,
    )
    return any(root.is_dir() for root in roots)


def _snap_installed(name: str, env: Mapping[str, str]) -> bool:
    return Path(f"/snap/{name}/current").exists() and _executable_file(Path(f"/snap/bin/{name}"))


def _executable_file(path: Path) -> bool:
    try:
        info = path.stat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and bool(info.st_mode & 0o111 or os.name == "nt")


def _version_key(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in text.split("."):
        digits = "".join(char for char in chunk if char.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _normalize(channel: str) -> str:
    key = (channel or "stable").strip().lower()
    if key not in CHANNEL_SPECS:
        raise ValueError(f"canal desconhecido: {channel} (use {', '.join(CHANNELS)})")
    return key


def running_processes(install: Install) -> bool:
    """O Discord já está aberto? Uma instância viva ignora os novos argumentos."""
    names = {
        CHANNEL_SPECS[install.channel].windows_exe.lower(),
        CHANNEL_SPECS[install.channel].linux_exe.lower(),
        *(name.lower() for name in CHANNEL_SPECS[install.channel].linux_commands),
    }
    if os.name == "nt":
        return _windows_running(names)
    return _proc_running(names, install.executable)


def _windows_running(names: Iterable[str]) -> bool:
    import csv
    import io
    import subprocess

    wanted = {name.lower() for name in names}
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
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return any(row and row[0].lower() in wanted for row in csv.reader(io.StringIO(result.stdout)))


def _proc_running(names: Iterable[str], executable: Path | None) -> bool:
    proc = Path("/proc")
    if not proc.is_dir():
        return False
    wanted = {name.lower() for name in names}
    target = executable.resolve(strict=False) if executable else None
    own = os.getpid()
    try:
        entries = list(proc.iterdir())
    except OSError:
        return False
    for entry in entries:
        if not entry.name.isdigit() or int(entry.name) == own:
            continue
        try:
            linked = (entry / "exe").resolve(strict=True)
        except OSError:
            continue
        if target is not None and linked == target:
            return True
        if linked.name.lower() in wanted:
            return True
    return False
