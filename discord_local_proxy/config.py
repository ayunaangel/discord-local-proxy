from __future__ import annotations

import configparser
import ipaddress
import os
import re
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


MAX_CONFIG_BYTES = 64 * 1024
MAX_VOICE_PACKET_BYTES = 65_507
CONFIG_FILENAME = "discord-local-proxy.ini"
VOICE_PACKET_FILENAMES = (
    "discord-local-proxy-packet.bin",
    "drover-packet.bin",
)
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ALLOWED_SECTIONS = {"proxy", "voice", "discord"}
_ALLOWED_KEYS = {
    "proxy": {"type", "host", "port", "username", "password", "password_env", "connect_timeout"},
    "voice": {"enabled", "delay_ms", "packet_file"},
    "discord": {"executable"},
}


class ConfigError(ValueError):
    """The INI file is unsafe or invalid."""


class ConfigPermissionError(ConfigError):
    """A config containing a password is readable by other users."""


@dataclass(frozen=True)
class ProxySettings:
    kind: str = "none"
    host: str = ""
    port: int = 0
    username: str = ""
    password: str = field(default="", repr=False)
    password_env: str = ""
    connect_timeout: float = 10.0

    def __post_init__(self) -> None:
        kind = self.kind.strip().lower()
        object.__setattr__(self, "kind", kind)
        if kind not in {"none", "http", "socks5"}:
            raise ConfigError("proxy.type deve ser none, http ou socks5")

        if kind == "none":
            if any((self.host, self.port, self.username, self.password, self.password_env)):
                raise ConfigError("proxy.type=none não aceita host, porta ou credenciais")
            return

        normalized_host = _validate_host(self.host)
        object.__setattr__(self, "host", normalized_host)
        if not 1 <= self.port <= 65535:
            raise ConfigError("proxy.port deve estar entre 1 e 65535")
        _validate_text("proxy.username", self.username, 255)
        _validate_secret(self.password)
        if self.password_env and not _ENV_NAME.fullmatch(self.password_env):
            raise ConfigError("proxy.password_env não é um nome de variável válido")
        if self.password and self.password_env:
            raise ConfigError("use apenas password ou password_env, não ambos")
        if not 1.0 <= self.connect_timeout <= 60.0:
            raise ConfigError("proxy.connect_timeout deve estar entre 1 e 60 segundos")
        if self.kind == "socks5":
            for label, value in (("username", self.username), ("password", self.password)):
                if len(value.encode("utf-8")) > 255:
                    raise ConfigError(f"proxy.{label} excede o limite SOCKS5 de 255 bytes")

    @property
    def enabled(self) -> bool:
        return self.kind != "none"

    @property
    def safe_label(self) -> str:
        if not self.enabled:
            return "direto (sem proxy)"
        user = f"{self.username}:***@" if self.username else ""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.kind}://{user}{host}:{self.port}"

    def resolved_password(self, environ: Mapping[str, str] | None = None) -> str:
        if self.password_env:
            env = os.environ if environ is None else environ
            try:
                value = env[self.password_env]
            except KeyError as exc:
                raise ConfigError(
                    f"a variável {self.password_env} definida em proxy.password_env não existe"
                ) from exc
            _validate_secret(value)
            return value
        return self.password


@dataclass(frozen=True)
class VoiceSettings:
    enabled: bool = True
    delay_ms: int = 50
    packet_file: Path | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.delay_ms <= 1000:
            raise ConfigError("voice.delay_ms deve estar entre 0 e 1000")
        if self.packet_file is not None:
            object.__setattr__(
                self,
                "packet_file",
                validate_voice_packet_file(self.packet_file),
            )


@dataclass(frozen=True)
class AppConfig:
    proxy: ProxySettings = field(default_factory=ProxySettings)
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    executable: Path | None = None


def default_config() -> AppConfig:
    return AppConfig()


