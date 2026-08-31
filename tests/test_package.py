"""O que o pacote entrega, e por quê.

As escolhas testadas aqui não são estéticas: executável em modo pasta, com
metadados e fora de uma pasta com ponto na frente, é o que evita o falso
positivo do Windows Defender. Trocar qualquer uma delas por engano reabre o
problema sem nenhum sintoma visível no build.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

import package


class VersionResource(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self._build = package.BUILD
        package.BUILD = self.directory
        self.addCleanup(setattr, package, "BUILD", self._build)

    def test_numbers_always_have_four_parts(self):
        self.assertEqual(len(package.version_numbers()), 4)
        self.assertTrue(all(isinstance(part, int) for part in package.version_numbers()))

    def test_file_is_pure_ascii(self):
        """O PyInstaller lê este arquivo como texto; acento cru já quebrou uma vez."""
        raw = package.write_version_resource().read_bytes()
        raw.decode("ascii")  # levanta se algum acento escapou para o arquivo

    def test_evaluates_to_the_fields_windows_shows(self):
        """O PyInstaller desserializa com `eval()`; o que vale é o que sai dele."""

        class Node:
            def __init__(self, *args, **kwargs):
                self.args, self.kwargs = args, kwargs

        names = (
            "VSVersionInfo",
            "FixedFileInfo",
            "StringFileInfo",
            "StringTable",
            "StringStruct",
            "VarFileInfo",
            "VarStruct",
        )
        scope = {name: type(name, (Node,), {}) for name in names}
        info = eval(package.write_version_resource().read_text(encoding="ascii"), scope)

        table = info.kwargs["kids"][0].args[0][0]
        fields = dict(entry.args for entry in table.args[1])
        self.assertEqual(fields["CompanyName"], "ayunaangel")
        self.assertEqual(fields["OriginalFilename"], f"{package.NAME}.exe")
        self.assertEqual(fields["FileVersion"], fields["ProductVersion"])
        self.assertEqual(info.kwargs["ffi"].kwargs["filevers"], package.version_numbers())


class Collect(unittest.TestCase):
    """Um build de Windows em modo pasta, montado à mão para não exigir PyInstaller."""

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)

        for name, value in (("DIST", self.directory / "dist"), ("WINDOWS", True)):
            self.addCleanup(setattr, package, name, getattr(package, name))
            setattr(package, name, value)

        self.bundle = package.DIST / package.NAME
        (self.bundle / "_internal" / "native").mkdir(parents=True)
        self.executable = self.bundle / f"{package.NAME}.exe"
        self.executable.write_bytes(b"MZ executavel de mentira")
        (self.bundle / "_internal" / "native" / "version.dll").write_bytes(b"MZ dll")
        (self.bundle / "_internal" / "base_library.zip").write_bytes(b"biblioteca")

        self.shim = self.directory / "version.dll"
        self.shim.write_bytes(b"MZ dll")

        self.staging = self.directory / "pacote"
        package.collect(self.executable, self.shim, self.staging)
        self.program = self.staging / package.PROGRAM_DIR

    def test_program_folder_is_not_hidden(self):
        """Pasta com ponto na frente parece esconderijo, e isso conta contra."""
        self.assertFalse(package.PROGRAM_DIR.startswith("."))
        self.assertTrue(self.program.is_dir())

    def test_folder_mode_keeps_the_libraries_together(self):
        self.assertTrue((self.program / f"{package.NAME}.exe").is_file())
        self.assertEqual(
            (self.program / "_internal" / "base_library.zip").read_bytes(), b"biblioteca"
        )

    def test_native_component_is_not_duplicated(self):
        """No modo pasta ele já vem pelo `--add-data`; uma cópia solta é ruído."""
        self.assertFalse((self.program / "version.dll").exists())
        self.assertTrue((self.program / "_internal" / "native" / "version.dll").is_file())

    def test_hashes_are_relative_and_correct(self):
        lines = (self.program / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines()
        listed = {}
        for line in lines:
            digest, name = line.split("  ", 1)
            listed[name] = digest
        self.assertIn(f"{package.NAME}.exe", listed)
        self.assertIn("_internal/native/version.dll", listed)
        for name, digest in listed.items():
            target = self.program / name
            self.assertTrue(target.is_file(), name)
            self.assertEqual(package.sha256(target), digest)

    def test_guide_stays_outside_where_people_see_it(self):
        self.assertTrue((self.staging / "COMO-USAR.txt").is_file())
        self.assertTrue((self.staging / "INICIAR-WINDOWS.cmd").is_file())


class Checksum(unittest.TestCase):
    def test_receipt_matches_the_file(self):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        target = directory / "pacote.zip"
        target.write_bytes(b"conteudo qualquer")
        receipt = package.checksum_file(target)
        digest, name = receipt.read_text(encoding="ascii").strip().split("  ", 1)
        self.assertEqual(name, target.name)
        self.assertEqual(digest, package.sha256(target))


if __name__ == "__main__":
    unittest.main()
