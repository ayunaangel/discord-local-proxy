"""Configuração do Discord Proxy.

Um único arquivo INI, uma única seção. O proxy é escrito como uma URL só — o
mesmo formato do `drover.ini` — em vez de host/porta/usuário/senha espalhados
em campos separados.

    [discord-proxy]
    proxy  = socks5://127.0.0.1:9150
    voice  = off

O `proxy` é o que troca a região da chamada. O `voice` é outra coisa: mexe no
primeiro pacote UDP para furar filtro de DPI, não tem efeito nenhum sobre
região, e por isso nasce desligado.
"""

from __future__ import annotations

import configparser
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

CONFIG_NAME = "discord-proxy.ini"
SECTION = "discord-proxy"
MAX_CONFIG_BYTES = 64 * 1024
MAX_PACKET_BYTES = 65_507

# scheme://[usuario[:senha]@]host:porta   (o esquema e as credenciais são opcionais)
_PROXY_RE = re.compile(
    r"""\A
    (?:(?P<scheme>[a-z][a-z0-9+.-]*)://)?
    (?:(?P<user>[^:@/]+)(?::(?P<password>[^@/]*))?@)?
    (?P<host>\[[0-9a-fA-F:]+\]|[^:@/\[\]]+)
    :(?P<port>\d{1,5})
    /?\Z""",
    re.VERBOSE | re.IGNORECASE,
)
_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_TRUE = {"1", "on", "true", "yes", "sim", "ligado"}
_FALSE = {"0", "off", "false", "no", "nao", "não", "desligado", ""}


class ConfigError(ValueError):
    """O INI está inválido ou aponta para algo que não dá para usar."""


@dataclass(frozen=True)
class Proxy:
    """Um upstream HTTP ou SOCKS5 já validado."""

    scheme: str = ""
    host: str = ""
    port: int = 0
    user: str = ""
    password: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.scheme)

    @property
    def has_auth(self) -> bool:
        return bool(self.user or self.password)

    @property
    def url(self) -> str:
        """A URL completa, do jeito que vai para o INI. Inclui a senha."""
        if not self.enabled:
            return ""
        credentials = ""
        if self.has_auth:
            credentials = f"{self.user}:{self.password}@" if self.password else f"{self.user}@"
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{credentials}{host}:{self.port}"

    @property
    def label(self) -> str:
        """Descrição segura para log e interface — nunca inclui a senha."""
        if not self.enabled:
            return "direto (sem proxy)"
        credentials = f"{self.user}:***@" if self.has_auth else ""
        return f"{self.scheme}://{credentials}{self.host}:{self.port}"


@dataclass(frozen=True)
class Config:
    proxy: Proxy = Proxy()
    voice: bool = False
    delay_ms: int = 50
    packet: Path | None = None
    executable: Path | None = None
    path: Path | None = None

    def as_ini(self) -> str:
        return render_ini(self)


def parse_proxy(text: str, *, environ: Mapping[str, str] | None = None) -> Proxy:
    """Converte `socks5://user:senha@host:porta` em um :class:`Proxy`.

    Texto vazio significa modo direto. `${VARIAVEL}` em qualquer parte da URL é
    trocado pelo valor da variável de ambiente, para manter a senha fora do
    arquivo quando o usuário preferir.
    """
    text = (text or "").strip()
    if not text:
        return Proxy()

    env = os.environ if environ is None else environ

    def expand(value: str) -> str:
        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            try:
                return env[name]
            except KeyError:
                raise ConfigError(f"a variável de ambiente {name} não existe") from None

        return _ENV_RE.sub(replace, value)

    match = _PROXY_RE.match(expand(text))
    if match is None:
        raise ConfigError(
            "proxy inválido; use algo como socks5://127.0.0.1:1080 "
            "ou http://usuario:senha@servidor:8080"
        )

    scheme = (match.group("scheme") or "http").lower()
    if scheme in {"https", "http"}:
        scheme = "http"
    elif scheme in {"socks5", "socks5h", "socks"}:
        scheme = "socks5"
    else:
        raise ConfigError(f"tipo de proxy não suportado: {scheme} (use http ou socks5)")

    host = match.group("host").strip("[]")
    port = int(match.group("port"))
    if not 1 <= port <= 65535:
        raise ConfigError("a porta do proxy precisa estar entre 1 e 65535")

    user = match.group("user") or ""
    password = match.group("password") or ""
    if scheme == "socks5":
        for label, value in (("usuário", user), ("senha", password)):
            if len(value.encode("utf-8")) > 255:
                raise ConfigError(f"{label} do SOCKS5 passa de 255 bytes")

    return Proxy(scheme=scheme, host=host, port=port, user=user, password=password)