def load_config(path: Path, *, environ: Mapping[str, str] | None = None) -> AppConfig:
    path = Path(path)
    data, mode = _read_regular_file(path)
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        parser.read_string(data.decode("utf-8-sig"), source=str(path))
    except (UnicodeDecodeError, configparser.Error) as exc:
        raise ConfigError(f"INI inválido em {path}: {exc}") from exc

    if parser.defaults():
        raise ConfigError("a seção DEFAULT não é permitida")
    unknown_sections = set(parser.sections()) - _ALLOWED_SECTIONS
    if unknown_sections:
        raise ConfigError(f"seção desconhecida: {sorted(unknown_sections)[0]}")
    for section in parser.sections():
        unknown_keys = set(parser[section]) - _ALLOWED_KEYS[section]
        if unknown_keys:
            raise ConfigError(f"chave desconhecida em [{section}]: {sorted(unknown_keys)[0]}")

    kind = _get(parser, "proxy", "type", "none").strip().lower()
    host = _get(parser, "proxy", "host", "").strip()
    port_text = _get(parser, "proxy", "port", "").strip()
    port = _parse_int("proxy.port", port_text, default=0)
    username = _get(parser, "proxy", "username", "")
    password = _get(parser, "proxy", "password", "")
    password_env = _get(parser, "proxy", "password_env", "").strip()
    timeout = _parse_float(
        "proxy.connect_timeout",
        _get(parser, "proxy", "connect_timeout", "10"),
    )
    voice_enabled = _parse_bool("voice.enabled", _get(parser, "voice", "enabled", "true"))
    delay_ms = _parse_int("voice.delay_ms", _get(parser, "voice", "delay_ms", "50"))
    packet_text = _get(parser, "voice", "packet_file", "").strip()
    executable_text = _get(parser, "discord", "executable", "").strip()

    proxy = ProxySettings(
        kind=kind,
        host=host,
        port=port,
        username=username,
        password=password,
        password_env=password_env,
        connect_timeout=timeout,
    )
    if proxy.password and os.name != "nt" and mode & 0o077:
        raise ConfigPermissionError(
            f"{path} contém senha e deve usar permissão 0600 (atual: {mode & 0o777:04o})"
        )
    # Resolve now so a missing secret fails before Discord is started.
    proxy.resolved_password(environ)

    executable: Path | None = None
    if executable_text:
        _validate_path_text(executable_text)
        candidate = Path(executable_text).expanduser()
        executable = candidate if candidate.is_absolute() else path.parent / candidate
        executable = executable.resolve(strict=False)

    packet_file: Path | None = None
    if packet_text:
        _validate_path_text(packet_text)
        packet_candidate = Path(packet_text).expanduser()
        if not packet_candidate.is_absolute():
            packet_candidate = path.parent / packet_candidate
        packet_file = validate_voice_packet_file(packet_candidate)
    else:
        packet_file = find_adjacent_voice_packet(path.parent)

    return AppConfig(
        proxy=proxy,
        voice=VoiceSettings(
            enabled=voice_enabled,
            delay_ms=delay_ms,
            packet_file=packet_file,
        ),
        executable=executable,
    )


