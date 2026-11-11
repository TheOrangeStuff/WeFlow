"""Rules from CLAUDE.md that can be checked by reading the source."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "WeFlow"


def sources(folder: Path) -> list[Path]:
    return sorted(folder.rglob("*.py"))


def test_core_never_imports_qt() -> None:
    for path in sources(ROOT / "core"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"^\s*(from|import)\s+(PySide6|PyQt|shiboken)", text, re.M), path


def test_ui_reaches_core_only_through_bridge() -> None:
    pattern = re.compile(r"^\s*(from|import)\s+(WeFlow\.core|\.+core)\b", re.M)
    for path in sources(ROOT / "ui"):
        if path.name == "bridge.py":
            continue
        assert not pattern.search(path.read_text(encoding="utf-8")), path


def test_nfr06_secrets_only_touch_keyring_module() -> None:
    for path in sources(ROOT):
        if path.name == "secrets.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "import keyring" not in text and "keyring." not in text, path
