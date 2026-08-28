#!/usr/bin/env python3
"""Monta o pacote que vai para a página de releases.

    python build.py            # compila o componente nativo
    python package.py          # gera release/DiscordProxy-<plataforma>-x64.<ext>

Precisa do PyInstaller (`python -m pip install pyinstaller`). O pacote leva o
executável, o componente nativo, o arquivo de exemplo e o script de início
fácil da plataforma.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
RELEASE = ROOT / "release"
NAME = "DiscordProxy"


def build_executable(shim: Path) -> Path:
    separator = ";" if os.name == "nt" else ":"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        NAME,
        "--add-data",
        f"{shim}{separator}native",
        "--distpath",
        str(DIST),
        "--workpath",
        str(BUILD / "pyinstaller"),
        "--specpath",
        str(BUILD),
    ]
    if os.name == "nt":
        command.append("--windowed")
    command.append(str(ROOT / "discord_proxy" / "__main__.py"))
    subprocess.run(command, check=True)
    produced = DIST / (f"{NAME}.exe" if os.name == "nt" else NAME)
    if not produced.is_file():
        raise SystemExit(f"o PyInstaller não gerou {produced}")
    return produced


def collect(executable: Path, shim: Path, staging: Path) -> None:
    if staging.exists():
        shutil.rmtree(staging)
    hidden = staging / ".discord-proxy"
    hidden.mkdir(parents=True)
    shutil.copy2(executable, hidden / executable.name)
    shutil.copy2(shim, hidden / shim.name)
    shutil.copy2(ROOT / "discord-proxy.ini.example", hidden / "discord-proxy.ini.example")
    shutil.copy2(ROOT / "README.md", hidden / "README.md")
    shutil.copy2(ROOT / "LICENSE", hidden / "LICENSE")

    starter = "INICIAR-WINDOWS.cmd" if os.name == "nt" else "INICIAR-LINUX.sh"
    destination = staging / starter
    shutil.copy2(ROOT / starter, destination)
    if os.name != "nt":
        destination.chmod(0o755)
        (hidden / executable.name).chmod(0o755)


def archive(staging: Path, name: str) -> Path:
    RELEASE.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        target = RELEASE / f"{name}.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
            for item in sorted(staging.rglob("*")):
                bundle.write(item, item.relative_to(staging))
    else:
        target = RELEASE / f"{name}.tar.gz"
        with tarfile.open(target, "w:gz") as bundle:
            for item in sorted(staging.iterdir()):
                bundle.add(item, arcname=item.name)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default = f"{NAME}-{'Windows' if os.name == 'nt' else 'Linux'}-x64"
    parser.add_argument("--name", default=default, help="nome do arquivo final")
    arguments = parser.parse_args(argv)

    shim = BUILD / ("version.dll" if os.name == "nt" else "libdiscordproxy.so")
    if not shim.is_file():
        raise SystemExit(f"componente nativo ausente: {shim} — rode `python build.py` antes")

    executable = build_executable(shim)
    staging = BUILD / "pacote"
    collect(executable, shim, staging)
    target = archive(staging, arguments.name)
    print(f"pronto: {target} ({target.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
