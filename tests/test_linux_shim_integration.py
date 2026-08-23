from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(sys.platform.startswith("linux"), "integração específica do Linux")
class LinuxShimIntegrationTests(unittest.TestCase):
    def test_first_discovery_packet_gets_two_probes_and_second_does_not(self) -> None:
        configured = os.environ.get("DLP_LINUX_SHIM")
        if not configured:
            self.skipTest("DLP_LINUX_SHIM não foi definido")
        shim = Path(configured)
        if not shim.is_file():
            self.fail(f"shim não encontrado: {shim}")

        listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.settimeout(5)
        host, port = listener.getsockname()
        packet = b"\x00\x01\x00\x46" + b"\x00" * 70
        child_code = (
            "import socket,sys;"
            "p=b'\\x00\\x01\\x00\\x46'+b'\\x00'*70;"
            "s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);"
            "d=(sys.argv[1],int(sys.argv[2]));"
            "s.sendto(p,d);s.sendto(p,d);s.close()"
        )
        environment = dict(os.environ)
        environment.update(
            {
                "LD_PRELOAD": str(shim.resolve(strict=True)),
                "DISCORD_LOCAL_PROXY_VOICE_ENABLED": "1",
                "DISCORD_LOCAL_PROXY_VOICE_DELAY_MS": "0",
            }
        )
        child = subprocess.Popen(
            [sys.executable, "-c", child_code, host, str(port)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
        datagrams = [listener.recvfrom(128)[0] for _ in range(4)]
        stdout, stderr = child.communicate(timeout=10)

        self.assertEqual(child.returncode, 0, (stdout + stderr).decode("utf-8", "replace"))
        self.assertEqual(datagrams, [b"\x00", b"\x01", packet, packet])

    def test_custom_packet_is_sent_first_and_reread_for_a_new_socket(self) -> None:
        configured = os.environ.get("DLP_LINUX_SHIM")
        if not configured:
            self.skipTest("DLP_LINUX_SHIM não foi definido")
        shim = Path(configured)
        if not shim.is_file():
            self.fail(f"shim não encontrado: {shim}")

        with tempfile.TemporaryDirectory() as temporary_directory:
            packet_file = Path(temporary_directory) / "packet.bin"
            first_custom = b"first-custom-packet"
            second_custom = b"second-custom-packet"
            packet_file.write_bytes(first_custom)
            listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.addCleanup(listener.close)
            listener.bind(("127.0.0.1", 0))
            listener.settimeout(5)
            host, port = listener.getsockname()
            discovery = b"\x00\x01\x00\x46" + b"\x00" * 70
            child_code = (
                "import pathlib,socket,sys;"
                "p=b'\\x00\\x01\\x00\\x46'+b'\\x00'*70;"
                "d=(sys.argv[1],int(sys.argv[2]));"
                "s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);"
                "s.sendto(p,d);s.close();"
                "pathlib.Path(sys.argv[3]).write_bytes(b'second-custom-packet');"
                "s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);"
                "s.sendto(p,d);s.close()"
            )
            environment = dict(os.environ)
            environment.update(
                {
                    "LD_PRELOAD": str(shim.resolve(strict=True)),
                    "DISCORD_LOCAL_PROXY_VOICE_ENABLED": "1",
                    "DISCORD_LOCAL_PROXY_VOICE_DELAY_MS": "0",
                    "DISCORD_LOCAL_PROXY_VOICE_PACKET_FILE": str(packet_file),
                }
            )
            child = subprocess.Popen(
                [sys.executable, "-c", child_code, host, str(port), str(packet_file)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            )
            datagrams = [listener.recvfrom(128)[0] for _ in range(8)]
            stdout, stderr = child.communicate(timeout=10)

            self.assertEqual(
                child.returncode,
                0,
                (stdout + stderr).decode("utf-8", "replace"),
            )
            self.assertEqual(
                datagrams,
                [
                    first_custom,
                    b"\x00",
                    b"\x01",
                    discovery,
                    second_custom,
                    b"\x00",
                    b"\x01",
                    discovery,
                ],
            )


if __name__ == "__main__":
    unittest.main()