def save_config(path: Path, config: AppConfig) -> None:
    """Atomically write a strict INI with user-only permissions."""
    path = Path(path)
    _assert_safe_destination(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass

    parser = configparser.ConfigParser(interpolation=None)
    parser["proxy"] = {
        "type": config.proxy.kind,
        "host": config.proxy.host,
        "port": str(config.proxy.port) if config.proxy.enabled else "",
        "username": config.proxy.username,
        "password": config.proxy.password,
        "password_env": config.proxy.password_env,
        "connect_timeout": _format_number(config.proxy.connect_timeout),
    }
    parser["voice"] = {
        "enabled": "true" if config.voice.enabled else "false",
        "delay_ms": str(config.voice.delay_ms),
        "packet_file": str(config.voice.packet_file) if config.voice.packet_file else "",
    }
    parser["discord"] = {
        "executable": str(config.executable) if config.executable else "",
    }

    import io

    output = io.StringIO()
    output.write("; Discord Local Proxy — edite e salve com UTF-8\n")
    output.write("; Senhas neste arquivo ficam em texto simples. Prefira password_env.\n\n")
    parser.write(output, space_around_delimiters=True)
    payload = output.getvalue().encode("utf-8")
    if len(payload) > MAX_CONFIG_BYTES:
        raise ConfigError("configuração excede 64 KiB")

    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        os.chmod(temp_path, 0o600)
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        _fsync_directory(path.parent)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise


def _read_regular_file(path: Path) -> tuple[bytes, int]:
    try:
        before = path.lstat()
    except FileNotFoundError as exc:
        raise ConfigError(f"configuração não encontrada: {path}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ConfigError(f"a configuração deve ser um arquivo regular, não um link: {path}")
    if before.st_size > MAX_CONFIG_BYTES:
        raise ConfigError("configuração excede 64 KiB")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ConfigError(f"não foi possível abrir {path}: {exc}") from exc
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            raise ConfigError("a configuração mudou durante a abertura")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(8192, MAX_CONFIG_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_CONFIG_BYTES:
                raise ConfigError("configuração excede 64 KiB")
        return b"".join(chunks), stat.S_IMODE(opened.st_mode)
    finally:
        os.close(fd)


def _assert_safe_destination(path: Path) -> None:
    _validate_path_text(str(path))
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ConfigError(f"destino inseguro para configuração: {path}")
    if info.st_nlink != 1:
        raise ConfigError(f"configuração com hard links não será sobrescrita: {path}")


def validate_voice_packet_file(path: Path) -> Path:
    candidate = Path(path).expanduser().absolute()
    _validate_path_text(str(candidate))
    try:
        before = candidate.lstat()
    except FileNotFoundError as exc:
        raise ConfigError(f"pacote UDP personalizado não encontrado: {candidate}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ConfigError(
            f"o pacote UDP personalizado deve ser um arquivo regular, não um link: {candidate}"
        )
    if not 1 <= before.st_size <= MAX_VOICE_PACKET_BYTES:
        raise ConfigError(
            f"o pacote UDP personalizado deve ter entre 1 e {MAX_VOICE_PACKET_BYTES} bytes"
        )

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(candidate, flags)
    except OSError as exc:
        raise ConfigError(f"não foi possível abrir o pacote UDP {candidate}: {exc}") from exc
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or not 1 <= opened.st_size <= MAX_VOICE_PACKET_BYTES
        ):
            raise ConfigError("o pacote UDP personalizado mudou durante a abertura")
    finally:
        os.close(fd)
    return candidate


def find_adjacent_voice_packet(directory: Path) -> Path | None:
    directory = Path(directory).expanduser().absolute()
    for filename in VOICE_PACKET_FILENAMES:
        candidate = directory / filename
        try:
            candidate.lstat()
        except FileNotFoundError:
            continue
        return validate_voice_packet_file(candidate)
    return None


def _validate_host(host: str) -> str:
    host = host.strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    _validate_text("proxy.host", host, 253)
    if not host:
        raise ConfigError("proxy.host é obrigatório")
    if any(char in host for char in "/\\@?#") or "://" in host:
        raise ConfigError("proxy.host deve conter apenas o host, sem URL, porta ou caminho")
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        return host
    except ValueError:
        pass
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ConfigError("proxy.host não é um hostname válido") from exc
    if len(ascii_host) > 253 or any(
        not label or len(label) > 63 or not re.fullmatch(r"[A-Za-z0-9_-]+", label)
        for label in ascii_host.rstrip(".").split(".")
    ):
        raise ConfigError("proxy.host não é um hostname válido")
    return ascii_host.rstrip(".")


def _validate_text(label: str, value: str, maximum: int) -> None:
    if len(value) > maximum:
        raise ConfigError(f"{label} é longo demais")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ConfigError(f"{label} contém caracteres de controle")


def _validate_secret(value: str) -> None:
    if len(value) > 1024:
        raise ConfigError("proxy.password é longa demais")
    if "\x00" in value or "\r" in value or "\n" in value:
        raise ConfigError("proxy.password contém quebra de linha ou NUL")


def _validate_path_text(value: str) -> None:
    if not value or "\x00" in value or "\r" in value or "\n" in value:
        raise ConfigError("caminho inválido")


def _get(parser: configparser.ConfigParser, section: str, key: str, default: str) -> str:
    return parser.get(section, key, fallback=default, raw=True)


def _parse_int(label: str, value: str, *, default: int | None = None) -> int:
    text = value.strip()
    if not text and default is not None:
        return default
    try:
        return int(text, 10)
    except ValueError as exc:
        raise ConfigError(f"{label} deve ser um número inteiro") from exc


def _parse_float(label: str, value: str) -> float:
    try:
        return float(value.strip())
    except ValueError as exc:
        raise ConfigError(f"{label} deve ser um número") from exc


def _parse_bool(label: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "yes", "true", "on", "sim"}:
        return True
    if normalized in {"0", "no", "false", "off", "não", "nao"}:
        return False
    raise ConfigError(f"{label} deve ser true ou false")


def _format_number(value: float) -> str:
    numeric = float(value)
    return str(int(numeric)) if numeric.is_integer() else str(numeric)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
