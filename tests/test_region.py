"""Descoberta de para onde a chamada está indo."""

import ipaddress
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from discord_proxy import region as region_module
from discord_proxy.region import Endpoint

_original_state = region_module.state_path

HOLDER = r"""
import socket, sys, time
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.connect(("93.184.216.34", 50007))
print("pronto", flush=True)
time.sleep(int(sys.argv[1]))
"""


class Naming(unittest.TestCase):
    def test_current_format_uses_the_airport_code(self):
        # Foi o que o Discord entregou numa chamada real de teste.
        endpoint = Endpoint("", 2053, "c-iad10-b19ce4e8.discord.media")
        self.assertEqual(endpoint.code, "iad")
        self.assertIn("Washington", endpoint.region)

    def test_brazilian_datacenter(self):
        self.assertIn("São Paulo", Endpoint("", 443, "c-gru05-4f2c1a9b.discord.media").region)

    def test_unknown_airport_falls_back_to_the_code(self):
        self.assertEqual(Endpoint("", 443, "c-xyz01-abcdef12.discord.media").region, "xyz")

    def test_old_format_still_works(self):
        endpoint = Endpoint("1.2.3.4", 50001, "brazil11111.discord.media")
        self.assertEqual(endpoint.code, "brazil")

    def test_region_without_digits(self):
        self.assertEqual(Endpoint("1.2.3.4", 1, "rotterdam.discord.media").code, "rotterdam")

    def test_no_hostname_means_no_region(self):
        endpoint = Endpoint("1.2.3.4", 50001)
        self.assertEqual(endpoint.region, "")
        self.assertIn("região desconhecida", str(endpoint))

    def test_text_shows_the_region(self):
        self.assertIn("Washington", str(Endpoint("", 2053, "c-iad10-b19ce4e8.discord.media")))


class ProcAddresses(unittest.TestCase):
    def test_ipv4_is_little_endian_hex(self):
        # 0100007F:14E9 -> 127.0.0.1:5353
        endpoint = region_module._parse_proc_address("0100007F:14E9", 4)
        self.assertIsNone(endpoint, "loopback deve ser descartado")

    def test_public_ipv4(self):
        packed = ipaddress.IPv4Address("93.184.216.34").packed
        field = packed[::-1].hex().upper() + ":C357"
        endpoint = region_module._parse_proc_address(field, 4)
        self.assertEqual((endpoint.address, endpoint.port), ("93.184.216.34", 0xC357))

    def test_port_zero_is_not_a_destination(self):
        self.assertIsNone(region_module._parse_proc_address("00000000:0000", 4))

    def test_private_ranges_are_ignored(self):
        for text in ("192.168.0.10", "10.1.2.3", "203.0.113.7"):
            with self.subTest(address=text):
                field = ipaddress.IPv4Address(text).packed[::-1].hex().upper() + ":C357"
                self.assertIsNone(region_module._parse_proc_address(field, 4))

    def test_quic_on_udp_443_is_not_voice(self):
        field = ipaddress.IPv4Address("93.184.216.34").packed[::-1].hex().upper() + ":01BB"
        self.assertIsNone(region_module._parse_proc_address(field, 4))

    def test_ipv6(self):
        # 2001:db8::/32 é faixa de documentação e conta como privada, então o
        # endereço aqui precisa ser um global de verdade.
        packed = ipaddress.IPv6Address("2606:4700:4700::1111").packed
        field = b"".join(packed[i : i + 4][::-1] for i in range(0, 16, 4)).hex().upper() + ":C357"
        endpoint = region_module._parse_proc_address(field, 16)
        self.assertEqual(endpoint.address, "2606:4700:4700::1111")


