"""A ponte local testada contra proxies de mentira, mas que falam o protocolo."""

import base64
import socket
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from discord_proxy import bridge as bridge_module
from discord_proxy.config import parse_proxy


class FakeProxy(threading.Thread):
    """Servidor mínimo que aceita um túnel e depois devolve o que receber."""

    def __init__(self, kind: str, *, expect_auth: str | None = None, refuse: bool = False):
        super().__init__(daemon=True)
        self.kind = kind
        self.expect_auth = expect_auth
        self.refuse = refuse
        self.saw_auth: str | None = None
        self.target: tuple[str, int] | None = None
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(8)
        self.port = self.socket.getsockname()[1]
        self.stop_event = threading.Event()

    @property
    def url(self) -> str:
        return f"{self.kind}://127.0.0.1:{self.port}"

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                client, _ = self.socket.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def close(self) -> None:
        self.stop_event.set()
        self.socket.close()

    def _serve(self, client: socket.socket) -> None:
        try:
            if self.kind == "http":
                self._serve_http(client)
            else:
                self._serve_socks5(client)
        except OSError:
            pass
        finally:
            client.close()

    def _serve_http(self, client: socket.socket) -> None:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = client.recv(4096)
            if not chunk:
                return
            data += chunk
        header = data.decode("iso-8859-1")
        first = header.split("\r\n", 1)[0]
        assert first.startswith("CONNECT "), first
        authority = first.split(" ")[1]
        host, _, port = authority.rpartition(":")
        self.target = (host, int(port))
        for line in header.split("\r\n"):
            if line.lower().startswith("proxy-authorization:"):
                self.saw_auth = line.split(" ", 2)[-1]
        if self.refuse:
            client.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n")
            return
        client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        self._echo(client)

    def _serve_socks5(self, client: socket.socket) -> None:
        greeting = client.recv(512)
        assert greeting[0] == 5
        methods = set(greeting[2 : 2 + greeting[1]])
        if self.expect_auth is not None:
            assert 2 in methods, "a ponte deveria oferecer usuário e senha"
            client.sendall(b"\x05\x02")
            request = client.recv(512)
            user_length = request[1]
            user = request[2 : 2 + user_length].decode()
            password_length = request[2 + user_length]
            password = request[3 + user_length : 3 + user_length + password_length].decode()
            self.saw_auth = f"{user}:{password}"
            if self.refuse:
                client.sendall(b"\x01\x01")
                return
            client.sendall(b"\x01\x00")
        else:
            client.sendall(b"\x05\x00")

        request = client.recv(512)
        assert request[:3] == b"\x05\x01\x00"
        kind = request[3]
        if kind == 3:
            length = request[4]
            host = request[5 : 5 + length].decode()
            port = int.from_bytes(request[5 + length : 7 + length], "big")
        else:
            host = ".".join(str(byte) for byte in request[4:8])
            port = int.from_bytes(request[8:10], "big")
        self.target = (host, port)
        client.sendall(b"\x05\x00\x00\x01" + bytes(4) + (0).to_bytes(2, "big"))
        self._echo(client)

    def _echo(self, client: socket.socket) -> None:
        while True:
            data = client.recv(4096)
            if not data:
                return
            client.sendall(data)


def talk_through(bridge: bridge_module.Bridge, target: str = "discord.com:443") -> bytes:
    """Faz o que o Chromium faria: CONNECT na ponte e depois dados crus."""
    with socket.create_connection(("127.0.0.1", bridge.port), 5) as client:
        client.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = client.recv(4096)
            if not chunk:
                break
            response += chunk
        if b" 200 " not in response.split(b"\r\n", 1)[0]:
            return response
        client.sendall(b"ping")
        return response + client.recv(4096)


