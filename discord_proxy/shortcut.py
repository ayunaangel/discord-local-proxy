"""Atalhos "Discord (Proxy)" — opcionais, por usuário e fáceis de remover.

Nada aqui é obrigatório para usar a ferramenta: `discord-proxy run` já abre o
Discord. O atalho existe só para quem prefere clicar num ícone.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .discord import CHANNEL_SPECS, Install

MARKER = "# discord-proxy\n"


@dataclass(frozen=True)
class Shortcut:
    path: Path
    created: bool


def launcher_command(channel: str) -> list[str]:
    """O comando que o atalho vai executar."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "run", "--channel", channel]
    return [sys.executable, "-m", "discord_proxy", "run", "--channel", channel]


def create(install: Install) -> Shortcut:
    if os.name == "nt":
        return _create_windows(install)
    if sys.platform == "darwin":
        return _create_macos(install)
    return _create_linux(install)


def remove(install: Install) -> bool:
    removed = False
    for path in _paths(install):
        if path.is_file() and _is_ours(path):
            path.unlink()
            removed = True
    return removed


def _paths(install: Install) -> list[Path]:
    name = _basename(install)
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return [
            base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{name}.lnk",
            Path.home() / "Desktop" / f"{name}.lnk",
        ]
    if sys.platform == "darwin":
        return [Path.home() / "Applications" / f"{name}.command"]
    data = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return [Path(data) / "applications" / f"discord-proxy-{install.channel}.desktop"]


def _basename(install: Install) -> str:
    label = CHANNEL_SPECS[install.channel].label
    return f"{label} (Proxy)"


def _create_linux(install: Install) -> Shortcut:
    path = _paths(install)[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    command = " ".join(shlex.quote(part) for part in launcher_command(install.channel))
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={_basename(install)}\n"
        "Comment=Abre o Discord com proxy e ajuste de voz\n"
        f"Exec={command}\n"
        "Icon=discord\n"
        "Terminal=false\n"
        "Categories=Network;InstantMessaging;\n"
        f"{MARKER}"
    )
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return Shortcut(path=path, created=True)


def _create_macos(install: Install) -> Shortcut:
    path = _paths(install)[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    command = " ".join(shlex.quote(part) for part in launcher_command(install.channel))
    path.write_text(f"#!/bin/sh\n{MARKER}exec {command}\n", encoding="utf-8")
    path.chmod(0o755)
    return Shortcut(path=path, created=True)


def _create_windows(install: Install) -> Shortcut:
    path = _paths(install)[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    parts = launcher_command(install.channel)
    target, arguments = parts[0], subprocess.list2cmdline(parts[1:])
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        "$shell = New-Object -ComObject WScript.Shell\n"
        f"$link = $shell.CreateShortcut({_ps_quote(str(path))})\n"
        f"$link.TargetPath = {_ps_quote(target)}\n"
        f"$link.Arguments = {_ps_quote(arguments)}\n"
        f"$link.WorkingDirectory = {_ps_quote(str(Path(target).parent))}\n"
        f"$link.Description = {_ps_quote(_basename(install))}\n"
    )
    if install.executable is not None:
        script += f"$link.IconLocation = {_ps_quote(str(install.executable))}\n"
    script += "$link.Save()\n"

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0 or not path.is_file():
        raise RuntimeError(
            "não consegui criar o atalho do Windows: "
            + (result.stderr or "o PowerShell não devolveu detalhes").strip()[:300]
        )
    return Shortcut(path=path, created=True)


def _is_ours(path: Path) -> bool:
    """Só apagamos o que nós mesmos criamos."""
    if path.suffix == ".lnk":
        return "(Proxy)" in path.stem
    try:
        return MARKER.strip() in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
