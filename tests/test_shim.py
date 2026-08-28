"""Teste de verdade do componente nativo, quando ele já estiver compilado.

Roda um processo com o shim carregado, manda um pacote de 74 bytes com a
assinatura da descoberta de IP e confere que o preparo (`0x00`, `0x01`) chegou
antes dele. Se `build/libdiscordproxy.so` não existir, o teste é pulado — rode
`python build.py` primeiro.
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHIM = ROOT / "build" / "libdiscordproxy.so"

SENDER = r"""
import socket, sys
port = int(sys.argv[1])
packet = bytes([0x00, 0x01, 0x00, 0x46]) + bytes(70)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.sendto(packet, ("127.0.0.1", port))
sock.close()
"""


@unittest.skipUnless(sys.platform.startswith("linux"), "o shim POSIX é só para Linux")
@unittest.skipUnless(SHIM.is_file(), "componente nativo ainda não compilado")
class NativeShim(unittest.TestCase):
    def setUp(self):
        self.receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.receiver.bind(("127.0.0.1", 0))
        self.receiver.settimeout(5)
        self.port = self.receiver.getsockname()[1]
        self.addCleanup(self.receiver.close)

    def _send_with_shim(self, environment_extra: dict) -> list[bytes]:
        environment = dict(os.environ)
        environment["LD_PRELOAD"] = str(SHIM)
        environment.update(environment_extra)
        subprocess.run(
            [sys.executable, "-c", SENDER, str(self.port)],
            env=environment,
            check=True,
            timeout=30,
        )
        received = []
        while True:
            try:
                data, _ = self.receiver.recvfrom(65535)
            except socket.timeout:
                break
            received.append(data)
            if len(data) == 74:
                break
        return received

    def test_priming_comes_before_the_real_packet(self):
        received = self._send_with_shim({"DISCORD_PROXY_VOICE": "1", "DISCORD_PROXY_DELAY": "1"})
        self.assertEqual(received[:2], [b"\x00", b"\x01"])
        self.assertEqual(len(received[2]), 74)

    def test_disabled_voice_sends_only_the_real_packet(self):
        received = self._send_with_shim({"DISCORD_PROXY_VOICE": "0"})
        self.assertEqual(len(received), 1)
        self.assertEqual(len(received[0]), 74)

    def test_custom_packet_goes_first(self):
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as handle:
            handle.write(b"OLA-MUNDO")
            packet_path = handle.name
        self.addCleanup(os.unlink, packet_path)
        received = self._send_with_shim(
            {
                "DISCORD_PROXY_VOICE": "1",
                "DISCORD_PROXY_DELAY": "1",
                "DISCORD_PROXY_PACKET": packet_path,
            }
        )
        self.assertEqual(received[0], b"OLA-MUNDO")
        self.assertEqual(received[1:3], [b"\x00", b"\x01"])

    def test_accented_path_survives(self):
        directory = tempfile.mkdtemp(prefix="ação-çãõ-")
        self.addCleanup(shutil.rmtree, directory, True)
        packet_path = Path(directory) / "pacote-início.bin"
        packet_path.write_bytes(b"ACENTO")
        received = self._send_with_shim(
            {
                "DISCORD_PROXY_VOICE": "1",
                "DISCORD_PROXY_DELAY": "1",
                "DISCORD_PROXY_PACKET": str(packet_path),
            }
        )
        self.assertEqual(received[0], b"ACENTO")

    def test_config_file_is_read_when_there_is_no_environment_override(self):
        with tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False) as handle:
            handle.write("[discord-proxy]\nvoice = off\n")
            ini_path = handle.name
        self.addCleanup(os.unlink, ini_path)
        environment = dict(os.environ)
        environment["LD_PRELOAD"] = str(SHIM)
        environment["DISCORD_PROXY_INI"] = ini_path
        for name in ("DISCORD_PROXY_VOICE", "DISCORD_PROXY_DELAY", "DISCORD_PROXY_PACKET"):
            environment.pop(name, None)
        subprocess.run(
            [sys.executable, "-c", SENDER, str(self.port)],
            env=environment,
            check=True,
            timeout=30,
        )
        data, _ = self.receiver.recvfrom(65535)
        self.assertEqual(len(data), 74)


if __name__ == "__main__":
    unittest.main()
