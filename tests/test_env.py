"""Limpeza do rastro do empacotador antes de abrir um processo filho."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from discord_proxy import env as env_module
from discord_proxy import run as run_module
from discord_proxy.config import Config


class Frozen:
    """Finge que estamos rodando empacotados, numa pasta conhecida."""

    def __init__(self, bundle: str):
        self.bundle = bundle

    def __enter__(self):
        self.before = (getattr(sys, "frozen", None), getattr(sys, "_MEIPASS", None))
        sys.frozen = True
        sys._MEIPASS = self.bundle
        return self

    def __exit__(self, *_):
        frozen, meipass = self.before
        for name, value in (("frozen", frozen), ("_MEIPASS", meipass)):
            if value is None:
                if hasattr(sys, name):
                    delattr(sys, name)
            else:
                setattr(sys, name, value)


class StripBundle(unittest.TestCase):
    def test_fora_do_pacote_nada_muda(self):
        source = {"LD_LIBRARY_PATH": "/opt/meu/lib", "HOME": "/home/alguem"}
        self.assertEqual(env_module.strip_bundle(source), source)

    def test_tira_a_pasta_do_empacotador(self):
        with Frozen("/tmp/_MEI123"):
            limpo = env_module.strip_bundle(
                {"LD_LIBRARY_PATH": "/tmp/_MEI123", "HOME": "/home/alguem"}
            )
        self.assertNotIn("LD_LIBRARY_PATH", limpo)
        self.assertEqual(limpo["HOME"], "/home/alguem")

    def test_preserva_o_que_a_pessoa_ja_tinha(self):
        with Frozen("/tmp/_MEI123"):
            limpo = env_module.strip_bundle(
                {"LD_LIBRARY_PATH": f"/tmp/_MEI123:/opt/cuda/lib"}
            )
        self.assertEqual(limpo["LD_LIBRARY_PATH"], "/opt/cuda/lib")

    def test_devolve_o_valor_original_guardado(self):
        with Frozen("/tmp/_MEI123"):
            limpo = env_module.strip_bundle(
                {"LD_LIBRARY_PATH": "/tmp/_MEI123", "LD_LIBRARY_PATH_ORIG": "/opt/cuda/lib"}
            )
        self.assertEqual(limpo["LD_LIBRARY_PATH"], "/opt/cuda/lib")
        self.assertNotIn("LD_LIBRARY_PATH_ORIG", limpo)

    def test_usa_a_variavel_interna_quando_nao_ha_meipass(self):
        # Sem `sys._MEIPASS`, a pasta do pacote ainda é descoberta pelo ambiente.
        limpo = env_module.strip_bundle(
            {
                "LD_LIBRARY_PATH": "/tmp/_MEI123:/opt/cuda/lib",
                "_PYI_APPLICATION_HOME_DIR": "/tmp/_MEI123",
            }
        )
        self.assertEqual(limpo["LD_LIBRARY_PATH"], "/opt/cuda/lib")

    def test_some_com_as_variaveis_internas(self):
        with Frozen("/tmp/_MEI123"):
            limpo = env_module.strip_bundle(
                {
                    "_PYI_APPLICATION_HOME_DIR": "/tmp/_MEI123",
                    "_PYI_ARCHIVE_FILE": "/opt/DiscordProxy",
                    "_MEIPASS2": "/tmp/_MEI123",
                    "HOME": "/home/alguem",
                }
            )
        self.assertEqual(limpo, {"HOME": "/home/alguem"})


class DiscordEnvironment(unittest.TestCase):
    """O ambiente entregue ao Discord não pode carregar nada do empacotador."""

    def test_o_discord_nao_herda_o_ld_library_path_do_pacote(self):
        source = {
            "HOME": "/home/alguem",
            "LD_LIBRARY_PATH": "/tmp/_MEI123",
            "_PYI_APPLICATION_HOME_DIR": "/tmp/_MEI123",
        }
        with Frozen("/tmp/_MEI123"):
            environment = run_module._environment(
                source, Config(), None, Path("/tmp/discord-proxy.ini")
            )
        self.assertNotIn("LD_LIBRARY_PATH", environment)
        self.assertNotIn("_PYI_APPLICATION_HOME_DIR", environment)
        self.assertEqual(environment["HOME"], "/home/alguem")


if __name__ == "__main__":
    unittest.main()
