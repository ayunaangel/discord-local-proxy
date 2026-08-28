"""Ponte local em 127.0.0.1.

O Electron aceita `--proxy-server=host:porta`, mas não aceita usuário e senha
na URL e não fala SOCKS5 autenticado. A ponte resolve isso: o Discord fala com
um proxy HTTP sem senha no loopback, e a ponte repassa tudo para o proxy real
já autenticado — sem que a senha apareça em argumento, atalho ou log.
"""

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

from .config import Proxy

MAX_HEADER_BYTES = 64 * 1024
CHUNK = 64 * 1024
IDLE_SECONDS = 600
CONNECT_TIMEOUT = 10.0


class ProxyError(ConnectionError):
    """O proxy configurado recusou a conexão ou não respondeu."""


@dataclass(frozen=True)
class Probe:
    ok: bool
    message: str


def open_tunnel(proxy: Proxy, host: str, port: int) -> tuple[socket.socket, bytes]:
    """Abre um túnel até `host:porta` através do proxy configurado."""
    if not proxy.enabled:
        raise ProxyError("nenhum proxy configurado")
    if proxy.scheme == "http":
        return _http_tunnel(proxy, host, port)
    return _socks5_tunnel(proxy, host, port), b""


def test_proxy(proxy: Proxy, host: str = "discord.com", port: int = 443) -> Probe:
    """Confere o proxy antes de abrir o Discord, para não falhar no meio."""
    if not proxy.enabled:
        return Probe(True, "Modo direto: não há proxy para testar.")
    try:
        tunnel, _ = open_tunnel(proxy, host, port)
        tunnel.close()
    except (ProxyError, OSError) as exc:
        return Probe(False, _clean(exc))
    return Probe(True, f"O proxy respondeu e abriu um túnel até {host}:{port}.")


class Bridge:
    """Servidor de loopback com ciclo de vida ligado ao do Discord."""

    def __init__(self, proxy: Proxy):
        if not proxy.enabled:
            raise ProxyError("a ponte local só faz sentido com um proxy configurado")
        self.proxy = proxy
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("a ponte ainda não foi iniciada")
        return int(self._server.server_address[1])

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "Bridge":
        if self._server is None:
            self._server = _Server(self.proxy)
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                name="discord-proxy-bridge",
                daemon=True,
            )
            self._thread.start()
        return self

    def stop(self) -> None:
        server, thread = self._server, self._thread
        self._server = self._thread = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3)

    def __enter__(self) -> "Bridge":
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.stop()


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, proxy: Proxy):
        self.proxy = proxy
        super().__init__(("127.0.0.1", 0), _Handler)


class _Handler(socketserver.BaseRequestHandler):
    server: _Server

    def handle(self) -> None:
        client: socket.socket = self.request
        client.settimeout(CONNECT_TIMEOUT)
        try:
            header, extra = _read_header(client)
            method, target, version, headers = _parse_request(header)
            if method == "CONNECT":
                self._tunnel(client, target, extra)
            else:
                self._forward(client, method, target, version, headers, extra)
        except (ProxyError, OSError, ValueError) as exc:
            _send_error(client, _clean(exc))

    def _tunnel(self, client: socket.socket, target: str, extra: bytes) -> None:
        host, port = _split_authority(target, 443)
        upstream, leftover = open_tunnel(self.server.proxy, host, port)
        try:
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            if leftover:
                client.sendall(leftover)
            if extra:
                upstream.sendall(extra)
            _relay(client, upstream)
        finally:
            upstream.close()

    def _forward(
        self,
        client: socket.socket,
        method: str,
        target: str,
        version: str,
        headers: list[tuple[str, str]],
        extra: bytes,
    ) -> None:
        parsed = urlsplit(target)
        if parsed.scheme.lower() != "http" or not parsed.hostname:
            raise ProxyError("requisição sem CONNECT precisa de uma URL http absoluta")
        proxy = self.server.proxy
        port = parsed.port or 80

        if proxy.scheme == "http":
            upstream = socket.create_connection((proxy.host, proxy.port), CONNECT_TIMEOUT)
            line = target
            auth = _basic_auth(proxy)
            extra_headers = [("Proxy-Authorization", f"Basic {auth}")] if auth else []
        else:
            upstream = _socks5_tunnel(proxy, parsed.hostname, port)
            line = parsed.path or "/"
            if parsed.query:
                line += f"?{parsed.query}"
            extra_headers = []

        try:
            kept = [
                (name, value)
                for name, value in headers
                if name.lower() not in {"proxy-authorization", "proxy-connection", "connection"}
            ]
            kept.extend(extra_headers)
            kept.append(("Connection", "close"))
            block = f"{method} {line} {version}\r\n".encode("iso-8859-1")
            block += b"".join(f"{n}: {v}\r\n".encode("iso-8859-1") for n, v in kept) + b"\r\n"
            upstream.sendall(block + extra)
            _relay(client, upstream)
        finally:
            upstream.close()


