from __future__ import annotations

import base64
import ipaddress
import select
import socket
import socketserver
import struct
import threading
from dataclasses import dataclass
from urllib.parse import urlsplit

from .config import ConfigError, ProxySettings


MAX_HEADER_BYTES = 64 * 1024
BUFFER_SIZE = 64 * 1024
RELAY_IDLE_SECONDS = 600


class ProxyError(ConnectionError):
    """The configured upstream proxy rejected or could not open a tunnel."""


@dataclass(frozen=True)
class ProxyProbe:
    ok: bool
    message: str


class _BridgeServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, settings: ProxySettings):
        self.settings = settings
        super().__init__(("127.0.0.1", 0), _BridgeHandler, bind_and_activate=True)


class LocalProxyBridge:
    """Loopback HTTP proxy that authenticates to an HTTP or SOCKS5 upstream."""

    def __init__(self, settings: ProxySettings):
        if not settings.enabled:
            raise ConfigError("o encaminhador local exige um proxy configurado")
        self.settings = settings
        self._server: _BridgeServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("encaminhador ainda não iniciado")
        return int(self._server.server_address[1])

    @property
    def proxy_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "LocalProxyBridge":
        if self._server is not None:
            return self
        server = _BridgeServer(self.settings)
        thread = threading.Thread(
            target=server.serve_forever,
            name="discord-local-proxy",
            daemon=True,
        )
        thread.start()
        self._server = server
        self._thread = thread
        return self

    def stop(self) -> None:
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=3)

    def __enter__(self) -> "LocalProxyBridge":
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.stop()


class _BridgeHandler(socketserver.BaseRequestHandler):
    server: _BridgeServer

    def handle(self) -> None:
        client: socket.socket = self.request
        client.settimeout(self.server.settings.connect_timeout)
        try:
            header, remainder = _read_header(client)
            method, target, version, headers = _parse_request(header)
            if method == "CONNECT":
                host, port = _parse_authority(target, default_port=443)
                upstream, upstream_remainder = open_tunnel(self.server.settings, host, port)
                try:
                    client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    if upstream_remainder:
                        client.sendall(upstream_remainder)
                    if remainder:
                        upstream.sendall(remainder)
                    _relay(client, upstream)
                finally:
                    upstream.close()
                return

            if method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
                raise ProxyError(f"método HTTP local não suportado: {method}")
            _forward_plain_http(
                client,
                self.server.settings,
                method,
                target,
                version,
                headers,
                remainder,
            )
        except (ProxyError, ConfigError, OSError, ValueError) as exc:
            _send_error(client, 502, _safe_error(exc))


def probe_proxy(
    settings: ProxySettings,
    *,
    target_host: str = "discord.com",
    target_port: int = 443,
) -> ProxyProbe:
    if not settings.enabled:
        return ProxyProbe(True, "Modo direto: nenhum proxy para testar.")
    try:
        sock, _ = open_tunnel(settings, target_host, target_port)
        sock.close()
        return ProxyProbe(True, f"Proxy respondeu e abriu um túnel para {target_host}:{target_port}.")
    except (ProxyError, OSError, ConfigError) as exc:
        return ProxyProbe(False, _safe_error(exc))


def open_tunnel(settings: ProxySettings, host: str, port: int) -> tuple[socket.socket, bytes]:
    if not settings.enabled:
        raise ProxyError("proxy não configurado")
    if settings.kind == "http":
        return _open_http_tunnel(settings, host, port)
    if settings.kind == "socks5":
        return _open_socks5_tunnel(settings, host, port), b""
    raise ProxyError(f"tipo de proxy não suportado: {settings.kind}")


def _open_http_tunnel(settings: ProxySettings, host: str, port: int) -> tuple[socket.socket, bytes]:
    sock = socket.create_connection((settings.host, settings.port), settings.connect_timeout)
    sock.settimeout(settings.connect_timeout)
    authority = _format_authority(host, port)
    lines = [
        f"CONNECT {authority} HTTP/1.1",
        f"Host: {authority}",
        "Proxy-Connection: Keep-Alive",
    ]
    auth = _basic_auth(settings)
    if auth:
        lines.append(f"Proxy-Authorization: Basic {auth}")
    request = ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")
    try:
        sock.sendall(request)
        response, remainder = _read_header(sock)
        first_line = response.split(b"\r\n", 1)[0].decode("iso-8859-1", "replace")
        parts = first_line.split(" ", 2)
        if len(parts) < 2 or not parts[1].isdigit():
            raise ProxyError("proxy HTTP enviou uma resposta inválida")
        status = int(parts[1])
        if not 200 <= status < 300:
            if status == 407:
                raise ProxyError("proxy HTTP recusou as credenciais (407)")
            raise ProxyError(f"proxy HTTP recusou o túnel ({status})")
        sock.settimeout(None)
        return sock, remainder
    except BaseException:
        sock.close()
        raise


