"""Rotating log files in ``<app-data>/logs/`` (NFR-10). Never log secrets."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(data_dir: Path) -> None:
    folder = data_dir / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        folder / "weflow.log", maxBytes=1_000_000, backupCount=4, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("WeFlow")
    root.setLevel(logging.INFO)
    root.handlers[:] = [handler]