def _http_tunnel(proxy: Proxy, host: str, port: int) -> tuple[socket.socket, bytes]:
    sock = socket.create_connection((proxy.host, proxy.port), CONNECT_TIMEOUT)
    sock.settimeout(CONNECT_TIMEOUT)
    authority = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    lines = [f"CONNECT {authority} HTTP/1.1", f"Host: {authority}", "Proxy-Connection: Keep-Alive"]
    auth = _basic_auth(proxy)
    if auth:
        lines.append(f"Proxy-Authorization: Basic {auth}")
    try:
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
        response, leftover = _read_header(sock)
        parts = response.split(b"\r\n", 1)[0].decode("iso-8859-1", "replace").split(" ", 2)
        if len(parts) < 2 or not parts[1].isdigit():
            raise ProxyError("o proxy HTTP respondeu algo que não é HTTP")
        status = int(parts[1])
        if status == 407:
            raise ProxyError("o proxy HTTP recusou usuário e senha (407)")
        if not 200 <= status < 300:
            raise ProxyError(f"o proxy HTTP recusou o túnel (código {status})")
        sock.settimeout(None)
        return sock, leftover
    except BaseException:
        sock.close()
        raise


def _socks5_tunnel(proxy: Proxy, host: str, port: int) -> socket.socket:
    sock = socket.create_connection((proxy.host, proxy.port), CONNECT_TIMEOUT)
    sock.settimeout(CONNECT_TIMEOUT)
    # Com credenciais configuradas, não aceitamos cair para "sem autenticação".
    methods = b"\x02" if proxy.has_auth else b"\x00"
    try:
        sock.sendall(b"\x05" + bytes((len(methods),)) + methods)
        version, method = _recv_exact(sock, 2)
        if version != 5:
            raise ProxyError("o proxy SOCKS5 respondeu com uma versão inválida")
        if method == 0xFF:
            raise ProxyError("o proxy SOCKS5 recusou o método de autenticação oferecido")
        if method == 0x02:
            user = proxy.user.encode("utf-8")
            password = proxy.password.encode("utf-8")
            sock.sendall(
                b"\x01" + bytes((len(user),)) + user + bytes((len(password),)) + password
            )
            auth_version, status = _recv_exact(sock, 2)
            if auth_version != 1 or status != 0:
                raise ProxyError("o proxy SOCKS5 recusou usuário e senha")
        elif method != 0x00:
            raise ProxyError(f"método SOCKS5 não suportado: {method}")

        sock.sendall(b"\x05\x01\x00" + _socks_address(host) + struct.pack("!H", port))
        version, reply, reserved, kind = _recv_exact(sock, 4)
        if version != 5 or reserved != 0:
            raise ProxyError("o proxy SOCKS5 enviou uma resposta inválida")
        if reply != 0:
            raise ProxyError(f"o proxy SOCKS5 recusou o túnel (código {reply})")
        _skip_socks_address(sock, kind)
        _recv_exact(sock, 2)
        sock.settimeout(None)
        return sock
    except BaseException:
        sock.close()
        raise