def _open_socks5_tunnel(settings: ProxySettings, host: str, port: int) -> socket.socket:
    sock = socket.create_connection((settings.host, settings.port), settings.connect_timeout)
    sock.settimeout(settings.connect_timeout)
    password = settings.resolved_password()
    has_auth = bool(settings.username or password)
    # If credentials were explicitly configured, do not permit a silent
    # downgrade to the unauthenticated method.
    methods = b"\x02" if has_auth else b"\x00"
    try:
        sock.sendall(b"\x05" + bytes((len(methods),)) + methods)
        version, method = _recv_exact(sock, 2)
        if version != 5:
            raise ProxyError("proxy SOCKS5 respondeu com versão inválida")
        if method == 0xFF:
            raise ProxyError("proxy SOCKS5 não aceita os métodos de autenticação oferecidos")
        if method == 0x02:
            username = settings.username.encode("utf-8")
            encoded_password = password.encode("utf-8")
            if len(username) > 255 or len(encoded_password) > 255:
                raise ProxyError("credenciais SOCKS5 excedem 255 bytes")
            sock.sendall(
                b"\x01"
                + bytes((len(username),))
                + username
                + bytes((len(encoded_password),))
                + encoded_password
            )
            auth_version, status = _recv_exact(sock, 2)
            if auth_version != 1 or status != 0:
                raise ProxyError("proxy SOCKS5 recusou as credenciais")
        elif method != 0x00:
            raise ProxyError(f"método de autenticação SOCKS5 não suportado: {method}")

        address = _encode_socks_address(host)
        sock.sendall(b"\x05\x01\x00" + address + struct.pack("!H", port))
        version, reply, reserved, atyp = _recv_exact(sock, 4)
        if version != 5 or reserved != 0:
            raise ProxyError("proxy SOCKS5 enviou uma resposta inválida")
        if reply != 0:
            raise ProxyError(f"proxy SOCKS5 recusou o túnel (código {reply})")
        _discard_socks_address(sock, atyp)
        _recv_exact(sock, 2)
        sock.settimeout(None)
        return sock
    except BaseException:
        sock.close()
        raise


def _forward_plain_http(
    client: socket.socket,
    settings: ProxySettings,
    method: str,
    target: str,
    version: str,
    headers: list[tuple[str, str]],
    remainder: bytes,
) -> None:
    parsed = urlsplit(target)
    if parsed.scheme.lower() != "http" or not parsed.hostname:
        raise ProxyError("requisições sem CONNECT precisam usar uma URL http absoluta")
    host = parsed.hostname
    port = parsed.port or 80

    if settings.kind == "http":
        upstream = socket.create_connection((settings.host, settings.port), settings.connect_timeout)
        first_target = target
        extra = [("Proxy-Authorization", f"Basic {_basic_auth(settings)}")] if _basic_auth(settings) else []
    else:
        upstream = _open_socks5_tunnel(settings, host, port)
        first_target = parsed.path or "/"
        if parsed.query:
            first_target += f"?{parsed.query}"
        extra = []

    try:
        sanitized = [
            (name, value)
            for name, value in headers
            if name.lower() not in {"proxy-authorization", "proxy-connection", "connection"}
        ]
        sanitized.extend(extra)
        sanitized.append(("Connection", "close"))
        first = f"{method} {first_target} {version}\r\n".encode("iso-8859-1")
        block = first + b"".join(
            f"{name}: {value}\r\n".encode("iso-8859-1") for name, value in sanitized
        ) + b"\r\n"
        upstream.sendall(block + remainder)
        _relay(client, upstream)
    finally:
        upstream.close()


def _read_header(sock: socket.socket) -> tuple[bytes, bytes]:
    data = bytearray()
    while True:
        marker = data.find(b"\r\n\r\n")
        if marker >= 0:
            end = marker + 4
            return bytes(data[:end]), bytes(data[end:])
        if len(data) >= MAX_HEADER_BYTES:
            raise ProxyError("cabeçalho HTTP excede 64 KiB")
        chunk = sock.recv(min(8192, MAX_HEADER_BYTES + 1 - len(data)))
        if not chunk:
            raise ProxyError("conexão encerrada antes do cabeçalho HTTP")
        data.extend(chunk)


