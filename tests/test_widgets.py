"""M3: widgets and every status-panel state (SPEC §7.2 – §7.3)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.ui_states import CARD, EXPECTED_PANEL, PANEL_STATES, batch
from WeFlow.core.config import Profile
from WeFlow.ui.theme import theme
from WeFlow.ui.widgets import CardRow, Chip, EventCard, StatusPanel, StepTracker, Toggle, view_for
from WeFlow.ui.widgets.activity_drawer import WIDTH, ActivityDrawer
from WeFlow.ui.widgets.event_card import event_summary
from WeFlow.ui.widgets.status_panel import LOOKS

HEADLINES = {
    "idle": "Insert a card",
    "paused": "Paused",
    "copying": "Copying — keep card in",
    "safe": "Safe to remove",
    "working-processing": "Processing",
    "working-uploading": "Uploading",
    "ready": "Safe to remove",
    "held": "Ready to upload",
    "done": "All done",
    "attention": "Needs attention",
    "copy-error": "Don't remove yet — copy failed",
}
BUTTONS = {
    "paused": ["Resume"],
    "safe": ["Eject"],
    "ready": ["Process", "Eject"],
    "held": ["Upload", "Open folder"],
    "attention": ["Retry", "Details"],
    "copy-error": ["Retry"],
}


@pytest.fixture
def themed(bridge):  # type: ignore[no-untyped-def]
    return bridge


@pytest.mark.parametrize("name", list(PANEL_STATES))
def test_ui02_status_panel_state(qtbot, themed, name: str) -> None:  # type: ignore[no-untyped-def]
    sample, card_in, paused = PANEL_STATES[name]
    view = view_for(sample, event_name="Smith Wedding", paused=paused, card_present=card_in)
    panel = StatusPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.show_view(view)
    assert panel.state == EXPECTED_PANEL[name]
    assert panel.headline.text() == HEADLINES[name]
    assert panel.property("tone") == LOOKS[panel.state][0]
    # Actions appear only when they apply (UI-06).
    assert panel.visible_buttons() == BUTTONS.get(name, [])
    shown = [b.text() for b in panel._all_buttons if b.isVisible()]
    assert sorted(shown) == sorted(BUTTONS.get(name, []))
    # Tracker and progress only where there is progress to show.
    assert panel.tracker.isVisible() == (view.step >= 0)
    assert panel.progress.isVisible() == (view.progress is not None)
    assert len(view.subline) > 0


def test_ui02_sublines_match_spec() -> None:
    def sub(name: str) -> str:
        sample, card_in, paused = PANEL_STATES[name]
        return view_for(sample, event_name="Gala", paused=paused, card_present=card_in).subline

    assert sub("idle") == "Photos go to Gala"
    assert sub("paused") == "Card watching is off"
    assert sub("copying") == "100 of 248 photos"
    assert sub("safe") == "EOS_DIGITAL · 248 photos copied and verified"
    assert sub("working-processing") == "120 of 248 photos"
    assert sub("ready") == "248 photos copied. Process and auto-tune them?"
    assert sub("held") == "248 photos tuned. Upload them to SmugMug?"
    assert sub("done") == "248 photos uploaded to SmugMug"
    assert sub("copy-error").endswith("Re-insert the card to try again.")
    assert sub("copy-error").count(".") <= 2  # NFR-09


def test_ui03_safe_needs_verified_copy_and_card_present() -> None:
    copying = batch("copying", copied=247, verified=False)
    assert view_for(copying, card_present=True).state == "copying"
    failed = batch("error", verified=False, error="IMG_1.jpg did not copy correctly.")
    assert view_for(failed, card_present=True).state == "copy_error"  # FR-15
    verified = batch("uploading", processed=248, uploaded=10)
    assert view_for(verified, card_present=True).state == "safe"
    assert view_for(verified, card_present=False).state == "working"
    folder = batch("processing", is_card=False)
    assert view_for(folder, card_present=True).state == "working"  # FR-18: never "safe"
    done_in_slot = view_for(batch("done", processed=248, uploaded=248), card_present=True)
    assert done_in_slot.state == "safe" and done_in_slot.step == 4


def test_safe_tracker_continues_through_process_and_upload() -> None:
    assert view_for(batch("processing", processed=3), card_present=True).step == 1
    assert view_for(batch("uploading", processed=248), card_present=True).step == 2
    assert view_for(batch("copying", verified=False), card_present=True).step == 0


def test_step_tracker_states(qtbot, themed) -> None:  # type: ignore[no-untyped-def]
    tracker = StepTracker()
    qtbot.addWidget(tracker)
    tracker.set_current(2)
    assert [tracker.step_state(i) for i in range(4)] == ["done", "done", "current", "pending"]
    tracker.set_current(4)
    assert {tracker.step_state(i) for i in range(4)} == {"done"}
    tracker.resize(400, 44)
    assert not tracker.grab().isNull()


def test_chip_pairs_colour_with_icon_and_word(qtbot, themed) -> None:  # type: ignore[no-untyped-def]
    for state, word, tone in [
        ("done", "Done", "good"),
        ("held", "Held", "warn"),
        ("uploading", "Uploading", "warn"),
        ("attention", "Needs attention", "bad"),
        ("error", "Copy failed", "bad"),
    ]:
        chip = Chip()
        qtbot.addWidget(chip)
        chip.set_batch(batch(state))
        assert (chip.text(), chip.tone) == (word, tone)
        assert chip._icon.pixmap() is not None and not chip._icon.pixmap().isNull()


def test_ui04_card_row_action_only_when_applicable(qtbot, themed) -> None:  # type: ignore[no-untyped-def]
    row = CardRow(batch("uploading", label="CARD_B", copied=180))
    qtbot.addWidget(row)
    row.show()
    assert row.text.text() == "CARD_B · 180 photos" and row.height() == 44
    assert not row.action.isVisible()
    for state, text, name in [
        ("attention", "Retry", "retry"),
        ("ready", "Process", "process"),
        ("held", "Upload", "release"),
        ("done", "Open folder", "open"),
    ]:
        row.set_batch(batch(state))
        assert row.action.isVisible() and row.action.text() == text
        with qtbot.waitSignal(row.action_clicked) as signal:
            row.action.click()
        assert signal.args[0] == name
    row.set_batch(batch("processing"))
    assert not row.action.isVisible()


def test_ui01_event_card_summary_shows_only_what_applies(qtbot, themed) -> None:  # type: ignore[no-untyped-def]
    plain = Profile(name="Local")
    assert event_summary(plain) == "Library only"
    full = Profile(
        name="Smith Wedding",
        destination="smugmug",
        album="https://me.smugmug.com/Weddings/Smith/n-Ab12Cd",
        watermark=True,
        hold_uploads=True,
    )
    assert event_summary(full) == "SmugMug · Smith · Watermark · Held"
    flickr = Profile(name="Gala", destination="flickr", album="Gala Night")
    assert event_summary(flickr) == "Flickr · Gala Night"

    card = EventCard()
    qtbot.addWidget(card)
    card.set_events([plain, full], full)
    assert card.name.text() == "Smith Wedding"
    entries = [a.text() for a in card.build_menu().actions() if a.text()]
    assert entries == ["Local", "Smith Wedding", "New event…", "Edit this event…", "Import folder…"]
    with qtbot.waitSignal(card.switch_requested) as signal:
        card.menu.actions()[0].trigger()
    assert signal.args == [plain.id]
    card.set_events([], None)
    assert card.name.text() == "No event chosen"
    assert [a.text() for a in card.build_menu().actions() if a.text()] == ["New event…"]


def test_ui07_activity_drawer_opens_from_the_right(qtbot, themed) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtWidgets import QWidget

    host = QWidget()
    qtbot.addWidget(host)
    host.resize(560, 720)
    host.show()
    drawer = ActivityDrawer(host)
    assert not drawer.isVisible() and drawer.width() == WIDTH == 320
    drawer.add("error", "CARD_A: 2 photos failed. Flickr is not connected. Connect it in Settings.")
    drawer.add("info", "CARD_A: all done.")
    assert [e[2][:6] for e in drawer.entries] == ["CARD_A", "CARD_A"] and drawer.entries[0][
        1
    ] == "info"
    drawer.open_drawer()
    assert drawer.isVisible() and drawer.x() == 560 - 320 and drawer.height() == 720
    host.resize(700, 600)
    assert drawer.x() == 700 - 320
    drawer.close_drawer()
    assert not drawer.isVisible()


def test_toggle_switches_with_click_and_keyboard(qtbot, themed) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtCore import Qt

    toggle = Toggle()
    qtbot.addWidget(toggle)
    toggle.show()
    qtbot.mouseClick(toggle, Qt.MouseButton.LeftButton)
    assert toggle.isChecked()
    qtbot.keyClick(toggle, Qt.Key.Key_Space)
    assert not toggle.isChecked()


def test_theme_switches_live(qtbot, themed, qapp) -> None:  # type: ignore[no-untyped-def]
    panel = StatusPanel()
    qtbot.addWidget(panel)
    seen: list[str] = []
    theme.changed.connect(lambda: seen.append(theme.scheme))
    theme.set_scheme("dark")
    assert theme.t["bg"] == "#111113" and "#111113" in qapp.styleSheet()
    theme.set_scheme("light")
    assert theme.t["bg"] == "#F7F7F8" and "#F7F7F8" in qapp.styleSheet()
    assert seen == ["dark", "light"]


def test_panel_crossfades_between_states(qtbot, themed, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("WeFlow_NO_MOTION")
    panel = StatusPanel()
    qtbot.addWidget(panel)
    panel.resize(500, 400)
    panel.show()
    qtbot.waitExposed(panel)
    panel.show_view(view_for(batch("copying", copied=5, verified=False), card_present=True))
    assert panel._fade is not None and panel._fade_anim.duration() == 200
    qtbot.waitUntil(lambda: panel._fade is None, timeout=2000)
    same_state = replace(panel.view, subline="6 of 248 photos")
    panel.show_view(same_state)
    assert panel._fade is None  # counter changes do not re-run the fade


def test_card_constant_matches_fixture() -> None:
    assert CARD.startswith("/Volumes/")
