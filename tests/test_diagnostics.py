from __future__ import annotations

import logging
import os
import tempfile
import unittest
from pathlib import Path

from discord_local_proxy.diagnostics import (
    LOGGER,
    LOG_BACKUPS,
    MAX_LOG_BYTES,
    configure_logging,
    log_directory,
    log_file_path,
)


class DiagnosticPathTests(unittest.TestCase):
    def test_linux_uses_xdg_state_home_and_safe_fallback(self) -> None:
        home = Path("/home/example")
        self.assertEqual(
            log_directory(
                environ={"XDG_STATE_HOME": "/tmp/custom-state"},
                home=home,
                platform="posix",
            ),
            Path("/tmp/custom-state/discord-local-proxy/logs"),
        )
        self.assertEqual(
            log_directory(environ={}, home=home, platform="posix"),
            home / ".local/state/discord-local-proxy/logs",
        )

    def test_windows_uses_local_app_data(self) -> None:
        self.assertEqual(
            log_file_path(
                environ={"LOCALAPPDATA": "C:/Users/Ana/AppData/Local"},
                home=Path("C:/Users/Ana"),
                platform="nt",
            ),
            Path("C:/Users/Ana/AppData/Local/discord-local-proxy/logs/discord-local-proxy.log"),
        )

    def test_configure_logging_writes_a_rotating_private_log(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "logs" / "diagnostic.log"
            self.assertEqual(configure_logging(path=path), path.absolute())
            LOGGER.error("diagnóstico de teste")
            for handler in LOGGER.handlers:
                handler.flush()
            self.assertIn("diagnóstico de teste", path.read_text(encoding="utf-8"))
            rotating = [
                handler for handler in LOGGER.handlers if hasattr(handler, "maxBytes")
            ]
            self.assertEqual(len(rotating), 1)
            self.assertEqual(rotating[0].maxBytes, MAX_LOG_BYTES)
            self.assertEqual(rotating[0].backupCount, LOG_BACKUPS)
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                rotating[0].doRollover()
                LOGGER.error("registro após rotação")
                rotating[0].flush()
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(Path(f"{path}.1").stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
