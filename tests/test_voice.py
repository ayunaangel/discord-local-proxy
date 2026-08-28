"""Proteções na instalação do componente nativo do Windows."""

import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from discord_proxy import voice as voice_module

X64 = 0x8664
X86 = 0x014C


def write_pe(path: Path, machine: int) -> Path:
    """Um PE mínimo: só o suficiente para ler a arquitetura."""
    header = bytearray(0x100)
    header[0:2] = b"MZ"
    struct.pack_into("<I", header, 0x3C, 0x80)
    header[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<H", header, 0x84, machine)
    path.write_bytes(bytes(header))
    return path


class Architecture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_reads_the_machine_field(self):
        self.assertEqual(voice_module._pe_machine(write_pe(self.root / "a.dll", X64)), X64)
        self.assertEqual(voice_module._pe_machine(write_pe(self.root / "b.dll", X86)), X86)

    def test_not_a_pe_file(self):
        plain = self.root / "leia.txt"
        plain.write_text("nada a ver")
        self.assertIsNone(voice_module._pe_machine(plain))

    def test_matching_architecture_passes(self):
        shim = write_pe(self.root / "version.dll", X64)
        write_pe(self.root / "Discord.exe", X64)
        voice_module._check_architecture(shim, self.root)

    def test_mismatch_is_refused(self):
        shim = write_pe(self.root / "version.dll", X64)
        write_pe(self.root / "Discord.exe", X86)
        with self.assertRaises(voice_module.VoiceError) as caught:
            voice_module._check_architecture(shim, self.root)
        self.assertIn("impediria o Discord de abrir", str(caught.exception))

    def test_empty_directory_is_not_a_problem(self):
        shim = write_pe(self.root / "version.dll", X64)
        voice_module._check_architecture(shim, self.root / "vazio")


class Receipts(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_receipt_round_trip(self):
        path = self.root / "recibo"
        digest = "a" * 64
        voice_module._write_receipt(path, digest)
        self.assertEqual(voice_module._read_receipt(path), digest)

    def test_garbage_receipt_is_ignored(self):
        path = self.root / "recibo"
        path.write_text("não é um hash\n")
        self.assertEqual(voice_module._read_receipt(path), "")

    def test_missing_receipt_is_empty(self):
        self.assertEqual(voice_module._read_receipt(self.root / "nao-existe"), "")



class DataRoot(unittest.TestCase):
    """A pasta de dados precisa respeitar o ambiente que lhe passam.

    Sem isso, um teste que chama `launch` com HOME próprio ainda escreve (e
    apaga) arquivos na pasta real de quem está usando o programa — foi o que
    aconteceu: a suíte apagou o registro da ponte de uma sessão em andamento.
    """

    def test_explicit_environment_is_respected(self):
        import os

        if os.name == "nt" or sys.platform == "darwin":
            self.skipTest("caminho POSIX")
        fake = {"HOME": "/tmp/casa-de-mentira", "XDG_DATA_HOME": "/tmp/dados-de-mentira"}
        self.assertEqual(
            voice_module.data_root(fake), Path("/tmp/dados-de-mentira/discord-proxy")
        )

    def test_falls_back_to_home_when_xdg_is_absent(self):
        import os

        if os.name == "nt" or sys.platform == "darwin":
            self.skipTest("caminho POSIX")
        fake = {"HOME": "/tmp/casa-de-mentira"}
        self.assertEqual(
            voice_module.data_root(fake),
            Path("/tmp/casa-de-mentira/.local/share/discord-proxy"),
        )

    def test_a_temporary_home_stays_inside_itself(self):
        import os
        import tempfile

        if os.name == "nt" or sys.platform == "darwin":
            self.skipTest("caminho POSIX")
        with tempfile.TemporaryDirectory() as home:
            root = voice_module.data_root({"HOME": home, "XDG_DATA_HOME": f"{home}/.local/share"})
        self.assertTrue(str(root).startswith(home), f"{root} escapou de {home}")
        self.assertFalse(str(root).startswith(str(Path.home() / ".local")))


if __name__ == "__main__":
    unittest.main()
