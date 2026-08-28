import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from discord_proxy import config as config_module
from discord_proxy.config import Config, ConfigError, parse_proxy


class ParseProxy(unittest.TestCase):
    def test_empty_means_direct(self):
        proxy = parse_proxy("   ")
        self.assertFalse(proxy.enabled)
        self.assertEqual(proxy.label, "direto (sem proxy)")

    def test_socks5_with_credentials(self):
        proxy = parse_proxy("socks5://ana:senha@10.0.0.5:1080")
        self.assertEqual(
            (proxy.scheme, proxy.host, proxy.port, proxy.user, proxy.password),
            ("socks5", "10.0.0.5", 1080, "ana", "senha"),
        )
        self.assertTrue(proxy.has_auth)

    def test_scheme_is_optional_and_https_becomes_http(self):
        self.assertEqual(parse_proxy("127.0.0.1:8080").scheme, "http")
        self.assertEqual(parse_proxy("https://127.0.0.1:8080").scheme, "http")
        self.assertEqual(parse_proxy("socks5h://127.0.0.1:1080").scheme, "socks5")

    def test_ipv6_keeps_the_address_without_brackets(self):
        proxy = parse_proxy("socks5://[::1]:1080")
        self.assertEqual(proxy.host, "::1")
        self.assertEqual(proxy.url, "socks5://[::1]:1080")

    def test_environment_placeholder(self):
        proxy = parse_proxy("socks5://ana:${SEGREDO}@host:1080", environ={"SEGREDO": "abc"})
        self.assertEqual(proxy.password, "abc")

    def test_missing_environment_variable_is_an_error(self):
        with self.assertRaises(ConfigError):
            parse_proxy("socks5://ana:${NAO_EXISTE_MESMO}@host:1080", environ={})

    def test_rejected_values(self):
        for text in ("banana", "socks4://1.2.3.4:1080", "http://host", "http://host:0"):
            with self.subTest(text=text), self.assertRaises(ConfigError):
                parse_proxy(text)

    def test_label_never_shows_the_password(self):
        self.assertNotIn("senha", parse_proxy("http://ana:senha@host:8080").label)


class SaveAndLoad(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "discord-proxy.ini"

    def tearDown(self):
        self.directory.cleanup()

    def test_round_trip(self):
        original = Config(
            proxy=parse_proxy("socks5://ana:senha@servidor:1080"),
            voice=False,
            delay_ms=120,
        )
        config_module.save(self.path, original)
        loaded = config_module.load(self.path)
        self.assertEqual(loaded.proxy.url, original.proxy.url)
        self.assertFalse(loaded.voice)
        self.assertEqual(loaded.delay_ms, 120)

    @unittest.skipIf(os.name == "nt", "permissões POSIX")
    def test_saved_file_is_private(self):
        config_module.save(self.path, Config())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    @unittest.skipIf(os.name == "nt", "permissões POSIX")
    def test_loose_permissions_are_tightened_when_there_is_a_password(self):
        self.path.write_text("[discord-proxy]\nproxy = http://ana:senha@host:8080\n")
        self.path.chmod(0o644)
        config_module.load(self.path)
        self.assertEqual(self.path.stat().st_mode & 0o077, 0)

    def test_drover_section_still_works(self):
        self.path.write_text("[drover]\nproxy = http://127.0.0.1:1080\n")
        self.assertEqual(config_module.load(self.path).proxy.port, 1080)

    def test_missing_file_gives_the_defaults(self):
        config = config_module.load_or_default(self.path)
        self.assertFalse(config.proxy.enabled)
        self.assertTrue(config.voice)

    def test_packet_must_be_a_real_file(self):
        self.path.write_text("[discord-proxy]\npacket = nao-existe.bin\n")
        with self.assertRaises(ConfigError):
            config_module.load(self.path)

    def test_packet_is_resolved_next_to_the_ini(self):
        packet = Path(self.directory.name) / "meu.bin"
        packet.write_bytes(b"\x01\x02\x03")
        self.path.write_text("[discord-proxy]\npacket = meu.bin\n")
        self.assertEqual(config_module.load(self.path).packet, packet.resolve())

    def test_delay_outside_the_range_is_rejected(self):
        self.path.write_text("[discord-proxy]\ndelay = 5000\n")
        with self.assertRaises(ConfigError):
            config_module.load(self.path)


if __name__ == "__main__":
    unittest.main()
