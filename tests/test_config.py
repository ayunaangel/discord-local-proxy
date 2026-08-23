from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from discord_local_proxy.config import (
    AppConfig,
    ConfigError,
    ConfigPermissionError,
    MAX_VOICE_PACKET_BYTES,
    ProxySettings,
    VoiceSettings,
    load_config,
    save_config,
)


class ProxySettingsValidationTests(unittest.TestCase):
    def test_rejects_invalid_proxy_inputs(self) -> None:
        invalid_settings = (
            {"kind": "ftp", "host": "proxy.example", "port": 8080},
            {"kind": "http", "host": "https://proxy.example", "port": 8080},
            {"kind": "http", "host": "proxy.example", "port": 0},
            {"kind": "http", "host": "proxy.example", "port": 65536},
            {
                "kind": "socks5",
                "host": "proxy.example",
                "port": 1080,
                "password": "line\nbreak",
            },
            {
                "kind": "http",
                "host": "proxy.example",
                "port": 8080,
                "password": "secret",
                "password_env": "PROXY_PASSWORD",
            },
            {"kind": "none", "host": "proxy.example"},
        )

        for values in invalid_settings:
            with self.subTest(values=values), self.assertRaises(ConfigError):
                ProxySettings(**values)

    def test_environment_password_must_exist(self) -> None:
        settings = ProxySettings(
            kind="socks5",
            host="127.0.0.1",
            port=1080,
            username="alice",
            password_env="DISCORD_TEST_PROXY_PASSWORD",
        )

        self.assertEqual(
            settings.resolved_password({"DISCORD_TEST_PROXY_PASSWORD": "secret"}),
            "secret",
        )
        with self.assertRaises(ConfigError):
            settings.resolved_password({})


class ConfigFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)

    def test_load_rejects_unknown_ini_keys(self) -> None:
        path = self.root / "discord-local-proxy.ini"
        path.write_text(
            "[proxy]\n"
            "type = http\n"
            "host = proxy.example\n"
            "port = 8080\n"
            "surprise = enabled\n",
            encoding="utf-8",
        )
        path.chmod(0o600)

        with self.assertRaises(ConfigError):
            load_config(path)

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are not enforced on Windows")
    def test_load_rejects_world_readable_literal_password(self) -> None:
        path = self.root / "discord-local-proxy.ini"
        path.write_text(
            "[proxy]\n"
            "type = http\n"
            "host = 127.0.0.1\n"
            "port = 8080\n"
            "password = top-secret\n",
            encoding="utf-8",
        )
        path.chmod(0o644)

        with self.assertRaises(ConfigPermissionError):
            load_config(path)

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are not enforced on Windows")
    def test_save_round_trips_and_uses_user_only_permissions(self) -> None:
        path = self.root / "nested" / "discord-local-proxy.ini"
        packet_file = self.root / "voice-packet.bin"
        packet_file.write_bytes(b"custom UDP prelude")
        expected = AppConfig(
            proxy=ProxySettings(
                kind="socks5",
                host="127.0.0.1",
                port=1080,
                username="alice",
                password="top-secret",
                connect_timeout=4.5,
            ),
            voice=VoiceSettings(
                enabled=True,
                delay_ms=75,
                packet_file=packet_file,
            ),
            executable=self.root / "Discord",
        )

        save_config(path, expected)

        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(load_config(path), expected)

    def test_voice_packet_must_be_a_nonempty_bounded_regular_file(self) -> None:
        empty = self.root / "empty.bin"
        empty.write_bytes(b"")
        with self.assertRaisesRegex(ConfigError, "entre 1"):
            VoiceSettings(packet_file=empty)

        oversized = self.root / "oversized.bin"
        oversized.write_bytes(b"x" * (MAX_VOICE_PACKET_BYTES + 1))
        with self.assertRaisesRegex(ConfigError, "entre 1"):
            VoiceSettings(packet_file=oversized)

        target = self.root / "packet.bin"
        target.write_bytes(b"packet")
        link = self.root / "packet-link.bin"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            return
        with self.assertRaisesRegex(ConfigError, "arquivo regular"):
            VoiceSettings(packet_file=link)

    def test_blank_packet_setting_detects_compatible_adjacent_filename(self) -> None:
        path = self.root / "discord-local-proxy.ini"
        path.write_text(
            "[proxy]\ntype = none\n[voice]\npacket_file =\n",
            encoding="utf-8",
        )
        path.chmod(0o600)
        packet = self.root / "drover-packet.bin"
        packet.write_bytes(b"compatible packet")

        self.assertEqual(load_config(path).voice.packet_file, packet.absolute())

    def test_failed_atomic_replace_preserves_old_file_and_removes_temporary_file(self) -> None:
        path = self.root / "discord-local-proxy.ini"
        original = b"original configuration\n"
        path.write_bytes(original)
        path.chmod(0o600)
        replacement = AppConfig(voice=VoiceSettings(enabled=False, delay_ms=0))

        with patch(
            "discord_local_proxy.config.os.replace",
            side_effect=OSError("simulated replace failure"),
        ):
            with self.assertRaises(OSError):
                save_config(path, replacement)

        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(self.root.glob(f".{path.name}.*.tmp")), [])

    def test_load_rejects_symlinked_config(self) -> None:
        target = self.root / "target.ini"
        target.write_text("[proxy]\ntype = none\n", encoding="utf-8")
        target.chmod(0o600)
        link = self.root / "discord-local-proxy.ini"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links are unavailable")

        with self.assertRaises(ConfigError):
            load_config(link)


if __name__ == "__main__":
    unittest.main()
