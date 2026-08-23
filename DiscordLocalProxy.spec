# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os
import sys

project_root = Path(SPECPATH)
native_name = "version.dll" if os.name == "nt" else "libdiscord_udp_shim.so"
native_path = project_root / "discord_local_proxy" / "native" / native_name
if not native_path.is_file():
    raise SystemExit(f"Native component not found: {native_path}")

binaries = [(str(native_path), "native")]
if os.name != "nt":
    python_lib = Path(sys.base_prefix) / "lib"
    private_tk_libraries = {
        candidate.resolve()
        for pattern in ("libtcl*.so", "libtk*.so")
        for candidate in python_lib.glob(pattern)
        if candidate.is_file()
    }
    binaries.extend((str(candidate), ".") for candidate in sorted(private_tk_libraries))

analysis = Analysis(
    [str(project_root / "run_discord_local_proxy.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    hiddenimports=["tkinter", "tkinter.ttk", "tkinter.messagebox"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="DiscordLocalProxy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
