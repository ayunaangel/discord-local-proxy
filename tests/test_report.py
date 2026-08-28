"""O relatório de diagnóstico — o que ele traz e o que ele nunca traz."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from discord_proxy import report as report_module


class Content(unittest.TestCase):
    def setUp(self):
        self.texto = report_module.build()

    def test_has_the_expected_sections(self):
        for titulo in ("SISTEMA", "DISCORD ENCONTRADO", "CONFIGURAÇÃO", "TOR"):
            with self.subTest(titulo=titulo):
                self.assertIn(titulo, self.texto)

    def test_says_what_to_do_with_it(self):
        self.assertIn("O QUE FAZER COM ISTO", self.texto)

    def test_is_readable_portuguese(self):
        self.assertIn("Gerado em", self.texto)
        self.assertIn("NÃO contém a sua senha", self.texto)


class PasswordMasking(unittest.TestCase):
    def test_password_is_replaced(self):
        linha = "proxy = socks5://ana:supersecreta@servidor.exemplo:1080"
        mascarada = report_module._mask(linha)
        self.assertNotIn("supersecreta", mascarada)
        self.assertIn("ana:***@servidor.exemplo:1080", mascarada)

    def test_lines_without_password_are_untouched(self):
        for linha in ("proxy = tor", "pais = nl", "voice = off", "proxy = socks5://1.2.3.4:1080"):
            with self.subTest(linha=linha):
                self.assertEqual(report_module._mask(linha), linha)

    def test_a_saved_report_never_carries_the_password(self):
        from discord_proxy import config as config_module

        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "discord-proxy.ini"
            config_module.save(
                caminho,
                config_module.Config(
                    proxy=config_module.parse_proxy("socks5://ana:supersecreta@host:1080")
                ),
            )
            texto = report_module.build(config_path=caminho)
        self.assertNotIn("supersecreta", texto)
        self.assertIn("***", texto)


class Saving(unittest.TestCase):
    def test_saves_where_asked(self):
        with tempfile.TemporaryDirectory() as pasta:
            caminho = report_module.save(Path(pasta))
            self.assertTrue(caminho.is_file())
            self.assertEqual(caminho.name, report_module.REPORT_NAME)
            self.assertGreater(len(caminho.read_text(encoding="utf-8")), 500)

    def test_slow_lines_are_flagged(self):
        self.assertTrue(report_module._slow("11:00:00 host:443 ok enviado=5MB recebido=1KB 62.0s"))
        self.assertFalse(report_module._slow("11:00:00 host:443 ok enviado=5MB recebido=1KB 2.0s"))
        self.assertFalse(report_module._slow("linha estranha"))


if __name__ == "__main__":
    unittest.main()