class StateFile(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "voice-endpoint.txt"
        self.previous = region_module.state_path
        region_module.state_path = lambda: self.path

    def tearDown(self):
        region_module.state_path = self.previous
        self.directory.cleanup()

    def test_reads_the_most_recent_first(self):
        self.path.write_text("93.184.216.34:50007\n198.51.100.9:50009\n")
        found = region_module._from_state_file()
        self.assertEqual([item.address for item in found], ["198.51.100.9", "93.184.216.34"])

    def test_repeated_lines_appear_once(self):
        self.path.write_text("93.184.216.34:50007\n" * 5)
        self.assertEqual(len(region_module._from_state_file()), 1)

    def test_garbage_lines_are_skipped(self):
        self.path.write_text("lixo\n\nnão-é-ip:99\n93.184.216.34:50007\n")
        found = region_module._from_state_file()
        self.assertEqual([item.address for item in found], ["93.184.216.34"])

    def test_missing_file_is_not_an_error(self):
        self.assertEqual(region_module._from_state_file(), [])


@unittest.skipUnless(sys.platform.startswith("linux"), "leitura do /proc é só no Linux")
class ProcDiscovery(unittest.TestCase):
    """Sobe um processo chamado `discord` com um socket UDP e o encontra."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.fake = Path(self.directory.name) / "discord"
        shutil.copy2(sys.executable, self.fake)
        self.fake.chmod(0o755)
        self.process = subprocess.Popen(
            [str(self.fake), "-c", HOLDER, "20"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.addCleanup(self._stop)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if (self.process.stdout or None) and self.process.stdout.readline().strip() == "pronto":
                return
            if self.process.poll() is not None:
                break
        self.skipTest("o processo de mentira não subiu")

    def _stop(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.directory.cleanup()

    def test_finds_the_udp_destination(self):
        found = region_module._from_proc(None)
        addresses = {item.address for item in found}
        self.assertIn("93.184.216.34", addresses)
        chosen = next(item for item in found if item.address == "93.184.216.34")
        self.assertEqual(chosen.port, 50007)

    def test_voice_ports_are_listed_first(self):
        region_module.state_path = lambda: Path("/caminho/que/nao/existe")
        try:
            found = region_module.voice_endpoints(resolve=False)
        finally:
            region_module.state_path = _original_state
        if len(found) > 1:
            ports = [item.port for item in found]
            in_range = [port in region_module.VOICE_PORT_RANGE for port in ports]
            self.assertEqual(in_range, sorted(in_range, reverse=True))


class Places(unittest.TestCase):
    def test_place_from_payload(self):
        place = region_module._place_from(
            {"ip": "1.2.3.4", "city": "Amsterdam", "region": "North Holland", "country": "NL"}
        )
        self.assertIn("Amsterdam", str(place))
        self.assertIn("NL", str(place))

    def test_place_without_details(self):
        self.assertIn("local desconhecido", str(region_module._place_from({"ip": "1.2.3.4"})))

    def test_locate_refuses_something_that_is_not_an_ip(self):
        with self.assertRaises(ValueError):
            region_module.locate("nao-e-ip")


if __name__ == "__main__":
    unittest.main()


class BridgeJournal(unittest.TestCase):
    """Com proxy, o destino da mídia vem da ponte — com nome e tudo."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.journal = Path(self.directory.name) / "bridge-targets.txt"
        self.previous_journal = region_module.journal_path
        self.previous_state = region_module.state_path
        region_module.journal_path = lambda: self.journal
        region_module.state_path = lambda: Path(self.directory.name) / "nao-existe"

    def tearDown(self):
        region_module.journal_path = self.previous_journal
        region_module.state_path = self.previous_state
        self.directory.cleanup()

    def test_only_media_servers_count(self):
        self.journal.write_text(
            "cdn.discordapp.com:443\n"
            "rotterdam1234.discord.media:443\n"
            "discord.com:443\n"
            "ipinfo.io:443\n"
        )
        found = region_module.voice_endpoints()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].hostname, "rotterdam1234.discord.media")
        self.assertEqual(found[0].code, "rotterdam")

    def test_most_recent_first(self):
        self.journal.write_text(
            "brazil1111.discord.media:443\nrotterdam2222.discord.media:443\n"
        )
        self.assertEqual(
            [item.code for item in region_module.voice_endpoints()], ["rotterdam", "brazil"]
        )

    def test_repeated_targets_appear_once(self):
        self.journal.write_text("brazil1111.discord.media:443\n" * 8)
        self.assertEqual(len(region_module.voice_endpoints()), 1)

    def test_the_journal_wins_over_the_other_sources(self):
        state = Path(self.directory.name) / "voice-endpoint.txt"
        state.write_text("93.184.216.34:50007\n")
        region_module.state_path = lambda: state
        self.journal.write_text("rotterdam1234.discord.media:443\n")
        found = region_module.voice_endpoints()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].code, "rotterdam")

    def test_reads_the_full_log_format(self):
        self.journal.write_text(
            "11:05:12 cdn.discordapp.com:443 ok enviado=2KB recebido=180KB 1.2s\n"
            "11:05:20 c-iad10-b19ce4e8.discord.media:2053 ok enviado=5.1MB recebido=800KB 62.0s\n"
            "11:06:01 latency.discord.media:443 ok enviado=1KB recebido=1KB 0.3s\n"
        )
        found = region_module.voice_endpoints()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].hostname, "c-iad10-b19ce4e8.discord.media")
        self.assertEqual(found[0].port, 2053)

    def test_a_failed_tunnel_still_names_its_target(self):
        self.journal.write_text(
            "11:07:00 c-gru05-4f2c1a9b.discord.media:443 recusado(tempo esgotado) "
            "enviado=0B recebido=0B 10.0s\n"
        )
        self.assertIn("São Paulo", region_module.voice_endpoints()[0].region)

    def test_text_shows_name_and_region(self):
        self.journal.write_text("brazil1111.discord.media:443\n")
        self.assertIn("brazil1111.discord.media:443", str(region_module.voice_endpoints()[0]))


