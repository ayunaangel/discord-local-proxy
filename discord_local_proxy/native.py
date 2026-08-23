from __future__ import annotations

import hashlib
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

from .config import ConfigError
from .discovery import DiscordInstallation, app_data_root


class NativeShimError(ConfigError):
    """The native UDP shim is missing or cannot be installed safely."""


WINDOWS_SHIM = "version.dll"
LINUX_SHIM = "libdiscord_udp_shim.so"
WINDOWS_RECEIPT = "version.dll.discord-local-proxy.sha256"


def find_bundled_shim(*, platform: str | None = None) -> Path:
    platform = platform or ("windows" if os.name == "nt" else "linux")
    filename = WINDOWS_SHIM if platform == "windows" else LINUX_SHIM
    candidates: list[Path] = []
    override = os.environ.get("DISCORD_LOCAL_PROXY_NATIVE_DIR")
    if override:
        candidates.append(Path(override) / filename)
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    candidates.extend(
        (
            bundle_root / "native" / filename,
            Path(__file__).parent / "native" / filename,
            Path(__file__).resolve().parents[1] / "build" / "native" / "linux" / filename,
            Path(__file__).resolve().parents[1] / "build" / "native" / "windows" / "Release" / filename,
            app_data_root() / "native" / filename,
        )
    )
    for candidate in candidates:
        if _regular_file(candidate):
            return candidate.resolve(strict=True)
    raise NativeShimError(
        f"componente nativo {filename} não encontrado; execute o build nativo ou use um pacote de release"
    )


def ensure_native_shim(
    installation: DiscordInstallation,
    *,
    source: Path | None = None,
) -> Path:
    if not installation.supports_udp_shim:
        raise NativeShimError(
            f"{installation.source} isola bibliotecas externas; o ajuste UDP não é suportado nesse pacote"
        )
    if os.name == "nt" or installation.source == "squirrel":
        if installation.executable is None:
            raise NativeShimError("instalação Windows sem executável")
        source = source or find_bundled_shim(platform="windows")
        if os.name == "nt":
            managed_source = app_data_root() / "native" / WINDOWS_SHIM
            if source.resolve(strict=True) != managed_source.resolve(strict=False):
                _atomic_copy(source, managed_source, mode=0o600)
            source = managed_source
        destination = installation.executable.parent / WINDOWS_SHIM
        receipt = installation.executable.parent / WINDOWS_RECEIPT
        _install_windows_sideload(source, destination, receipt)
        return destination

    source = source or find_bundled_shim(platform="linux")
    native_root = app_data_root() / "native"
    native_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = native_root / LINUX_SHIM
    _atomic_copy(source, destination, mode=0o700)
    return destination


def remove_native_shim(installation: DiscordInstallation) -> bool:
    if os.name != "nt" and installation.source != "squirrel":
        # Shared Linux asset is removed only by the top-level uninstaller.
        return False
    if installation.executable is None:
        return False
    destination = installation.executable.parent / WINDOWS_SHIM
    receipt = installation.executable.parent / WINDOWS_RECEIPT
    destination_exists = _path_lexists(destination)
    receipt_exists = _path_lexists(receipt)
    if not destination_exists and not receipt_exists:
        return False
    if not destination_exists and _read_receipt(receipt):
        receipt.unlink()
        return True
    expected = _read_receipt(receipt)
    if not expected or not _regular_file(destination) or _sha256(destination) != expected:
        raise NativeShimError(
            f"{destination} foi alterado ou não pertence ao instalador; ele foi preservado"
        )
    destination.unlink()
    receipt.unlink(missing_ok=True)
    return True


def remove_shared_linux_shim() -> bool:
    destination = app_data_root() / "native" / LINUX_SHIM
    if not _path_lexists(destination):
        return False
    if destination.is_symlink() or not destination.is_file():
        raise NativeShimError(f"componente nativo inseguro preservado: {destination}")
    destination.unlink()
    try:
        destination.parent.rmdir()
    except OSError:
        pass
    return True


def remove_shared_windows_shim() -> bool:
    destination = app_data_root() / "native" / WINDOWS_SHIM
    if not _path_lexists(destination):
        return False
    if destination.is_symlink() or not destination.is_file():
        raise NativeShimError(f"componente nativo inseguro preservado: {destination}")
    destination.unlink()
    try:
        destination.parent.rmdir()
    except OSError:
        pass
    return True


def _install_windows_sideload(source: Path, destination: Path, receipt: Path) -> None:
    source_hash = _sha256(source)
    if _path_lexists(destination):
        if destination.is_symlink() or not destination.is_file():
            raise NativeShimError(f"não é seguro substituir {destination}")
        destination_hash = _sha256(destination)
        previous_hash = _read_receipt(receipt)
        if destination_hash == source_hash:
            _write_receipt(receipt, source_hash)
            return
        if previous_hash != destination_hash:
            raise NativeShimError(
                f"já existe um version.dll não gerenciado em {destination}; remova o conflito manualmente"
            )
    _atomic_copy(source, destination, mode=0o600)
    _write_receipt(receipt, source_hash)


def _atomic_copy(source: Path, destination: Path, *, mode: int) -> None:
    if source.is_symlink() or not source.is_file():
        raise NativeShimError(f"origem nativa inválida: {source}")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if _path_lexists(destination) and (destination.is_symlink() or not destination.is_file()):
        raise NativeShimError(f"destino nativo inseguro: {destination}")
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
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


def _write_receipt(path: Path, digest: str) -> None:
    if _path_lexists(path) and (path.is_symlink() or not path.is_file()):
        raise NativeShimError(f"recibo inseguro: {path}")
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="ascii", newline="\n", closefd=True) as handle:
            handle.write(digest + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        temp.unlink(missing_ok=True)
        raise


def _read_receipt(path: Path) -> str:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return ""
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_size > 128:
        return ""
    value = path.read_text(encoding="ascii").strip().lower()
    return value if len(value) == 64 and all(char in "0123456789abcdef" for char in value) else ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_file(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode)


def _path_lexists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False
