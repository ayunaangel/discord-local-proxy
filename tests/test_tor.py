"""O Tor embutido: onde ele é procurado e como as respostas são lidas."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from discord_proxy import tor as tor_module


def make_tor_browser(root: Path, *, with_geoip: bool = True) -> Path:
    """Monta a árvore de um Tor Browser de mentira."""
    binary = root / "Browser" / "TorBrowser" / "Tor" / ("tor.exe" if os.name == "nt" else "tor")
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    if with_geoip:
        data = root / "Browser" / "TorBrowser" / "Data" / "Tor"
        data.mkdir(parents=True, exist_ok=True)
        (data / "geoip").write_text("# tabela de mentira\n")
        (data / "geoip6").write_text("# tabela de mentira\n")
    return binary


class Discovery(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name) / "tor-browser"

    def tearDown(self):
        self.directory.cleanup()

    def test_finds_inside_a_tor_browser_folder(self):
        binary = make_tor_browser(self.root)
        program = tor_module.find_tor(self.root)
        self.assertEqual(program.executable, binary)
        self.assertTrue(program.can_choose_country)
        self.assertEqual(program.geoip.name, "geoip")

    def test_accepts_the_executable_directly(self):
        binary = make_tor_browser(self.root)
        program = tor_module.find_tor(binary)
        self.assertEqual(program.executable, binary)

    def test_without_geoip_it_cannot_choose_a_country(self):
        make_tor_browser(self.root, with_geoip=False)
        program = tor_module.find_tor(self.root)
        self.assertFalse(program.can_choose_country)

    def test_choosing_a_country_without_geoip_is_refused(self):
        make_tor_browser(self.root, with_geoip=False)
        program = tor_module.find_tor(self.root)
        with self.assertRaises(tor_module.TorError) as caught:
            tor_module.start(country="nl", program=program, timeout=1)
        self.assertIn("tabelas de país", str(caught.exception))

    def test_an_invalid_country_is_refused(self):
        make_tor_browser(self.root)
        program = tor_module.find_tor(self.root)
        with self.assertRaises(tor_module.TorError):
            tor_module.start(country="brasil", program=program, timeout=1)

    def test_search_locations_are_absolute(self):
        places = tor_module.search_locations()
        self.assertTrue(places)
        for place in places:
            self.assertTrue(place.is_absolute(), place)


class Labels(unittest.TestCase):
    def test_known_countries(self):
        self.assertEqual(tor_module.country_label("nl"), "Holanda")
        self.assertEqual(tor_module.country_label("US"), "Estados Unidos")

    def test_empty_is_automatic(self):
        self.assertIn("Automático", tor_module.country_label(""))

    def test_unknown_code_is_kept(self):
        self.assertEqual(tor_module.country_label("zz"), "zz")


class ProgressParsing(unittest.TestCase):
    def test_percent(self):
        line = "Aug 28 [notice] Bootstrapped 45% (requesting_descriptors): Asking for relays"
        self.assertEqual(tor_module._percent_of(line), 45)
        self.assertIn("Asking", tor_module._stage_of(line))

    def test_line_without_progress(self):
        self.assertIsNone(tor_module._percent_of("Aug 28 [notice] Opening Socks listener"))

    def test_explains_a_country_failure(self):
        message = tor_module._explain("[err] no exits", "nl")
        self.assertIn("Holanda", message)
        self.assertIn("Automático", message)

    def test_explains_a_busy_port(self):
        self.assertIn("ocupada", tor_module._explain("Failed to bind", ""))


class HiddenWindow(unittest.TestCase):
    def test_options_match_the_platform(self):
        options = tor_module._hidden_window()
        if os.name == "nt":
            self.assertIn("creationflags", options)
            self.assertIn("startupinfo", options)
        else:
            self.assertEqual(options, {"start_new_session": True})


if __name__ == "__main__":
    unittest.main()
