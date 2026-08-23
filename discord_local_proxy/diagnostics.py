from __future__ import annotations

import logging
import os
import platform as platform_module
import subprocess
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Mapping

from . import __version__


LOGGER = logging.getLogger("discord_local_proxy")
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False
LOGGER.addHandler(logging.NullHandler())

LOG_FILENAME = "discord-local-proxy.log"
MAX_LOG_BYTES = 1024 * 1024
LOG_BACKUPS = 4

_handler_lock = threading.Lock()
_file_handler: RotatingFileHandler | None = None


class _PrivateRotatingFileHandler(RotatingFileHandler):
    def doRollover(self) -> None:
        super().doRollover()
        if os.name == "nt":
            return
        for candidate in (Path(self.baseFilename), *self._backup_paths()):
            try:
                os.chmod(candidate, 0o600)
            except FileNotFoundError:
                continue

    def _backup_paths(self) -> tuple[Path, ...]:
        return tuple(
            Path(f"{self.baseFilename}.{index}")
            for index in range(1, self.backupCount + 1)
        )


def log_directory(
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    platform: str | None = None,
) -> Path:
    env = os.environ if environ is None else environ
    user_home = Path.home() if home is None else Path(home)
    target_platform = os.name if platform is None else platform
    if target_platform == "nt":
        base = Path(env.get("LOCALAPPDATA", user_home / "AppData" / "Local"))
    else:
        base = Path(env.get("XDG_STATE_HOME", user_home / ".local" / "state"))
    return base / "discord-local-proxy" / "logs"


def log_file_path(
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    platform: str | None = None,
) -> Path:
    return log_directory(environ=environ, home=home, platform=platform) / LOG_FILENAME


def configure_logging(*, path: Path | None = None) -> Path:
    global _file_handler

    target = (path or log_file_path()).expanduser().absolute()
    with _handler_lock:
        if _file_handler is not None and Path(_file_handler.baseFilename) == target:
            return target
        if _file_handler is not None:
            LOGGER.removeHandler(_file_handler)
            _file_handler.close()
            _file_handler = None
        try:
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if os.name != "nt":
                os.chmod(target.parent, 0o700)
            handler = _PrivateRotatingFileHandler(
                target,
                maxBytes=MAX_LOG_BYTES,
                backupCount=LOG_BACKUPS,
                encoding="utf-8",
            )
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s | %(levelname)s | %(threadName)s | %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            LOGGER.addHandler(handler)
            _file_handler = handler
            if os.name != "nt":
                os.chmod(target, 0o600)
        except OSError:
            # Logging must never prevent the installer or launcher from opening.
            return target
    return target


def close_logging() -> None:
    """Close the active file handler so Windows can release the log file."""
    global _file_handler

    with _handler_lock:
        if _file_handler is None:
            return
        LOGGER.removeHandler(_file_handler)
        _file_handler.close()
        _file_handler = None


def record_session(command: str) -> None:
    LOGGER.info(
        "sessão iniciada | versão=%s | comando=%s | sistema=%s %s | python=%s | empacotado=%s",
        __version__,
        command,
        sys.platform,
        platform_module.release(),
        platform_module.python_version(),
        bool(getattr(sys, "frozen", False)),
    )


def record_exception(context: str, error: BaseException, *, warning: bool = False) -> None:
    method = LOGGER.warning if warning else LOGGER.error
    method(
        "%s: %s",
        context,
        error,
        exc_info=(type(error), error, error.__traceback__),
    )


def log_hint() -> str:
    return f"Registro de diagnóstico: {log_file_path()}"


def open_log_directory() -> Path:
    directory = log_directory().expanduser().absolute()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(directory, 0o700)
    if os.name == "nt":
        os.startfile(str(directory))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(
            ["xdg-open", str(directory)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    return directory
