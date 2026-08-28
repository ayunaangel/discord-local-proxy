"""Aviso de saída lenta e encerramento de uma sessão pendurada."""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from discord_proxy import bridge as bridge_module
from discord_proxy import run as run_module
from discord_proxy.config import parse_proxy
from tests.test_bridge import FakeProxy


class ProxyLento(FakeProxy):
    """Aceita o túnel e depois não faz nada — como uma saída congestionada."""

    def _echo(self, client):
        time.sleep(6)


class AvisoDeLentidao(unittest.TestCase):
    def setUp(self):
        self.slow_original = bridge_module.SLOW_SECONDS
        self.intervalo_original = bridge_module.WATCH_INTERVAL
        bridge_module.SLOW_SECONDS = 0.6
        bridge_module.WATCH_INTERVAL = 0.2

    def tearDown(self):
        bridge_module.SLOW_SECONDS = self.slow_original
        bridge_module.WATCH_INTERVAL = self.intervalo_original

    def test_avisa_enquanto_o_tunel_ainda_esta_aberto(self):
        upstream = ProxyLento("socks5")
        upstream.start()
        self.addCleanup(upstream.close)

        avisos = []
        ponte = bridge_module.Bridge(
            parse_proxy(f"socks5://127.0.0.1:{upstream.port}"),
            on_slow=lambda destino, segundos: avisos.append((destino, segundos)),
        ).start()
        self.addCleanup(ponte.stop)

        cliente = socket.create_connection(("127.0.0.1", ponte.port), 5)
        self.addCleanup(cliente.close)
        alvo = "discord-attachments-uploads-prd.storage.googleapis.com:443"
        cliente.sendall(f"CONNECT {alvo} HTTP/1.1\r\nHost: {alvo}\r\n\r\n".encode())
        cliente.recv(200)

        limite = time.monotonic() + 5
        while not avisos and time.monotonic() < limite:
            time.sleep(0.1)

        self.assertTrue(avisos, "o vigia não avisou de um túnel demorado")
        destino, segundos = avisos[0]
        self.assertIn("googleapis", destino)
        self.assertGreaterEqual(segundos, 0.6)

    def test_avisa_uma_vez_so_por_tunel(self):
        upstream = ProxyLento("socks5")
        upstream.start()
        self.addCleanup(upstream.close)
        avisos = []
        ponte = bridge_module.Bridge(
            parse_proxy(f"socks5://127.0.0.1:{upstream.port}"),
            on_slow=lambda d, s: avisos.append(d),
        ).start()
        self.addCleanup(ponte.stop)

        cliente = socket.create_connection(("127.0.0.1", ponte.port), 5)
        self.addCleanup(cliente.close)
        cliente.sendall(b"CONNECT exemplo.com:443 HTTP/1.1\r\nHost: exemplo.com\r\n\r\n")
        cliente.recv(200)
        time.sleep(2.0)
        self.assertEqual(len(avisos), 1, f"avisou {len(avisos)} vezes")

    def test_tunel_rapido_nao_gera_aviso(self):
        upstream = FakeProxy("socks5")
        upstream.start()
        self.addCleanup(upstream.close)
        avisos = []
        with bridge_module.Bridge(
            parse_proxy(f"socks5://127.0.0.1:{upstream.port}"),
            on_slow=lambda d, s: avisos.append(d),
        ) as ponte:
            from tests.test_bridge import talk_through

            talk_through(ponte)
            time.sleep(1.0)
        self.assertEqual(avisos, [])


@unittest.skipUnless(sys.platform.startswith("linux"), "encerramento por /proc é do Linux")
class EncerrarSessao(unittest.TestCase):
    """O que conta como 'nosso' — e, principalmente, o que não conta."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.processos = []

    def tearDown(self):
        for processo in self.processos:
            processo.kill()
            try:
                processo.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            if processo.stdout is not None:
                processo.stdout.close()
        self.directory.cleanup()

    def _subir(self, nome: str, argumentos: list[str]) -> int:
        caminho = Path(self.directory.name) / nome
        shutil.copy2(sys.executable, caminho)
        caminho.chmod(0o755)
        processo = subprocess.Popen(
            [str(caminho), "-c", "import time; print('x', flush=True); time.sleep(30)"] + argumentos,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.processos.append(processo)
        if processo.stdout is None or processo.stdout.readline().strip() != "x":
            self.skipTest("o processo de mentira não subiu")
        return processo.pid

    def test_reconhece_o_discord(self):
        pid = self._subir("Discord", [])
        discord, _, _ = run_module._own_processes()
        self.assertIn(pid, discord)

    def test_o_tor_da_pessoa_nao_e_nosso(self):
        """O Tor Browser aberto pelo usuário não pode ser encerrado por nós."""
        pid = self._subir("tor", ["--DataDirectory", "/home/alguem/Downloads/tor-browser/dados"])
        _, _, tor = run_module._own_processes()
        self.assertNotIn(pid, tor, "encerraríamos o Tor Browser de outra pessoa")

    def test_o_nosso_tor_e_reconhecido(self):
        from discord_proxy import voice as voice_module

        pid = self._subir("tor", ["--DataDirectory", str(voice_module.data_root() / "tor" / "dados")])
        _, _, tor = run_module._own_processes()
        self.assertIn(pid, tor)

    def test_relatorio_em_portugues(self):
        vazio = run_module.StopReport()
        self.assertEqual(str(vazio), "nada estava rodando")
        self.assertEqual(vazio.total, 0)
        cheio = run_module.StopReport(discord=8, launcher=1, tor=1)
        self.assertIn("8 processo(s) do Discord", str(cheio))
        self.assertEqual(cheio.total, 10)


if __name__ == "__main__":
    unittest.main()
