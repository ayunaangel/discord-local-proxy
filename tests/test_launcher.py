from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from discord_local_proxy.config import (
    AppConfig,
    ConfigError,
    ProxySettings,
    VoiceSettings,
    save_config,
)
from discord_local_proxy.discovery import DiscordInstallation
from discord_local_proxy.launcher import (
    _resolved_runtime_proxy,
    build_runtime_flags,
    prepare_launch,
)


def _installation(executable: Path, *, source: str = "native-package") -> DiscordInstallation:
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_bytes(b"fixture executable\n")
    executable.chmod(0o700)
    return DiscordInstallation(
        channel="stable",
        label="Discord",
        command=(str(executable),),
        executable=executable,
        root=executable.parent,
        source=source,
        icon="discord",
        supports_udp_shim=True,
    )


class LauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)

    def test_proxy_plan_uses_only_loopback_flags_and_does_not_expose_credentials(self) -> None:
        installation = _installation(self.root / "Discord")
        config_path = self.root / "discord-local-proxy.ini"
        password = "not-visible-in-process-metadata"
        config = AppConfig(
            proxy=ProxySettings(
                kind="http",
                host="upstream.proxy.example",
                port=3128,
                username="alice",
                password=password,
            ),
            voice=VoiceSettings(enabled=False, delay_ms=0),
        )
        save_config(config_path, config)
        inherited_environment = {
            "PATH": "/fixture/bin",
            "ELECTRON_RUN_AS_NODE": "1",
            "NODE_OPTIONS": "--require=/tmp/untrusted.js",
            "LD_PRELOAD": "/tmp/untrusted.so",
            "DISCORD_LOCAL_PROXY_CONFIG": "/tmp/stale.ini",
            "DISCORD_LOCAL_PROXY_VOICE_ENABLED": "1",
            "DISCORD_LOCAL_PROXY_VOICE_DELAY_MS": "999",
            "DISCORD_LOCAL_PROXY_VOICE_PACKET_FILE": "/tmp/stale-packet.bin",
        }

        plan = prepare_launch(
            "stable",
            config_path=config_path,
            installation=installation,
            bridge_url="http://127.0.0.1:48123",
            environ=inherited_environment,
        )

        self.assertEqual(
            plan.command,
            (
                str(installation.executable),
                "--proxy-server=http://127.0.0.1:48123",
                "--proxy-bypass-list=<local>",
                "--disable-quic",
            ),
        )
        exposed_process_metadata = "\n".join(
            (*plan.command, *(f"{key}={value}" for key, value in plan.environment.items()))
        )
        self.assertNotIn(password, exposed_process_metadata)
        self.assertNotIn("alice", exposed_process_metadata)
        self.assertNotIn("upstream.proxy.example", exposed_process_metadata)
        self.assertNotIn("NODE_OPTIONS", plan.environment)
        self.assertNotIn("ELECTRON_RUN_AS_NODE", plan.environment)
        self.assertNotIn("LD_PRELOAD", plan.environment)
        self.assertNotIn("DISCORD_LOCAL_PROXY_VOICE_PACKET_FILE", plan.environment)
        self.assertEqual(plan.environment["DISCORD_LOCAL_PROXY_VOICE_ENABLED"], "0")
        self.assertEqual(plan.environment["DISCORD_LOCAL_PROXY_CONFIG"], str(config_path))

    def test_voice_mode_remains_active_without_proxy_flags(self) -> None:
        installation = _installation(
            self.root / "Discord" / "app-1.0.0" / "Discord.exe",
            source="squirrel",
        )
        native_source = self.root / "bundled" / "version.dll"
        native_source.parent.mkdir(parents=True)
        native_source.write_bytes(b"native shim fixture")
        packet_file = self.root / "voice-packet.bin"
        packet_file.write_bytes(b"custom UDP prelude")
        config_path = self.root / "voice-only.ini"
        save_config(
            config_path,
            AppConfig(
                proxy=ProxySettings(),
                voice=VoiceSettings(
                    enabled=True,
                    delay_ms=65,
                    packet_file=packet_file,
                ),
            ),
        )

        with patch.dict(
            os.environ,
            {"LOCALAPPDATA": str(self.root / "LocalAppData")},
            clear=False,
        ):
            plan = prepare_launch(
                "stable",
                config_path=config_path,
                installation=installation,
                native_source=native_source,
                environ={"PATH": "/fixture/bin", "LD_PRELOAD": "/tmp/stale.so"},
            )

        self.assertEqual(plan.command, installation.command)
        self.assertEqual(build_runtime_flags(plan.config, None), ())
        self.assertEqual(plan.environment["DISCORD_LOCAL_PROXY_VOICE_ENABLED"], "1")
        self.assertEqual(plan.environment["DISCORD_LOCAL_PROXY_VOICE_DELAY_MS"], "65")
        self.assertEqual(
            plan.environment["DISCORD_LOCAL_PROXY_VOICE_PACKET_FILE"],
            str(packet_file),
        )
        self.assertNotIn("LD_PRELOAD", plan.environment)
        self.assertEqual(plan.native_shim, installation.executable.parent / "version.dll")
        self.assertTrue(plan.native_shim.is_file())

    def test_password_environment_variable_is_not_inherited_by_discord(self) -> None:
        installation = _installation(self.root / "Discord")
        config_path = self.root / "proxy-env.ini"
        secret_name = "DLP_TEST_PROXY_SECRET"
        secret = "environment-only-secret"
        save_config(
            config_path,
            AppConfig(
                proxy=ProxySettings(
                    kind="socks5",
                    host="proxy.example",
                    port=1080,
                    username="alice",
                    password_env=secret_name,
                ),
                voice=VoiceSettings(enabled=False, delay_ms=0),
            ),
        )

        plan = prepare_launch(
            "stable",
            config_path=config_path,
            installation=installation,
            bridge_url="http://127.0.0.1:48123",
            environ={"PATH": "/fixture/bin", secret_name: secret},
        )

        self.assertNotIn(secret_name, plan.environment)
        self.assertNotIn(secret, "\n".join(plan.environment.values()))
        runtime_proxy = _resolved_runtime_proxy(plan.config.proxy, {secret_name: secret})
        self.assertEqual(runtime_proxy.resolved_password({}), secret)
        self.assertEqual(runtime_proxy.password_env, "")

    def test_explicit_symlinked_config_is_not_resolved_around_safety_check(self) -> None:
        installation = _installation(self.root / "Discord")
        real_config = self.root / "real.ini"
        save_config(real_config, AppConfig(voice=VoiceSettings(enabled=False)))
        linked_config = self.root / "linked.ini"
        try:
            linked_config.symlink_to(real_config)
        except (OSError, NotImplementedError):
            self.skipTest("links simbólicos não estão disponíveis")

        with self.assertRaisesRegex(ConfigError, "arquivo regular"):
            prepare_launch(
                "stable",
                config_path=linked_config,
                installation=installation,
                environ={"PATH": "/fixture/bin"},
            )


if __name__ == "__main__":
    unittest.main()
