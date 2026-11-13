"""Rotating log files in ``<app-data>/logs/`` (NFR-10). Never log secrets."""

from __future__ import annotations

import logging
import platform
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_NAME = "weflow.log"
LOG_MAX_BYTES = 512_000  # per file; with 2 backups the logs stay under 1.5 MB
LOG_BACKUPS = 2
COPY_MAX_CHARS = 60_000  # "Copy log" gives the newest part, small enough to paste


def log_folder(data_dir: Path) -> Path:
    return data_dir / "logs"


def setup_logging(data_dir: Path) -> None:
    from .. import __version__

    folder = log_folder(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        folder / LOG_NAME, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("WeFlow")
    root.setLevel(logging.INFO)
    root.handlers[:] = [handler]
    root.info("WeFlow %s started on %s", __version__, platform.platform())


def recent_log(data_dir: Path, limit: int = COPY_MAX_CHARS) -> str:
    """The newest ``limit`` characters of the log, starting at a whole line.

    Reads the previous file too when the current one has only just rotated.
    """
    folder = log_folder(data_dir)
    text = ""
    for path in (folder / f"{LOG_NAME}.1", folder / LOG_NAME):
        try:
            text += path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    if len(text) > limit:
        text = text[-limit:]
        text = text.split("\n", 1)[1] if "\n" in text else text
    return text
