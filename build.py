#!/usr/bin/env python3
"""Compila o componente nativo. Sem CMake, sem dependência externa.

    python build.py                 # compila para o sistema atual
    python build.py --target windows  # version.dll (MinGW ou MSVC)
    python build.py --target linux    # libdiscordproxy.so
    python build.py --all           # os dois, se houver compilador para ambos

No Linux basta o gcc ou o clang. Para gerar o `version.dll` a partir do Linux,
instale o MinGW (`sudo dnf install mingw64-gcc`) — ou, sem instalar nada no
sistema, `python -m pip install ziglang`, que traz um compilador capaz de gerar
as duas plataformas sozinho.
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


def _zig() -> list[str] | None:
    """`zig cc` compila as duas plataformas sem nada instalado no sistema."""
    found = shutil.which("zig")
    if found:
        return [found, "cc"]
    try:
        import ziglang  # type: ignore[import-not-found]
    except ImportError:
        return None
    candidate = Path(ziglang.__file__).parent / ("zig.exe" if os.name == "nt" else "zig")
    return [str(candidate), "cc"] if candidate.is_file() else None


def build_linux() -> Path:
    target = OUTPUT / LINUX_NAME
    common = [
        *WARNINGS,
        "-std=c99",
        "-fPIC",
        "-fvisibility=hidden",
        "-shared",
        "-pthread",
        str(SOURCE),
        "-o",
        str(target),
    ]
    compiler = _first(["gcc", "clang", "cc"])
    if compiler is not None:
        _run([compiler, *common, "-ldl"])
        return target
    zig = _zig()
    if zig is not None:
        _run([*zig, *common])
        return target
    raise BuildError(
        "nenhum compilador C encontrado. Fedora: sudo dnf install gcc · "
        "Debian/Ubuntu: sudo apt install build-essential · "
        "sem tocar no sistema: python -m pip install ziglang"
    )


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

    zig = _zig()
    if zig is not None:
        _run(
            [
                *zig,
                "-target",
                "x86_64-windows-gnu",
                *WARNINGS,
                "-std=c99",
                "-shared",
                str(SOURCE),
                str(DEFINITIONS),
                "-o",
                str(target),
                "-lws2_32",
                "-lpsapi",
            ]
        )
        return target

    raise BuildError(
        "nenhum compilador para Windows encontrado. Instale o MinGW "
        "(dnf install mingw64-gcc / apt install gcc-mingw-w64-x86-64), rode "
        "este script no Prompt de Comando do Visual Studio, ou instale o "
        "compilador portátil: python -m pip install ziglang"
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