def parse_bool(text: str) -> bool:
    value = (text or "").strip().casefold()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"valor de liga/desliga inválido: {text!r}")


def load(path: Path, *, environ: Mapping[str, str] | None = None) -> Config:
    path = Path(path).expanduser()
    data = _read_small_file(path)
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(data.decode("utf-8-sig"), source=str(path))
    except (UnicodeDecodeError, configparser.Error) as exc:
        raise ConfigError(f"INI inválido em {path}: {exc}") from exc

    # Aceita o nome novo e o antigo para quem vem do discord-local-proxy.
    for name in (SECTION, "drover", "proxy"):
        if parser.has_section(name):
            section = parser[name]
            break
    else:
        section = parser[configparser.DEFAULTSECT]

    proxy = parse_proxy(section.get("proxy", ""), environ=environ)
    voice = parse_bool(section.get("voice", "off"))
    delay_ms = _parse_delay(section.get("delay", "50"))
    packet = _resolve_optional_path(section.get("packet", ""), path.parent)
    if packet is not None:
        packet = validate_packet(packet)
    executable = _resolve_optional_path(section.get("discord", ""), path.parent)

    if proxy.password and os.name != "nt":
        _warn_or_fix_permissions(path)

    return Config(
        proxy=proxy,
        voice=voice,
        delay_ms=delay_ms,
        packet=packet,
        executable=executable,
        path=path,
    )


def load_or_default(path: Path | None, *, environ: Mapping[str, str] | None = None) -> Config:
    if path is None or not Path(path).is_file():
        return Config(path=Path(path) if path else None)
    return load(Path(path), environ=environ)


def render_ini(config: Config) -> str:
    url = config.proxy.url
    return (
        f"[{SECTION}]\n"
        "; O proxy decide de onde o Discord parece vir — e é isso que muda a\n"
        "; região do servidor de voz que ele te entrega (a mesma por onde passa\n"
        "; o vídeo do Go Live). Vazio = direto, sem trocar nada.\n"
        "; Exemplos: socks5://127.0.0.1:9150  |  http://usuario:senha@servidor:8080\n"
        f"proxy = {url}\n"
        "\n"
        "; Ajuste de voz por UDP (contra filtro de DPI). Não tem efeito sobre\n"
        "; região; deixe off a menos que a voz esteja bloqueada na sua rede.\n"
        f"voice = {'on' if config.voice else 'off'}\n"
        "\n"
        "; Pausa em milissegundos depois do preparo de voz.\n"
        f"delay = {config.delay_ms}\n"
        "\n"
        "; Arquivo .bin opcional enviado antes do preparo de voz.\n"
        f"packet = {config.packet or ''}\n"
        "\n"
        "; Caminho manual do executável do Discord (vazio = detectar sozinho).\n"
        f"discord = {config.executable or ''}\n"
    )


def save(path: Path, config: Config) -> Path:
    """Grava o INI de forma atômica, sempre com permissão 0600."""
    path = Path(path).expanduser()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    payload = render_ini(config).encode("utf-8")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def validate_packet(path: Path) -> Path:
    path = Path(path).expanduser()
    try:
        info = path.lstat()
    except OSError as exc:
        raise ConfigError(f"pacote inicial não encontrado: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ConfigError(f"o pacote inicial precisa ser um arquivo comum: {path}")
    if info.st_size < 1:
        raise ConfigError(f"o pacote inicial está vazio: {path}")
    if info.st_size > MAX_PACKET_BYTES:
        raise ConfigError(f"o pacote inicial passa de {MAX_PACKET_BYTES} bytes: {path}")
    return path.resolve(strict=True)


def _parse_delay(text: str) -> int:
    try:
        value = int((text or "50").strip() or "50")
    except ValueError as exc:
        raise ConfigError(f"delay inválido: {text!r}") from exc
    if not 0 <= value <= 1000:
        raise ConfigError("delay precisa estar entre 0 e 1000 milissegundos")
    return value


def _resolve_optional_path(text: str, base: Path) -> Path | None:
    text = (text or "").strip()
    if not text:
        return None
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate


def _read_small_file(path: Path) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ConfigError(f"não foi possível ler {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ConfigError(f"{path} precisa ser um arquivo comum")
    if info.st_size > MAX_CONFIG_BYTES:
        raise ConfigError(f"{path} passa de 64 KiB")
    return path.read_bytes()


def _warn_or_fix_permissions(path: Path) -> None:
    """Um INI com senha não deve ficar legível para os outros usuários."""
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    if mode & 0o077:
        try:
            os.chmod(path, 0o600)
        except OSError as exc:  # pragma: no cover - depende do sistema de arquivos
            raise ConfigError(
                f"{path} tem senha e está legível por outros usuários; "
                f"ajuste para 0600 (chmod 600 {path}): {exc}"
            ) from exc
