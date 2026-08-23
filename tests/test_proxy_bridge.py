from __future__ import annotations

import base64
import ipaddress
import socket
import struct
import threading
import unittest
from collections.abc import Callable

from discord_local_proxy.config import ProxySettings
from discord_local_proxy.proxy_bridge import LocalProxyBridge


def _recv_exact(connection: socket.socket, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        chunk = connection.recv(length - len(data))
        if not chunk:
            raise ConnectionError("fixture peer closed early")
        data.extend(chunk)
    return bytes(data)


def _recv_header(connection: socket.socket) -> tuple[bytes, bytes]:
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = connection.recv(4096)
        if not chunk:
            raise ConnectionError("fixture peer closed before the HTTP header")
        data.extend(chunk)
    end = data.index(b"\r\n\r\n") + 4
    return bytes(data[:end]), bytes(data[end:])


class _OneShotTCPServer:
    def __init__(self, handler: Callable[[socket.socket], None]) -> None:
        self._handler = handler
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        self._listener.settimeout(3)
        self.port = int(self._listener.getsockname()[1])
        self.errors: list[BaseException] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            connection, _ = self._listener.accept()
            with connection:
                connection.settimeout(3)
                self._handler(connection)
        except BaseException as exc:
            self.errors.append(exc)
        finally:
            self._listener.close()

    def wait(self) -> None:
        self._thread.join(timeout=4)
        if self._thread.is_alive():
            raise AssertionError("fake proxy server did not finish")
        if self.errors:
            raise self.errors[0]

    def close(self) -> None:
        try:
            self._listener.close()
        except OSError:
            pass
        self._thread.join(timeout=4)


class LocalProxyBridgeHandshakeTests(unittest.TestCase):
    def test_http_upstream_receives_authenticated_connect_and_relays_bytes(self) -> None:
        observed: dict[str, bytes] = {}

        def serve_http_proxy(connection: socket.socket) -> None:
            request, remainder = _recv_header(connection)
            observed["request"] = request
            observed["initial_payload"] = remainder
            connection.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            payload = _recv_exact(connection, len(b"ping-http"))
            observed["payload"] = payload
            connection.sendall(b"echo:" + payload)

        upstream = _OneShotTCPServer(serve_http_proxy)
        self.addCleanup(upstream.close)
        settings = ProxySettings(
            kind="http",
            host="127.0.0.1",
            port=upstream.port,
            username="alice",
            password="secret",
            connect_timeout=2,
        )

        with LocalProxyBridge(settings) as bridge:
            with socket.create_connection(("127.0.0.1", bridge.port), timeout=2) as client:
                client.settimeout(2)
                client.sendall(
                    b"CONNECT gateway.discord.gg:443 HTTP/1.1\r\n"
                    b"Host: gateway.discord.gg:443\r\n\r\n"
                )
                response, remainder = _recv_header(client)
                self.assertTrue(response.startswith(b"HTTP/1.1 200 "))
                self.assertEqual(remainder, b"")
                client.sendall(b"ping-http")
                self.assertEqual(_recv_exact(client, len(b"echo:ping-http")), b"echo:ping-http")

        upstream.wait()
        expected_auth = base64.b64encode(b"alice:secret")
        self.assertIn(b"CONNECT gateway.discord.gg:443 HTTP/1.1\r\n", observed["request"])
        self.assertIn(b"Host: gateway.discord.gg:443\r\n", observed["request"])
        self.assertIn(b"Proxy-Authorization: Basic " + expected_auth + b"\r\n", observed["request"])
        self.assertEqual(observed["initial_payload"], b"")
        self.assertEqual(observed["payload"], b"ping-http")

    def test_socks5_upstream_authenticates_connects_by_hostname_and_relays_bytes(self) -> None:
        observed: dict[str, object] = {}

        def serve_socks5_proxy(connection: socket.socket) -> None:
            version, method_count = _recv_exact(connection, 2)
            methods = _recv_exact(connection, method_count)
            observed["greeting"] = (version, methods)
            connection.sendall(b"\x05\x02")

            auth_version, username_length = _recv_exact(connection, 2)
            username = _recv_exact(connection, username_length)
            password_length = _recv_exact(connection, 1)[0]
            password = _recv_exact(connection, password_length)
            observed["auth"] = (auth_version, username, password)
            connection.sendall(b"\x01\x00")

            request_version, command, reserved, address_type = _recv_exact(connection, 4)
            if address_type == 1:
                host = str(ipaddress.ip_address(_recv_exact(connection, 4)))
            elif address_type == 3:
                host_length = _recv_exact(connection, 1)[0]
                host = _recv_exact(connection, host_length).decode("idna")
            elif address_type == 4:
                host = str(ipaddress.ip_address(_recv_exact(connection, 16)))
            else:
                raise AssertionError(f"unexpected SOCKS5 address type: {address_type}")
            port = struct.unpack("!H", _recv_exact(connection, 2))[0]
            observed["connect"] = (
                request_version,
                command,
                reserved,
                address_type,
                host,
                port,
            )
            connection.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x9c\x40")

            payload = _recv_exact(connection, len(b"ping-socks"))
            observed["payload"] = payload
            connection.sendall(b"echo:" + payload)

        upstream = _OneShotTCPServer(serve_socks5_proxy)
        self.addCleanup(upstream.close)
        settings = ProxySettings(
            kind="socks5",
            host="127.0.0.1",
            port=upstream.port,
            username="bob",
            password="socks-secret",
            connect_timeout=2,
        )

        with LocalProxyBridge(settings) as bridge:
            with socket.create_connection(("127.0.0.1", bridge.port), timeout=2) as client:
                client.settimeout(2)
                client.sendall(
                    b"CONNECT voice.discord.media:8443 HTTP/1.1\r\n"
                    b"Host: voice.discord.media:8443\r\n\r\n"
                )
                response, remainder = _recv_header(client)
                self.assertTrue(response.startswith(b"HTTP/1.1 200 "))
                self.assertEqual(remainder, b"")
                client.sendall(b"ping-socks")
                self.assertEqual(
                    _recv_exact(client, len(b"echo:ping-socks")),
                    b"echo:ping-socks",
                )

        upstream.wait()
        self.assertEqual(observed["greeting"], (5, b"\x02"))
        self.assertEqual(observed["auth"], (1, b"bob", b"socks-secret"))
        self.assertEqual(
            observed["connect"],
            (5, 1, 0, 3, "voice.discord.media", 8443),
        )
        self.assertEqual(observed["payload"], b"ping-socks")


if __name__ == "__main__":
    unittest.main()
