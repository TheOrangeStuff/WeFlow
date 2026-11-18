"""WCAG AA for every text and status colour pair, in both themes (SPEC §7.5)."""

from __future__ import annotations

import re

import pytest

from WeFlow.ui.theme import DARK, LIGHT, build_qss, contrast, mix, tokens

AA_TEXT = 4.5  # normal text
AA_UI = 3.0  # large text, icons and other non-text marks
SCHEMES = ["light", "dark"]
NEUTRAL = ["bg", "surface", "surface-2"]
TINTS = ["good-tint", "warn-tint", "bad-tint"]


@pytest.mark.parametrize("scheme", SCHEMES)
def test_spec_token_values(scheme: str) -> None:
    t = tokens(scheme)
    base = DARK if scheme == "dark" else LIGHT
    assert all(t[name] == value for name, value in base.items())
    alpha = 0.16 if scheme == "dark" else 0.12
    for status in ("good", "warn", "bad"):
        assert t[f"{status}-tint"] == mix(t[status], t["surface"], alpha)
    assert LIGHT["accent"] == "#157A3A" and DARK["bg"] == "#111113"


@pytest.mark.parametrize("scheme", SCHEMES)
def test_body_text_on_every_background(scheme: str) -> None:
    t = tokens(scheme)
    for background in NEUTRAL + TINTS:
        assert contrast(t["text"], t[background]) >= AA_TEXT, background


@pytest.mark.parametrize("scheme", SCHEMES)
def test_secondary_text_on_neutral_backgrounds(scheme: str) -> None:
    """text-2 is only used on neutral surfaces; tinted panels use `text`."""
    t = tokens(scheme)
    for background in NEUTRAL:
        assert contrast(t["text-2"], t[background]) >= AA_TEXT, background


@pytest.mark.parametrize("scheme", SCHEMES)
@pytest.mark.parametrize("status", ["good", "warn", "bad"])
def test_status_text_and_icons(scheme: str, status: str) -> None:
    t = tokens(scheme)
    strong = t[f"{status}-strong"]
    for background in (f"{status}-tint", "surface-2", "bg"):  # chips, panel icons, buttons
        assert contrast(strong, t[background]) >= AA_TEXT, background
    # Raw good/bad mark panel edges and tracker fills against the window (non-text: 3:1).
    # Raw warn is never drawn on its own; it only feeds warn-tint and warn-strong.
    if status != "warn":
        assert contrast(t[status], t["bg"]) >= AA_UI


@pytest.mark.parametrize("scheme", SCHEMES)
def test_accent_text_and_primary_button(scheme: str) -> None:
    t = tokens(scheme)
    for background in NEUTRAL:  # text buttons and links never sit on a tint
        assert contrast(t["accent"], t[background]) >= AA_TEXT, background
    assert contrast(t["on-accent"], t["accent"]) >= AA_TEXT
    assert contrast(t["on-accent"], t["accent-hover"]) >= AA_TEXT
    assert contrast(t["accent"], t["bg"]) >= AA_UI  # focus ring


@pytest.mark.parametrize("scheme", SCHEMES)
def test_stylesheet_uses_only_token_colours(scheme: str) -> None:
    t = tokens(scheme)
    qss = build_qss(t)
    allowed = {v.upper() for v in t.values() if v.startswith("#")}
    used = {c.upper() for c in re.findall(r"#[0-9A-Fa-f]{6}\b", qss)}
    assert used <= allowed
    assert "gradient" not in qss


def test_no_hard_coded_colours_in_widgets() -> None:
    from pathlib import Path

    ui = Path(__file__).resolve().parent.parent / "WeFlow" / "ui"
    for path in ui.rglob("*.py"):
        if path.name == "theme.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"#[0-9A-Fa-f]{6}\b|#[0-9A-Fa-f]{3}\b|rgba?\(", text), path
        assert not re.search(r"QColor\(\s*[\"'\d]", text), path
        assert not re.search(r"GlobalColor\.(?!transparent)", text), path
