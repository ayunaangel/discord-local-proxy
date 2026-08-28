"""O componente nativo que ajusta o primeiro pacote UDP de voz.

O proxy do Electron cobre HTTP, HTTPS e WebSocket. A mídia de voz é UDP e passa
longe dele. O componente nativo entra só nesse ponto: quando o Discord manda o
pacote de descoberta de IP (74 bytes), ele envia antes um `0x00`, um `0x01` e,
se existir, o conteúdo de um arquivo `.bin` escolhido pelo usuário.

* Windows — `version.dll` ao lado do `Discord.exe` (carregamento lateral).
* Linux — `libdiscordproxy.so` carregada com `LD_PRELOAD` só no processo aberto
  pelo launcher.
* macOS, Flatpak e Snap — não suportado; nesses casos só o proxy TCP funciona.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

from .discord import Install

WINDOWS_SHIM = "version.dll"
LINUX_SHIM = "libdiscordproxy.so"
RECEIPT_SUFFIX = ".discord-proxy.sha256"


class VoiceError(RuntimeError):
    """O componente nativo não existe ou não pode ser instalado com segurança."""


def data_root() -> Path:
    """Pasta por usuário onde guardamos o componente e a configuração."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / "discord-proxy"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "discord-proxy"
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "discord-proxy"


def shim_name(platform: str | None = None) -> str:
    platform = platform or ("windows" if os.name == "nt" else "linux")
    return WINDOWS_SHIM if platform == "windows" else LINUX_SHIM


def find_shim(platform: str | None = None) -> Path:
    """Procura o binário nativo no pacote, na árvore de build ou nos dados."""
    name = shim_name(platform)
    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    candidates = [
        Path(os.environ["DISCORD_PROXY_NATIVE"]) / name
        if os.environ.get("DISCORD_PROXY_NATIVE")
        else None,
        bundle / "native" / name,
        Path(__file__).parent / "native" / name,
        Path(__file__).resolve().parent.parent / "build" / name,
        data_root() / "native" / name,
    ]
    for candidate in candidates:
        if candidate is not None and _regular(candidate):
            return candidate.resolve(strict=True)
    raise VoiceError(
        f"componente nativo {name} não encontrado — rode `python build.py` "
        "ou use um pacote pronto da página de releases"
    )


def install_shim(install: Install, *, source: Path | None = None) -> Path:
    """Deixa o componente pronto e devolve o caminho que o launcher vai usar."""
    if not install.supports_voice:
        raise VoiceError(install.voice_reason)

    if os.name == "nt":
        if install.directory is None:
            raise VoiceError("instalação do Windows sem executável")
        source = source or find_shim("windows")
        destination = install.directory / WINDOWS_SHIM
        _sideload(source, destination)
        return destination

    source = source or find_shim("linux")
    destination = data_root() / "native" / LINUX_SHIM
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _copy_atomic(source, destination, mode=0o700)
    return destination


def remove_shim(install: Install) -> bool:
    """Remove o `version.dll` — e só se for mesmo o nosso, conferido por hash."""
    if os.name != "nt" or install.directory is None:
        return False
    destination = install.directory / WINDOWS_SHIM
    receipt = destination.with_name(destination.name + RECEIPT_SUFFIX)
    expected = _read_receipt(receipt)
    if not _lexists(destination):
        # A DLL já sumiu (uma atualização do Discord costuma levá-la junto).
        receipt.unlink(missing_ok=True)
        return bool(expected)
    if not expected or not _regular(destination) or _sha256(destination) != expected:
        raise VoiceError(
            f"{destination} não foi instalado por esta ferramenta; ele ficou onde está"
        )
    destination.unlink()
    receipt.unlink(missing_ok=True)
    return True


def remove_shared_shim() -> bool:
    """Apaga a cópia guardada na pasta de dados do usuário."""
    removed = False
    for name in (WINDOWS_SHIM, LINUX_SHIM):
        target = data_root() / "native" / name
        if _regular(target):
            target.unlink()
            removed = True
    try:
        (data_root() / "native").rmdir()
    except OSError:
        pass
    return removed


def _sideload(source: Path, destination: Path) -> None:
    """Nunca sobrescreve um `version.dll` que não seja nosso."""
    _check_architecture(source, destination.parent)
    receipt = destination.with_name(destination.name + RECEIPT_SUFFIX)
    digest = _sha256(source)
    if _lexists(destination):
        if destination.is_symlink() or not destination.is_file():
            raise VoiceError(f"não é seguro substituir {destination}")
        current = _sha256(destination)
        if current == digest:
            _write_receipt(receipt, digest)
            return
        if _read_receipt(receipt) != current:
            raise VoiceError(
                f"já existe outro version.dll em {destination}; "
                "remova o arquivo à mão antes de continuar"
            )
    _copy_atomic(source, destination, mode=0o600)
    _write_receipt(receipt, digest)


# Máquinas do cabeçalho PE que nos interessam.
_MACHINES = {0x8664: "x64", 0x014C: "x86", 0xAA64: "arm64"}


def _pe_machine(path: Path) -> int | None:
    """Lê a arquitetura de um .exe/.dll sem depender de ferramenta externa."""
    try:
        with path.open("rb") as handle:
            if handle.read(2) != b"MZ":
                return None
            handle.seek(0x3C)
            offset = int.from_bytes(handle.read(4), "little")
            handle.seek(offset)
            if handle.read(4) != b"PE\x00\x00":
                return None
            return int.from_bytes(handle.read(2), "little")
    except OSError:
        return None


def _check_architecture(shim: Path, discord_directory: Path) -> None:
    """Uma DLL da arquitetura errada impede o Discord de abrir. Melhor recusar."""
    executables = sorted(discord_directory.glob("*.exe"))
    if not executables:
        return
    shim_machine = _pe_machine(shim)
    if shim_machine is None:
        return
    for executable in executables:
        machine = _pe_machine(executable)
        if machine is None or machine == shim_machine:
            continue
        raise VoiceError(
            f"o componente é {_MACHINES.get(shim_machine, hex(shim_machine))} e o "
            f"{executable.name} é {_MACHINES.get(machine, hex(machine))}; "
            "instalar assim impediria o Discord de abrir"
        )


def _copy_atomic(source: Path, destination: Path, *, mode: int) -> None:
    if source.is_symlink() or not source.is_file():
        raise VoiceError(f"origem inválida: {source}")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if _lexists(destination) and (destination.is_symlink() or not destination.is_file()):
        raise VoiceError(f"destino inseguro: {destination}")
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(name)
    try:
        with source.open("rb") as reader, os.fdopen(descriptor, "wb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _write_receipt(path: Path, digest: str) -> None:
    path.write_text(digest + "\n", encoding="ascii")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _read_receipt(path: Path) -> str:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_size > 128:
            return ""
        value = path.read_text(encoding="ascii", errors="replace").strip().lower()
    except OSError:
        return ""
    return value if len(value) == 64 and all(c in "0123456789abcdef" for c in value) else ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _lexists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True