def _parse_request(header: bytes) -> tuple[str, str, str, list[tuple[str, str]]]:
    try:
        text = header.decode("iso-8859-1")
    except UnicodeDecodeError as exc:
        raise ProxyError("cabeçalho HTTP inválido") from exc
    lines = text[:-4].split("\r\n")
    first = lines[0].split(" ", 2)
    if len(first) != 3:
        raise ProxyError("linha de requisição HTTP inválida")
    method, target, version = first[0].upper(), first[1], first[2]
    if not version.startswith("HTTP/") or any(ord(char) < 32 for char in target):
        raise ProxyError("linha de requisição HTTP inválida")
    parsed: list[tuple[str, str]] = []
    for line in lines[1:]:
        if not line or line[0] in " \t" or ":" not in line:
            raise ProxyError("cabeçalho HTTP inválido")
        name, value = line.split(":", 1)
        if not name or not all(char.isalnum() or char in "!#$%&'*+-.^_`|~" for char in name):
            raise ProxyError("nome de cabeçalho HTTP inválido")
        if "\r" in value or "\n" in value or "\x00" in value:
            raise ProxyError("valor de cabeçalho HTTP inválido")
        parsed.append((name, value.lstrip(" \t")))
    return method, target, version, parsed


def _parse_authority(authority: str, *, default_port: int) -> tuple[str, int]:
    authority = authority.strip()
    if authority.startswith("["):
        end = authority.find("]")
        if end < 0:
            raise ProxyError("destino IPv6 inválido")
        host = authority[1:end]
        suffix = authority[end + 1 :]
        port = default_port if not suffix else int(suffix.removeprefix(":"), 10)
    elif authority.count(":") == 1:
        host, port_text = authority.rsplit(":", 1)
        port = int(port_text, 10)
    elif ":" in authority:
        host, port = authority, default_port
    else:
        host, port = authority, default_port
    if not host or not 1 <= port <= 65535:
        raise ProxyError("destino de proxy inválido")
    return host, port


def _format_authority(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def _basic_auth(settings: ProxySettings) -> str:
    password = settings.resolved_password()
    if not settings.username and not password:
        return ""
    token = f"{settings.username}:{password}".encode("utf-8")
    return base64.b64encode(token).decode("ascii")


def _encode_socks_address(host: str) -> bytes:
    bare = host.split("%", 1)[0]
    try:
        address = ipaddress.ip_address(bare)
    except ValueError:
        encoded = host.encode("idna")
        if len(encoded) > 255:
            raise ProxyError("hostname de destino longo demais para SOCKS5")
        return b"\x03" + bytes((len(encoded),)) + encoded
    if address.version == 4:
        return b"\x01" + address.packed
    return b"\x04" + address.packed


def _discard_socks_address(sock: socket.socket, atyp: int) -> None:
    if atyp == 1:
        _recv_exact(sock, 4)
    elif atyp == 4:
        _recv_exact(sock, 16)
    elif atyp == 3:
        length = _recv_exact(sock, 1)[0]
        _recv_exact(sock, length)
    else:
        raise ProxyError("proxy SOCKS5 retornou tipo de endereço inválido")


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise ProxyError("proxy encerrou a conexão durante o handshake")
        data.extend(chunk)
    return bytes(data)


def _relay(left: socket.socket, right: socket.socket) -> None:
    left.settimeout(None)
    right.settimeout(None)
    sockets = [left, right]
    while sockets:
        readable, _, _ = select.select(sockets, [], [], RELAY_IDLE_SECONDS)
        if not readable:
            return
        for source in readable:
            destination = right if source is left else left
            try:
                data = source.recv(BUFFER_SIZE)
            except (ConnectionResetError, OSError):
                return
            if not data:
                return
            try:
                destination.sendall(data)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return


def _send_error(client: socket.socket, status: int, message: str) -> None:
    body = (message + "\n").encode("utf-8", "replace")[:2048]
    response = (
        f"HTTP/1.1 {status} Bad Gateway\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii") + body
    try:
        client.sendall(response)
    except OSError:
        pass


def _safe_error(exc: BaseException) -> str:
    text = str(exc).replace("\r", " ").replace("\n", " ")
    return text[:512] or exc.__class__.__name__
