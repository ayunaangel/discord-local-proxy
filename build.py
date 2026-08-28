#!/usr/bin/env python3
"""Compila o componente nativo. Sem CMake, sem dependência externa.

    python build.py                 # compila para o sistema atual
    python build.py --target windows  # version.dll (MinGW ou MSVC)
    python build.py --target linux    # libdiscordproxy.so
    python build.py --all           # os dois, se houver compilador para ambos

No Linux basta o gcc ou o clang. Para gerar o `version.dll` a partir do Linux,
instale o MinGW: `sudo dnf install mingw64-gcc` (Fedora) ou
`sudo apt install gcc-mingw-w64-x86-64` (Debian/Ubuntu).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "native" / "udp_shim.c"
DEFINITIONS = ROOT / "native" / "udp_shim.def"
OUTPUT = ROOT / "build"

LINUX_NAME = "libdiscordproxy.so"
WINDOWS_NAME = "version.dll"

WARNINGS = ["-Wall", "-Wextra", "-Wno-unused-parameter", "-O2"]


class BuildError(RuntimeError):
    pass


def build_linux() -> Path:
    compiler = _first(["gcc", "clang", "cc"])
    if compiler is None:
        raise BuildError(
            "nenhum compilador C encontrado. Fedora: sudo dnf install gcc · "
            "Debian/Ubuntu: sudo apt install build-essential"
        )
    target = OUTPUT / LINUX_NAME
    _run(
        [
            compiler,
            *WARNINGS,
            "-std=c99",
            "-fPIC",
            "-fvisibility=hidden",
            "-shared",
            "-pthread",
            str(SOURCE),
            "-o",
            str(target),
            "-ldl",
        ]
    )
    return target


def build_windows() -> Path:
    target = OUTPUT / WINDOWS_NAME
    mingw = _first(["x86_64-w64-mingw32-gcc", "x86_64-w64-mingw32-gcc-win32"])
    if mingw is not None:
        _run(
            [
                mingw,
                *WARNINGS,
                "-std=c99",
                "-shared",
                str(SOURCE),
                str(DEFINITIONS),
                "-o",
                str(target),
                "-lws2_32",
                "-lpsapi",
                "-static-libgcc",
                "-Wl,--kill-at",
            ]
        )
        return target

    if os.name == "nt" and _first(["cl.exe"]) is not None:
        _run(
            [
                "cl.exe",
                "/nologo",
                "/O2",
                "/W3",
                "/LD",
                str(SOURCE),
                f"/Fe:{target}",
                "/link",
                f"/DEF:{DEFINITIONS}",
                "ws2_32.lib",
                "psapi.lib",
            ],
            cwd=OUTPUT,
        )
        return target

    raise BuildError(
        "nenhum compilador para Windows encontrado. Instale o MinGW "
        "(dnf install mingw64-gcc / apt install gcc-mingw-w64-x86-64) ou rode "
        "este script no Prompt de Comando do Visual Studio."
    )


def _first(names: list[str]) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    print("  " + " ".join(command))
    result = subprocess.run(command, cwd=str(cwd) if cwd else None, check=False)
    if result.returncode != 0:
        raise BuildError(f"o compilador terminou com código {result.returncode}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("linux", "windows"), help="plataforma alvo")
    parser.add_argument("--all", action="store_true", help="tenta as duas plataformas")
    arguments = parser.parse_args(argv)

    OUTPUT.mkdir(parents=True, exist_ok=True)
    targets: list[str]
    if arguments.all:
        targets = ["linux", "windows"]
    elif arguments.target:
        targets = [arguments.target]
    else:
        targets = ["windows" if os.name == "nt" else "linux"]

    built: list[Path] = []
    problems: list[str] = []
    for target in targets:
        print(f"[{target}]")
        try:
            built.append(build_linux() if target == "linux" else build_windows())
        except BuildError as exc:
            problems.append(f"{target}: {exc}")
            print(f"  falhou — {exc}", file=sys.stderr)

    for path in built:
        print(f"pronto: {path} ({path.stat().st_size} bytes)")
    if not built:
        return 1
    if problems and not arguments.all:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
