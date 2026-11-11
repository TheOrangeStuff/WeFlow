"""Render the main window in every §7.3 state, light and dark, for human review.

Images land in tests/artifacts/. Pixels are not compared.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.ui_states import CARD, EARLIER, EXPECTED_PANEL, PANEL_STATES
from WeFlow.ui.theme import theme

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("name", list(PANEL_STATES))
def test_screenshot_every_state(qtbot, window, bridge, event, name: str, scheme: str) -> None:  # type: ignore[no-untyped-def]
    theme.set_scheme(scheme)
    window.resize(560, 720)
    sample, card_in, paused = PANEL_STATES[name]
    if paused:
        bridge.set_paused(True)
    if card_in:
        window._cards[CARD] = "EOS_DIGITAL"
    if sample is not None:
        bridge.batch_updated.emit(replace(sample, event_id=event.id))
    if name in ("safe", "working-uploading", "attention"):
        for earlier in EARLIER:
            bridge.batch_updated.emit(replace(earlier, event_id=event.id))
        window.drawer.add("info", "CARD_B: safe to remove. 180 photos copied and verified.")
    window.refresh()
    qtbot.wait(30)
    assert window.panel.state == EXPECTED_PANEL[name]
    ARTIFACTS.mkdir(exist_ok=True)
    shot = window.grab()
    assert shot.save(str(ARTIFACTS / f"main-{name}-{scheme}.png"))
    assert (shot.width(), shot.height()) == (560, 720)
    theme.set_scheme("light")