def _socks_address(host: str) -> bytes:
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        encoded = host.encode("idna")
        if len(encoded) > 255:
            raise ProxyError("o destino é longo demais para SOCKS5") from None
        return b"\x03" + bytes((len(encoded),)) + encoded
    return (b"\x01" if address.version == 4 else b"\x04") + address.packed


def _skip_socks_address(sock: socket.socket, kind: int) -> None:
    if kind == 1:
        _recv_exact(sock, 4)
    elif kind == 4:
        _recv_exact(sock, 16)
    elif kind == 3:
        _recv_exact(sock, _recv_exact(sock, 1)[0])
    else:
        raise ProxyError("o proxy SOCKS5 devolveu um tipo de endereço inválido")


def _basic_auth(proxy: Proxy) -> str:
    if not proxy.has_auth:
        return ""
    token = f"{proxy.user}:{proxy.password}".encode("utf-8")
    return base64.b64encode(token).decode("ascii")


def _read_header(sock: socket.socket) -> tuple[bytes, bytes]:
    data = bytearray()
    while True:
        marker = data.find(b"\r\n\r\n")
        if marker >= 0:
            end = marker + 4
            return bytes(data[:end]), bytes(data[end:])
        if len(data) >= MAX_HEADER_BYTES:
            raise ProxyError("cabeçalho HTTP maior que 64 KiB")
        chunk = sock.recv(min(8192, MAX_HEADER_BYTES + 1 - len(data)))
        if not chunk:
            raise ProxyError("a conexão caiu antes do fim do cabeçalho")
        data.extend(chunk)


def _parse_request(header: bytes) -> tuple[str, str, str, list[tuple[str, str]]]:
    lines = header.decode("iso-8859-1")[:-4].split("\r\n")
    first = lines[0].split(" ", 2)
    if len(first) != 3 or not first[2].startswith("HTTP/"):
        raise ProxyError("linha de requisição HTTP inválida")
    method, target, version = first[0].upper(), first[1], first[2]
    if any(ord(char) < 32 for char in target):
        raise ProxyError("destino HTTP inválido")
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        if not line or line[0] in " \t" or ":" not in line:
            raise ProxyError("cabeçalho HTTP inválido")
        name, value = line.split(":", 1)
        if not name or any(char in value for char in "\r\n\x00"):
            raise ProxyError("cabeçalho HTTP inválido")
        headers.append((name, value.lstrip(" \t")))
    return method, target, version, headers


def _split_authority(authority: str, default_port: int) -> tuple[str, int]:
    authority = authority.strip()
    if authority.startswith("["):
        end = authority.find("]")
        if end < 0:
            raise ProxyError("destino IPv6 inválido")
        host = authority[1:end]
        rest = authority[end + 1 :]
        port = int(rest[1:]) if rest.startswith(":") else default_port
    elif authority.count(":") == 1:
        host, _, text = authority.partition(":")
        port = int(text)
    else:
        host, port = authority, default_port
    if not host or not 1 <= port <= 65535:
        raise ProxyError("destino inválido")
    return host, port


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise ProxyError("o proxy encerrou a conexão durante o handshake")
        data.extend(chunk)
    return bytes(data)


def _relay(left: socket.socket, right: socket.socket) -> None:
    left.settimeout(None)
    right.settimeout(None)
    pair = [left, right]
    while True:
        readable, _, _ = select.select(pair, [], [], IDLE_SECONDS)
        if not readable:
            return
        for source in readable:
            destination = right if source is left else left
            try:
                data = source.recv(CHUNK)
                if not data:
                    return
                destination.sendall(data)
            except OSError:
                return


def _send_error(client: socket.socket, message: str) -> None:
    body = (message + "\n").encode("utf-8", "replace")[:2048]
    try:
        client.sendall(
            b"HTTP/1.1 502 Bad Gateway\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            + f"Content-Length: {len(body)}\r\n".encode("ascii")
            + b"Connection: close\r\n\r\n"
            + body
        )
    except OSError:
        pass


def _clean(exc: BaseException) -> str:
    text = str(exc).replace("\r", " ").replace("\n", " ").strip()
    return text[:400] or exc.__class__.__name__
