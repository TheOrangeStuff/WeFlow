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


def test_no_credentials_ship_with_the_app_or_sit_in_ci() -> None:
    """Each user enters their own API key and sign-in. None is built in or kept on GitHub."""
    repo = ROOT.parent
    assert not (ROOT / "core" / "_builtin_keys.py").exists()
    assert not (repo / "packaging" / "write_builtin_keys.py").exists()
    for path in sources(ROOT):
        assert "_builtin" not in path.read_text(encoding="utf-8"), path
    workflow = (repo / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert not re.search(r"API_KEY|API_SECRET|SMUGMUG|FLICKR", workflow, re.I)
    # The only CI secrets are for signing the installers and publishing them.
    assert set(re.findall(r"secrets\.([A-Z0-9_]+)", workflow)) == {
        "MACOS_CERT_P12_BASE64",
        "MACOS_CERT_PASSWORD",
        "MACOS_SIGN_IDENTITY",
        "APPLE_ID",
        "APPLE_TEAM_ID",
        "APPLE_APP_PASSWORD",
        "WINDOWS_CERT_PFX_BASE64",
        "WINDOWS_CERT_PASSWORD",
        "RELEASES_REPO_TOKEN",
    }
