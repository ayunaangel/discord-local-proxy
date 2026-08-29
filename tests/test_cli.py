"""Comandos que precisam se virar sozinhos quando algo lá fora falha."""

import argparse
import contextlib
import io
import socket
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from discord_proxy import cli as cli_module
from discord_proxy import region as region_module
from discord_proxy import tor as tor_module
from tests.test_bridge import FakeProxy


class TorFalso:
    """Faz o papel do Tor já ligado, apontando para um proxy de mentira."""

    def __init__(self, port: int):
        self.port = port
        self.stopped = False

    @property
    def proxy_url(self) -> str:
        return f"socks5://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.stopped = True


def _config_file(text: str) -> Path:
    path = Path(tempfile.mkdtemp()) / "discord-proxy.ini"
    path.write_text(f"[discord-proxy]\n{text}\n", encoding="utf-8")
    return path


def _run(function, arguments):
    """Roda um comando e devolve (código, o que saiu, o que foi para o erro)."""
    saida, erro = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(saida), contextlib.redirect_stderr(erro):
        code = function(arguments)
    return code, saida.getvalue(), erro.getvalue()


class TestarASaida(unittest.TestCase):
    """`discord-proxy test` com `proxy = tor`.

    O Tor não tem endereço fixo: ele só existe depois de subir. A versão 1.0.2
    entregava o proxy vazio ao teste e respondia 'Modo direto: não há proxy
    para testar' — dizendo que estava tudo bem sem ter testado nada.
    """

    def setUp(self):
        self.original = tor_module.start
        self.addCleanup(lambda: setattr(tor_module, "start", self.original))

    def test_sobe_o_tor_e_testa_por_dentro_dele(self):
        upstream = FakeProxy("socks5")
        upstream.start()
        self.addCleanup(upstream.close)
        processo = TorFalso(upstream.port)
        tor_module.start = lambda **kwargs: processo

        path = _config_file("proxy = tor\npais = us")
        code, saida, _ = _run(cli_module._test, argparse.Namespace(config=path))

        self.assertEqual(code, 0)
        self.assertIn("abriu um túnel", saida)
        self.assertNotIn("Modo direto", saida)
        self.assertTrue(processo.stopped, "o Tor precisa ser desligado depois do teste")

    def test_a_saida_que_nao_responde_reprova_o_teste(self):
        # Porta sem ninguém escutando: é o que acontece quando o Tor subiu mas
        # o circuito morreu no meio do caminho.
        morta = socket.socket()
        morta.bind(("127.0.0.1", 0))
        port = morta.getsockname()[1]
        morta.close()
        processo = TorFalso(port)
        tor_module.start = lambda **kwargs: processo

        path = _config_file("proxy = tor")
        code, saida, erro = _run(cli_module._test, argparse.Namespace(config=path))

        self.assertEqual(code, 1)
        self.assertNotIn("Modo direto", saida + erro)
        self.assertTrue(processo.stopped)

    def test_o_tor_que_nao_sobe_vira_recado_e_nao_traceback(self):
        def explode(**kwargs):
            raise tor_module.TorError("não achei o Tor Browser")

        tor_module.start = explode
        path = _config_file("proxy = tor")
        code, _, erro = _run(cli_module._test, argparse.Namespace(config=path))

        self.assertEqual(code, 1)
        self.assertIn("não achei o Tor Browser", erro)
        self.assertNotIn("Traceback", erro)

    def test_sem_saida_configurada_continua_dizendo_modo_direto(self):
        path = _config_file("proxy = ")
        code, saida, _ = _run(cli_module._test, argparse.Namespace(config=path))

        self.assertEqual(code, 0)
        self.assertIn("Modo direto", saida)


class ComandoTor(unittest.TestCase):
    """`discord-proxy tor` quando a consulta de país não responde.

    Na 1.0.2 a exceção subia até o topo e o usuário via um traceback do Python
    seguido de 'Failed to execute script'.
    """

    def setUp(self):
        self.find_original = tor_module.find_tor
        self.start_original = tor_module.start
        self.lookup_original = region_module.exit_address
        self.addCleanup(lambda: setattr(tor_module, "find_tor", self.find_original))
        self.addCleanup(lambda: setattr(tor_module, "start", self.start_original))
        self.addCleanup(lambda: setattr(region_module, "exit_address", self.lookup_original))

    def test_a_consulta_que_falha_nao_derruba_o_programa(self):
        programa = tor_module.TorProgram(
            executable=Path("/usr/bin/tor"), library_dir=None, label="tor do sistema"
        )
        processo = TorFalso(9050)
        tor_module.find_tor = lambda *args, **kwargs: programa
        tor_module.start = lambda **kwargs: processo

        def sem_resposta(proxy):
            raise region_module.LookupFailed("ipinfo.io: deu tempo esgotado")

        region_module.exit_address = sem_resposta

        code, saida, erro = _run(cli_module._tor, argparse.Namespace(pais="us"))

        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", erro)
        self.assertIn("deu tempo esgotado", erro)
        self.assertIn("Tor encerrado.", saida)
        self.assertTrue(processo.stopped, "o Tor precisa ser desligado mesmo com a falha")


if __name__ == "__main__":
    unittest.main()
