#!/usr/bin/env python3
"""Monta o pacote que vai para a página de releases.

    python build.py            # compila o componente nativo
    python package.py          # gera release/DiscordProxy-<plataforma>-x64.<ext>

Precisa do PyInstaller (`python -m pip install pyinstaller`). O pacote leva o
executável, o componente nativo, o arquivo de exemplo e o script de início
fácil da plataforma.

No Windows o executável sai em **modo pasta**, e não em arquivo único. Um
executável de arquivo único se descompacta sozinho numa pasta temporária e roda
de lá — é justamente esse comportamento, num binário sem assinatura digital, que
o Windows Defender reprova por heurística (`Wacatac`, `Sabsik`, `Zpevdo` e
parentes). O modo pasta não descompacta nada em tempo de execução. Junto vão os
metadados do arquivo (empresa, produto, versão), porque binário sem procedência
nenhuma pesa contra na mesma conta.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

from discord_proxy import __version__

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
RELEASE = ROOT / "release"
NAME = "DiscordProxy"

# Pasta do pacote onde ficam o executável e as bibliotecas. Nome comum, à
# vista: uma pasta começada por ponto parece coisa escondida, e "esconder o
# executável" é um dos padrões que o antivírus pontua.
PROGRAM_DIR = "programa"

WINDOWS = os.name == "nt"


def version_numbers() -> tuple[int, int, int, int]:
    parts = [int(piece) for piece in re.findall(r"\d+", __version__)][:4]
    return tuple(parts + [0] * (4 - len(parts)))  # type: ignore[return-value]


def write_version_resource() -> Path:
    """Gera os metadados que o Windows mostra em Propriedades do arquivo.

    O arquivo é lido pelo PyInstaller como código Python, então as acentuações
    saem escapadas (`ascii()`) e o arquivo em si fica ASCII puro — assim nenhum
    detalhe de codificação do leitor pode quebrar o build.
    """
    numbers = version_numbers()
    readable = ".".join(str(number) for number in numbers)
    fields = [
        ("CompanyName", "ayunaangel"),
        ("FileDescription", "Discord Proxy — faz o Discord sair por outro país"),
        ("FileVersion", readable),
        ("InternalName", NAME),
        ("LegalCopyright", "Copyright (c) ayunaangel — licença MIT"),
        ("OriginalFilename", f"{NAME}.exe"),
        ("ProductName", "Discord Proxy"),
        ("ProductVersion", readable),
    ]
    strings = ",\n         ".join(
        f"StringStruct({ascii(key)}, {ascii(value)})" for key, value in fields
    )
    content = f"""# Gerado por package.py -- nao edite a mao. Arquivo ASCII puro.
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={numbers},
    prodvers={numbers},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '041604B0',
        [{strings}])
    ]),
    VarFileInfo([VarStruct('Translation', [0x0416, 1200])])
  ]
)
"""
    BUILD.mkdir(parents=True, exist_ok=True)
    target = BUILD / "version-info.txt"
    target.write_text(content, encoding="ascii")
    return target


def build_executable(shim: Path) -> Path:
    separator = ";" if WINDOWS else ":"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        # Compressor de executável é sinal de alarme para antivírus e não nos
        # traz nada; se houver um `upx` no PATH, o PyInstaller o usaria sozinho.
        "--noupx",
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
    if WINDOWS:
        command += [
            "--onedir",
            "--windowed",
            "--version-file",
            str(write_version_resource()),
        ]
    else:
        command.append("--onefile")
    command.append(str(ROOT / "main.py"))
    subprocess.run(command, check=True)

    produced = DIST / NAME / f"{NAME}.exe" if WINDOWS else DIST / NAME
    if not produced.is_file():
        raise SystemExit(f"o PyInstaller não gerou {produced}")
    return produced


def collect(executable: Path, shim: Path, staging: Path) -> None:
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    program = staging / PROGRAM_DIR

    if executable.parent == DIST / NAME:
        # Modo pasta: vai o conjunto inteiro (executável + bibliotecas).
        shutil.copytree(executable.parent, program)
    else:
        program.mkdir(parents=True)
        shutil.copy2(executable, program / executable.name)

    # No modo pasta o componente nativo já veio junto pelo `--add-data`; uma
    # segunda cópia solta seria só mais um arquivo para o antivírus olhar. No
    # modo arquivo único ele fica dentro do executável, e aí a cópia solta é a
    # única que dá para conferir por hash.
    bundled = program / "_internal" / "native" / shim.name
    shim_copy = bundled if bundled.is_file() else program / shim.name
    if shim_copy != bundled:
        shutil.copy2(shim, shim_copy)

    shutil.copy2(ROOT / "discord-proxy.ini.example", program / "discord-proxy.ini.example")
    shutil.copy2(ROOT / "README.md", program / "README.md")
    shutil.copy2(ROOT / "LICENSE", program / "LICENSE")
    # O guia fica de fora, à vista de quem extrair o pacote.
    shutil.copy2(ROOT / "COMO-USAR.txt", staging / "COMO-USAR.txt")

    starter = "INICIAR-WINDOWS.cmd" if WINDOWS else "INICIAR-LINUX.sh"
    destination = staging / starter
    shutil.copy2(ROOT / starter, destination)
    if not WINDOWS:
        destination.chmod(0o755)
        (program / executable.name).chmod(0o755)

    # Os hashes que o README manda conferir antes de liberar no antivírus.
    # Caminhos relativos à pasta do programa, para o `sha256sum -c` funcionar
    # de dentro dela.
    listing = [program / executable.name, shim_copy]
    (program / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256(path)}  {path.relative_to(program).as_posix()}\n" for path in listing),
        encoding="ascii",
    )


def archive(staging: Path, name: str) -> Path:
    RELEASE.mkdir(parents=True, exist_ok=True)
    if WINDOWS:
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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checksum_file(target: Path) -> Path:
    """No formato do `sha256sum -c` e do `certutil -hashfile`."""
    receipt = target.with_name(target.name + ".sha256")
    receipt.write_text(f"{sha256(target)}  {target.name}\n", encoding="ascii")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default = f"{NAME}-{'Windows' if WINDOWS else 'Linux'}-x64"
    parser.add_argument("--name", default=default, help="nome do arquivo final")
    arguments = parser.parse_args(argv)

    shim = BUILD / ("version.dll" if WINDOWS else "libdiscordproxy.so")
    if not shim.is_file():
        raise SystemExit(f"componente nativo ausente: {shim} — rode `python build.py` antes")

    executable = build_executable(shim)
    staging = BUILD / "pacote"
    collect(executable, shim, staging)
    target = archive(staging, arguments.name)
    receipt = checksum_file(target)
    print(f"pronto: {target} ({target.stat().st_size} bytes)")
    print(f"        {receipt.read_text(encoding='ascii').strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