class HttpUpstream(unittest.TestCase):
    def test_tunnel_and_credentials(self):
        upstream = FakeProxy("http")
        upstream.start()
        self.addCleanup(upstream.close)
        proxy = parse_proxy(f"http://ana:senha@127.0.0.1:{upstream.port}")
        with bridge_module.Bridge(proxy) as bridge:
            answer = talk_through(bridge)
        self.assertIn(b"200 Connection Established", answer)
        self.assertTrue(answer.endswith(b"ping"))
        self.assertEqual(upstream.target, ("discord.com", 443))
        expected = base64.b64encode(b"ana:senha").decode()
        self.assertEqual(upstream.saw_auth, expected)

    def test_refused_credentials_do_not_open_a_tunnel(self):
        upstream = FakeProxy("http", refuse=True)
        upstream.start()
        self.addCleanup(upstream.close)
        proxy = parse_proxy(f"http://ana:errada@127.0.0.1:{upstream.port}")
        result = bridge_module.test_proxy(proxy, host="discord.com")
        self.assertFalse(result.ok)
        self.assertIn("407", result.message)


class Socks5Upstream(unittest.TestCase):
    def test_tunnel_without_credentials(self):
        upstream = FakeProxy("socks5")
        upstream.start()
        self.addCleanup(upstream.close)
        proxy = parse_proxy(f"socks5://127.0.0.1:{upstream.port}")
        with bridge_module.Bridge(proxy) as bridge:
            answer = talk_through(bridge)
        self.assertIn(b"200 Connection Established", answer)
        self.assertTrue(answer.endswith(b"ping"))
        self.assertEqual(upstream.target, ("discord.com", 443))

    def test_username_and_password(self):
        upstream = FakeProxy("socks5", expect_auth="ana:senha")
        upstream.start()
        self.addCleanup(upstream.close)
        proxy = parse_proxy(f"socks5://ana:senha@127.0.0.1:{upstream.port}")
        with bridge_module.Bridge(proxy) as bridge:
            talk_through(bridge)
        self.assertEqual(upstream.saw_auth, "ana:senha")

    def test_rejected_credentials(self):
        upstream = FakeProxy("socks5", expect_auth="ana:senha", refuse=True)
        upstream.start()
        self.addCleanup(upstream.close)
        proxy = parse_proxy(f"socks5://ana:errada@127.0.0.1:{upstream.port}")
        result = bridge_module.test_proxy(proxy)
        self.assertFalse(result.ok)
        self.assertIn("recusou", result.message)


class BridgeBasics(unittest.TestCase):
    def test_listens_only_on_loopback(self):
        upstream = FakeProxy("socks5")
        upstream.start()
        self.addCleanup(upstream.close)
        with bridge_module.Bridge(parse_proxy(f"socks5://127.0.0.1:{upstream.port}")) as bridge:
            self.assertTrue(bridge.url.startswith("http://127.0.0.1:"))
            host = bridge._server.server_address[0]
        self.assertEqual(host, "127.0.0.1")

    def test_direct_mode_needs_no_bridge(self):
        with self.assertRaises(bridge_module.ProxyError):
            bridge_module.Bridge(parse_proxy(""))
        self.assertTrue(bridge_module.test_proxy(parse_proxy("")).ok)

    def test_dead_upstream_is_reported(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            dead_port = probe.getsockname()[1]
        result = bridge_module.test_proxy(parse_proxy(f"http://127.0.0.1:{dead_port}"))
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()


class Journal(unittest.TestCase):
    """A ponte anota o destino de cada túnel que abre."""

    def test_targets_are_recorded(self):
        import tempfile
        from pathlib import Path

        upstream = FakeProxy("socks5")
        upstream.start()
        self.addCleanup(upstream.close)
        journal = Path(tempfile.mkdtemp()) / "targets.txt"
        proxy = parse_proxy(f"socks5://127.0.0.1:{upstream.port}")
        with bridge_module.Bridge(proxy, journal=journal) as bridge:
            talk_through(bridge, "rotterdam1234.discord.media:443")
        self.assertIn("rotterdam1234.discord.media:443", journal.read_text())

    def test_without_a_journal_nothing_is_written(self):
        upstream = FakeProxy("socks5")
        upstream.start()
        self.addCleanup(upstream.close)
        proxy = parse_proxy(f"socks5://127.0.0.1:{upstream.port}")
        with bridge_module.Bridge(proxy) as bridge:
            answer = talk_through(bridge)
        self.assertIn(b"200 Connection Established", answer)
