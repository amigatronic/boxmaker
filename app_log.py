"""
Logging setup.

Writes to a rotating log file in %LOCALAPPDATA%\\BoxMaker (and to the
console when one exists). A windowed EXE has no console, so without a
file every error message would simply be lost.
"""

import logging
import logging.handlers
import os
import sys
from pathlib import Path

LOG_FILE_NAME = "boxmaker.log"

_configured = False


def log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "BoxMaker"


def log_file_path() -> Path:
    return log_dir() / LOG_FILE_NAME


def setup_logging(level: int = logging.INFO) -> None:
    """Configures the root logger once. Safe to call more than once."""
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger()
    root.setLevel(level)

    # build123d logs a harmless INFO line on every shape created outside a
    # builder context ("None context requested by Box"): keep warnings only.
    logging.getLogger("build123d").setLevel(logging.WARNING)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    try:
        log_dir().mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file_path(), maxBytes=512 * 1024, backupCount=2, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        pass  # no writable location: keep going without a file

    # Windowed executables have sys.stderr set to None.
    if sys.stderr is not None:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)

    def _log_uncaught(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logging.getLogger("uncaught").critical(
            "Uncaught exception", exc_info=(exc_type, exc_value, exc_tb)
        )

    sys.excepthook = _log_uncaught
