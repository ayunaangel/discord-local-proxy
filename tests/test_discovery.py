from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from discord_local_proxy.discovery import discover_installations


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fixture executable\n")
    path.chmod(0o700)
    return path


class DiscordDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)

    def test_windows_finds_stable_ptb_canary_and_chooses_newest_version(self) -> None:
        local_app_data = self.root / "LocalAppData"
        fixtures = {
            "Discord": ("Discord.exe", ("app-1.9.0", "app-1.10.0")),
            "DiscordPTB": ("DiscordPTB.exe", ("app-0.0.80", "app-0.0.81")),
            "DiscordCanary": ("DiscordCanary.exe", ("app-2.3.4", "app-2.4.0")),
        }
        expected_paths: dict[str, Path] = {}
        channel_for_root = {
            "Discord": "stable",
            "DiscordPTB": "ptb",
            "DiscordCanary": "canary",
        }
        for root_name, (executable_name, versions) in fixtures.items():
            for version in versions:
                executable = _make_executable(
                    local_app_data / root_name / version / executable_name
                )
            expected_paths[channel_for_root[root_name]] = executable.resolve()

        # These names must not be mistaken for versioned Squirrel directories.
        _make_executable(local_app_data / "Discord" / "app-current" / "Discord.exe")

        found = discover_installations(
            platform="windows",
            environ={"LOCALAPPDATA": str(local_app_data)},
            home=self.root / "unused-home",
        )

        self.assertEqual([item.channel for item in found], ["stable", "ptb", "canary"])
        self.assertEqual(
            {item.channel: item.executable for item in found},
            expected_paths,
        )
        self.assertTrue(all(item.source == "squirrel" for item in found))
        self.assertTrue(all(item.supports_udp_shim for item in found))

    def test_linux_uses_user_update_native_command_and_appimage_fixtures(self) -> None:
        home = self.root / "home"
        config_home = self.root / "config"
        bin_directory = self.root / "bin"
        stable_user_update = _make_executable(config_home / "discord" / "Discord")
        # A PATH installation also exists, but the per-user Discord update wins.
        _make_executable(bin_directory / "discord")
        ptb_command = _make_executable(bin_directory / "discord-ptb")
        canary_appimage = _make_executable(
            home / "Applications" / "DiscordCanary-2.4.0.AppImage"
        )

        found = discover_installations(
            platform="linux",
            environ={
                "PATH": str(bin_directory),
                "XDG_CONFIG_HOME": str(config_home),
            },
            home=home,
        )
        by_channel = {item.channel: item for item in found}

        self.assertEqual(set(by_channel), {"stable", "ptb", "canary"})
        self.assertEqual(by_channel["stable"].source, "discord-user-update")
        self.assertEqual(by_channel["stable"].executable, stable_user_update.resolve())
        self.assertEqual(by_channel["ptb"].source, "native-package")
        self.assertEqual(by_channel["ptb"].executable, ptb_command.resolve())
        self.assertEqual(by_channel["canary"].source, "appimage")
        self.assertEqual(by_channel["canary"].executable, canary_appimage.resolve())
        self.assertTrue(all(item.supports_udp_shim for item in found))


if __name__ == "__main__":
    unittest.main()
