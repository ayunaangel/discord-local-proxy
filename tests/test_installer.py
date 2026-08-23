from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from discord_local_proxy.config import AppConfig, ProxySettings, VoiceSettings
from discord_local_proxy.discovery import DiscordInstallation, app_data_root, default_config_path
from discord_local_proxy.installer import InstallError, install, status, uninstall
from discord_local_proxy.native import find_bundled_shim


def _installation(executable: Path) -> DiscordInstallation:
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_bytes(b"fixture Discord executable\n")
    executable.chmod(0o700)
    return DiscordInstallation(
        channel="stable",
        label="Discord",
        command=(str(executable),),
        executable=executable,
        root=executable.parent,
        source="native-package",
        icon="discord",
        supports_udp_shim=True,
    )


@unittest.skipUnless(os.name == "posix", "integração do instalador Linux")
class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.environment = {
            "XDG_DATA_HOME": str(self.root / "data"),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "PATH": "",
        }
        self.installation = _installation(self.root / "system" / "Discord")
        self.native_source = self.root / "fixture" / "libdiscord_udp_shim.so"
        self.native_source.parent.mkdir(parents=True)
        self.native_source.write_bytes(b"native shim fixture\n")
        self.config = AppConfig(
            proxy=ProxySettings(),
            voice=VoiceSettings(enabled=True, delay_ms=50),
        )

    def test_install_zipapp_status_and_uninstall_in_isolated_directories(self) -> None:
        with patch.dict(os.environ, self.environment, clear=False):
            result = install(
                ["stable"],
                self.config,
                installations=[self.installation],
                native_source=self.native_source,
            )

            root = app_data_root()
            runtime = root / "discord-local-proxy.pyz"
            desktop = (
                Path(self.environment["XDG_DATA_HOME"])
                / "applications"
                / "discord-local-proxy-stable.desktop"
            )
            self.assertTrue(runtime.is_file())
            self.assertTrue(desktop.is_file())
            self.assertTrue(result.channels[0].config_path.is_file())
            managed_shim = root / "native" / "libdiscord_udp_shim.so"
            self.assertTrue(managed_shim.is_file())
            self.assertEqual(managed_shim.read_bytes(), self.native_source.read_bytes())
            self.assertTrue(find_bundled_shim(platform="linux").is_file())
            self.assertEqual(status()["channels"], ["stable"])

            completed = subprocess.run(
                [sys.executable, str(runtime), "status"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, **self.environment},
                timeout=20,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(json.loads(completed.stdout)["installed"])

            removed = uninstall(purge_config=True)
            self.assertFalse(runtime.exists())
            self.assertFalse(desktop.exists())
            self.assertFalse(result.channels[0].config_path.exists())
            self.assertFalse((root / "native" / "libdiscord_udp_shim.so").exists())
            self.assertGreaterEqual(len(removed.removed), 4)
            self.assertFalse(root.exists())

    def test_failed_install_restores_existing_files_and_removes_new_native_asset(self) -> None:
        with patch.dict(os.environ, self.environment, clear=False):
            root = app_data_root()
            root.mkdir(parents=True)
            config_path = default_config_path(self.installation)
            config_path.parent.mkdir(parents=True)
            config_path.write_bytes(b"previous config\n")
            runtime = root / "discord-local-proxy.pyz"
            runtime.write_bytes(b"previous runtime\n")
            desktop = (
                Path(self.environment["XDG_DATA_HOME"])
                / "applications"
                / "discord-local-proxy-stable.desktop"
            )
            desktop.parent.mkdir(parents=True)
            desktop.write_bytes(b"previous shortcut\n")
            manifest = root / "install-manifest.json"
            previous_manifest = json.dumps(
                {
                    "format": 1,
                    "version": "previous",
                    "runtime": [str(runtime)],
                    "channels": [
                        {
                            "channel": "stable",
                            "config": str(config_path),
                            "shortcuts": [str(desktop)],
                        }
                    ],
                }
            ).encode("utf-8")
            manifest.write_bytes(previous_manifest)

            with patch(
                "discord_local_proxy.installer._create_shortcuts",
                side_effect=InstallError("falha simulada"),
            ):
                with self.assertRaisesRegex(InstallError, "falha simulada"):
                    install(
                        ["stable"],
                        self.config,
                        installations=[self.installation],
                        native_source=self.native_source,
                    )

            self.assertEqual(config_path.read_bytes(), b"previous config\n")
            self.assertEqual(runtime.read_bytes(), b"previous runtime\n")
            self.assertEqual(desktop.read_bytes(), b"previous shortcut\n")
            self.assertEqual(manifest.read_bytes(), previous_manifest)
            self.assertFalse((root / "native" / "libdiscord_udp_shim.so").exists())

    def test_install_refuses_to_overwrite_an_unmanaged_shortcut(self) -> None:
        with patch.dict(os.environ, self.environment, clear=False):
            desktop = (
                Path(self.environment["XDG_DATA_HOME"])
                / "applications"
                / "discord-local-proxy-stable.desktop"
            )
            desktop.parent.mkdir(parents=True)
            desktop.write_bytes(b"belongs to the user\n")

            with self.assertRaisesRegex(InstallError, "não pertence"):
                install(
                    ["stable"],
                    self.config,
                    installations=[self.installation],
                    native_source=self.native_source,
                )

            self.assertEqual(desktop.read_bytes(), b"belongs to the user\n")


if __name__ == "__main__":
    unittest.main()
