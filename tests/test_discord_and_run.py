"""Detecção do Discord e montagem do comando, com um sistema de mentira."""

import os
import socket as socket_module
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from discord_proxy import config as config_module
from discord_proxy import discord as discord_module
from discord_proxy import run as run_module
from discord_proxy import shortcut as shortcut_module


def make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


@unittest.skipIf(os.name == "nt" or sys.platform == "darwin", "cenário Linux")
class LinuxDetection(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.home = Path(self.directory.name)
        self.binaries = self.home / "bin"
        self.environ = {
            "HOME": str(self.home),
            "PATH": str(self.binaries),
            "XDG_DATA_HOME": str(self.home / ".local" / "share"),
        }

    def tearDown(self):
        self.directory.cleanup()

    def test_official_package_points_at_the_real_binary(self):
        make_executable(self.binaries / "discord")
        real = make_executable(self.home / ".config" / "discord" / "Discord")
        found = discord_module.detect_channel("stable", environ=self.environ)
        self.assertIsNotNone(found)
        self.assertEqual(found.executable, real)
        self.assertEqual(found.kind, "linux")
        self.assertTrue(found.supports_voice)

    def test_appimage_is_found_in_applications(self):
        make_executable(self.home / "Applications" / "DiscordCanary-1.0.AppImage")
        found = discord_module.detect_channel("canary", environ=self.environ)
        self.assertIsNotNone(found)
        self.assertEqual(found.kind, "appimage")
        self.assertTrue(found.supports_voice)

    def test_nothing_installed(self):
        self.assertIsNone(discord_module.detect_channel("ptb", environ=self.environ))

    def test_unknown_channel(self):
        with self.assertRaises(ValueError):
            discord_module.detect_channel("beta", environ=self.environ)


class WindowsDetection(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    @unittest.skipIf(os.name != "nt", "cenário Windows")
    def test_newest_squirrel_version_wins(self):
        base = self.root / "Discord"
        make_executable(base / "app-1.0.9000" / "Discord.exe")
        newest = make_executable(base / "app-1.0.9100" / "Discord.exe")
        found = discord_module.detect_channel("stable", environ={"LOCALAPPDATA": str(self.root)})
        self.assertEqual(found.executable, newest)

    def test_version_ordering(self):
        keys = [discord_module._version_key(text) for text in ("1.0.9000", "1.0.9100", "1.2.0")]
        self.assertEqual(sorted(keys), keys)


class Planning(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.home = Path(self.directory.name)
        self.binaries = self.home / "bin"
        self.environ = {
            "HOME": str(self.home),
            "PATH": str(self.binaries),
            "LD_PRELOAD": "/algo/antigo.so",
            "NODE_OPTIONS": "--inspect",
            "XDG_DATA_HOME": str(self.home / ".local" / "share"),
        }
        self.config_path = self.home / "discord-proxy.ini"

    def tearDown(self):
        self.directory.cleanup()

    def _install_fake_discord(self):
        make_executable(self.binaries / "discord")
        return make_executable(self.home / ".config" / "discord" / "Discord")

    @unittest.skipIf(os.name == "nt" or sys.platform == "darwin", "cenário Linux")
    def test_proxy_becomes_chromium_flags(self):
        self._install_fake_discord()
        config_module.save(
            self.config_path,
            config_module.Config(proxy=config_module.parse_proxy("socks5://1.2.3.4:1080"), voice=False),
        )
        plan = run_module.build_plan(
            "stable",
            explicit_config=self.config_path,
            bridge_url="http://127.0.0.1:5555",
            environ=self.environ,
        )
        self.assertIn("--proxy-server=http://127.0.0.1:5555", plan.command)
        self.assertIn("--disable-quic", plan.command)
        self.assertIsNone(plan.shim)

    @unittest.skipIf(os.name == "nt" or sys.platform == "darwin", "cenário Linux")
    def test_environment_is_cleaned_up(self):
        self._install_fake_discord()
        config_module.save(self.config_path, config_module.Config(voice=False))
        plan = run_module.build_plan(
            "stable", explicit_config=self.config_path, environ=self.environ
        )
        self.assertNotIn("LD_PRELOAD", plan.environment)
        self.assertNotIn("NODE_OPTIONS", plan.environment)
        self.assertEqual(plan.environment[run_module.ENV_VOICE], "0")
        self.assertEqual(plan.environment[run_module.ENV_INI], str(self.config_path))

    @unittest.skipIf(os.name == "nt" or sys.platform == "darwin", "cenário Linux")
    def test_proxy_without_bridge_is_refused(self):
        self._install_fake_discord()
        config_module.save(
            self.config_path,
            config_module.Config(proxy=config_module.parse_proxy("http://1.2.3.4:8080"), voice=False),
        )
        with self.assertRaises(run_module.LaunchError):
            run_module.build_plan(
                "stable", explicit_config=self.config_path, environ=self.environ
            )

    def test_missing_channel_is_reported(self):
        with self.assertRaises(run_module.LaunchError):
            run_module.build_plan("ptb", explicit_config=self.config_path, environ=self.environ)


@unittest.skipIf(os.name == "nt" or sys.platform == "darwin", "cenário Linux")
class Shortcuts(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.home = Path(self.directory.name)
        self.previous = {name: os.environ.get(name) for name in ("HOME", "XDG_DATA_HOME")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_DATA_HOME"] = str(self.home / ".local" / "share")

    def tearDown(self):
        for name, value in self.previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.directory.cleanup()

    def test_create_and_remove(self):
        install = discord_module.Install(
            channel="stable",
            label="Discord",
            kind="linux",
            command=("/bin/true",),
            executable=Path("/bin/true"),
        )
        created = shortcut_module.create(install)
        self.assertTrue(created.path.is_file())
        self.assertIn("Discord (Proxy)", created.path.read_text())
        self.assertTrue(shortcut_module.remove(install))
        self.assertFalse(created.path.exists())

    def test_foreign_desktop_file_is_left_alone(self):
        install = discord_module.Install(
            channel="ptb", label="Discord PTB", kind="linux", command=("/bin/true",), executable=None
        )
        path = shortcut_module._paths(install)[0]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[Desktop Entry]\nName=de outra pessoa\n")
        self.assertFalse(shortcut_module.remove(install))
        self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(os.name == "nt" or sys.platform == "darwin", "cenário Linux")
class LaunchEndToEnd(unittest.TestCase):
    """Abre um 'Discord' de mentira e confere o que ele recebeu."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.home = Path(self.directory.name)
        self.report = self.home / "recebido.txt"
        fake = self.home / ".config" / "discord" / "Discord"
        fake.parent.mkdir(parents=True)
        fake.write_text(
            "#!/bin/sh\n"
            f'{{ echo "ARGS: $@"; echo "LD_PRELOAD=$LD_PRELOAD";'
            f' echo "VOICE=$DISCORD_PROXY_VOICE"; }} > "{self.report}"\n'
        )
        fake.chmod(0o755)
        make_executable(self.home / "bin" / "discord")
        self.environ = {
            "HOME": str(self.home),
            "PATH": str(self.home / "bin"),
            "XDG_DATA_HOME": str(self.home / ".local" / "share"),
        }
        self.config_path = self.home / "discord-proxy.ini"

    def tearDown(self):
        self.directory.cleanup()

    def _wait_for_report(self) -> str:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.report.is_file():
                text = self.report.read_text()
                if text.count("\n") >= 3:
                    return text
            time.sleep(0.05)
        self.fail("o executável de mentira não registrou nada")

    def test_direct_mode_starts_the_executable(self):
        config_module.save(self.config_path, config_module.Config(voice=False))
        result = run_module.launch(
            "stable",
            explicit_config=self.config_path,
            wait=False,
            require_closed=False,
            environ=self.environ,
        )
        self.assertFalse(result.proxy_used)
        recorded = self._wait_for_report()
        self.assertIn("ARGS: \n", recorded)
        self.assertIn("LD_PRELOAD=\n", recorded)
        self.assertIn("VOICE=0", recorded)

    def test_proxy_mode_passes_the_bridge_url(self):
        from tests.test_bridge import FakeProxy

        upstream = FakeProxy("socks5")
        upstream.start()
        self.addCleanup(upstream.close)
        config_module.save(
            self.config_path,
            config_module.Config(
                proxy=config_module.parse_proxy(f"socks5://127.0.0.1:{upstream.port}"),
                voice=False,
            ),
        )
        result = run_module.launch(
            "stable",
            explicit_config=self.config_path,
            wait=False,
            require_closed=False,
            environ=self.environ,
        )
        self.assertTrue(result.proxy_used)
        recorded = self._wait_for_report()
        self.assertIn("--proxy-server=http://127.0.0.1:", recorded)
        self.assertIn("--disable-quic", recorded)

    def test_an_open_discord_blocks_the_launch(self):
        """Uma janela viva ignora os argumentos novos, então recusamos abrir."""
        import shutil as shutil_module

        holder_directory = tempfile.mkdtemp()
        self.addCleanup(shutil_module.rmtree, holder_directory, True)
        holder = Path(holder_directory) / "Discord"
        shutil_module.copy2(sys.executable, holder)
        holder.chmod(0o755)
        import subprocess as subprocess_module

        process = subprocess_module.Popen(
            [str(holder), "-c", "import time; print('x', flush=True); time.sleep(20)"],
            stdout=subprocess_module.PIPE,
            stderr=subprocess_module.DEVNULL,
            text=True,
        )
        self.addCleanup(process.kill)
        if process.stdout is None or process.stdout.readline().strip() != "x":
            self.skipTest("o processo de mentira não subiu")

        config_module.save(self.config_path, config_module.Config(voice=False))
        with self.assertRaises(run_module.LaunchError) as caught:
            run_module.launch(
                "stable", explicit_config=self.config_path, environ=self.environ
            )
        self.assertIn("já está aberto", str(caught.exception))

    def test_a_broken_proxy_stops_the_launch(self):
        with socket_module.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            dead = probe.getsockname()[1]
        config_module.save(
            self.config_path,
            config_module.Config(
                proxy=config_module.parse_proxy(f"socks5://127.0.0.1:{dead}"), voice=False
            ),
        )
        with self.assertRaises(run_module.LaunchError):
            run_module.launch(
                "stable",
                explicit_config=self.config_path,
                require_closed=False,
                environ=self.environ,
            )
        self.assertFalse(self.report.exists())