class MediaServerNames(unittest.TestCase):
    def test_real_voice_servers(self):
        for host in (
            "c-iad10-b19ce4e8.discord.media",
            "c-gru05-4f2c1a9b.discord.media",
            "rotterdam1234.discord.media",
            "brazil11111.discord.media",
        ):
            with self.subTest(host=host):
                self.assertTrue(region_module._is_media_server(host))

    def test_latency_probe_is_not_a_voice_server(self):
        self.assertFalse(region_module._is_media_server("latency.discord.media"))

    def test_other_discord_hosts(self):
        for host in ("cdn.discordapp.com", "gateway.discord.gg", "discord.com", "media.discordapp.net"):
            with self.subTest(host=host):
                self.assertFalse(region_module._is_media_server(host))


class LookupAnswers(unittest.TestCase):
    """Respostas de cada serviço de consulta, e o que fazer com as ruins."""

    def test_ipinfo_shape(self):
        place = region_module._place_from(
            {"ip": "1.2.3.4", "city": "Amsterdam", "region": "North Holland", "country": "NL"}
        )
        self.assertEqual(place.address, "1.2.3.4")
        self.assertIn("Amsterdam", str(place))

    def test_ifconfig_shape(self):
        place = region_module._place_from(
            {
                "ip": "192.42.116.45",
                "country": "The Netherlands",
                "country_iso": "NL",
                "asn_org": "SURF B.V.",
            }
        )
        self.assertEqual(place.address, "192.42.116.45")
        self.assertIn("The Netherlands", str(place))
        self.assertIn("SURF", str(place))

    def test_tor_check_shape(self):
        place = region_module._place_from({"IsTor": True, "IP": "192.42.116.45"})
        self.assertEqual(place.address, "192.42.116.45")
        self.assertIn("saída do Tor", str(place))

    def test_an_answer_without_an_ip_is_a_failure(self):
        for body in ({}, {"error": "rate limited"}, {"city": "Lisboa"}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                region_module._place_from(body)
