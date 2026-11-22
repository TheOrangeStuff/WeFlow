"""M4: the main window wired to the real pipeline through the bridge."""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt

from tests.fixtures.make_card import make_card
from tests.ui_states import batch
from WeFlow.core import ingest
from WeFlow.core.config import Profile, Store
from WeFlow.core.uploaders import FakeUploader
from WeFlow.ui.tray import STATES, Throttle, Tray, tray_icon
from WeFlow.ui.widgets import Button

NEW_PHOTOS = 7


def use_flickr(store: Store, event: Profile, **changes: object) -> Profile:
    event.destination, event.album = "flickr", "Gala Night"
    for key, value in changes.items():
        setattr(event, key, value)
    return store.update_event(event)


def insert_card(tmp_path: Path, name: str = "CARD_A") -> Path:
    card = tmp_path / "volumes" / name
    make_card(card, count=8)
    return card


def test_m4_fake_card_inserted_safe_then_done(  # type: ignore[no-untyped-def]
    qtbot, window, bridge, store, event, tmp_path, fake_uploader: FakeUploader
) -> None:
    use_flickr(store, event)
    bridge.events_changed.emit()
    window.done_fade_ms = 150
    notes: list[tuple[str, str, str]] = []
    window.notify.connect(lambda kind, title, message: notes.append((kind, title, message)))
    assert window.panel.state == "idle" and window.panel.subline.text() == "Photos go to Test Event"

    bridge.start()
    card = insert_card(tmp_path)

    # FR-10/11: detected, announced without a modal, with Cancel; nothing was asked.
    qtbot.waitUntil(lambda: window.notice_bar.isVisible(), timeout=3000)
    assert window.notice_bar.text.text() == "Card CARD_A → Test Event"
    assert window.notice_bar.cancel.isVisible() and window.notice_bar.cancel.text() == "Cancel"
    assert window.notice_bar.countdown_ms == 15000
    assert window.panel.state == "idle"  # not started yet
    window.notice_bar.start_now()  # same path the 15 s timer takes

    qtbot.waitUntil(lambda: window.panel.state == "safe", timeout=10000)
    assert window.panel.headline.text() == "Safe to remove"
    assert window.panel.subline.text() == "CARD_A · 7 photos copied and verified"
    assert window.panel.visible_buttons() == ["Eject", "Erase card"]
    assert window.panel.stages.states()[0] == "done"

    # Processing and upload carry on behind the Safe panel.
    qtbot.waitUntil(lambda: len(fake_uploader.album_adds) == NEW_PHOTOS, timeout=15000)
    assert window.panel.state == "safe"

    shutil.rmtree(card)  # the card comes out
    qtbot.waitUntil(lambda: window.panel.state == "done", timeout=5000)
    assert window.panel.headline.text() == "All done"
    assert window.panel.subline.text() == "7 photos uploaded to Flickr"
    assert window.status() == "idle"

    kinds = [kind for kind, _, _ in notes]
    assert kinds.index("card") < kinds.index("safe") and "done" in kinds
    assert window.activity.isVisible() and any(
        "safe to remove" in e[2] for e in window.drawer.entries
    )

    # Done fades to Idle; the finished card moves to "Earlier cards".
    qtbot.waitUntil(lambda: window.panel.state == "idle", timeout=3000)
    rows = window.rows()
    assert len(rows) == 1 and rows[0].text.text() == "CARD_A · 7 photos"
    assert rows[0].chip.text() == "Done" and rows[0].action.text() == "Open folder"


def test_fr11_cancel_stops_the_import(qtbot, window, bridge, tmp_path) -> None:  # type: ignore[no-untyped-def]
    bridge.start()
    insert_card(tmp_path)
    qtbot.waitUntil(lambda: window.notice_bar.isVisible(), timeout=3000)
    qtbot.mouseClick(window.notice_bar.cancel, Qt.MouseButton.LeftButton)
    assert not window.notice_bar.isVisible()
    qtbot.wait(300)
    assert bridge.snapshot() == [] and window.panel.state == "idle"


def test_fr11_countdown_starts_by_itself(qtbot, window, bridge, tmp_path) -> None:  # type: ignore[no-untyped-def]
    window.notice_bar.countdown_ms = 300
    bridge.start()
    insert_card(tmp_path)
    qtbot.waitUntil(lambda: window.notice_bar.isVisible(), timeout=3000)
    assert window.notice_bar.detail.text() == "Starts in 1 s"
    qtbot.waitUntil(lambda: len(bridge.snapshot()) == 1, timeout=3000)
    assert not window.notice_bar.isVisible()


def test_fr11_no_active_event_waits_for_a_choice(  # type: ignore[no-untyped-def]
    qtbot, window, bridge, store, event, tmp_path, monkeypatch
) -> None:
    store.set_active("")
    bridge.events_changed.emit()
    assert window.event_card.name.text() == "No event chosen"
    opened: list[bool] = []
    monkeypatch.setattr(window.event_card, "open_switcher", lambda: opened.append(True))
    bridge.start()
    insert_card(tmp_path)
    qtbot.waitUntil(lambda: opened == [True], timeout=3000)
    assert not window.notice_bar.isVisible() and bridge.snapshot() == []
    bridge.set_active(event.id)  # choosing an event starts the card notice
    assert window.notice_bar.isVisible()
    assert window.notice_bar.text.text() == "Card CARD_A → Test Event"


def test_fr02_switching_event_takes_two_clicks(qtbot, window, bridge, store) -> None:  # type: ignore[no-untyped-def]
    other = store.create_event("Other Event", activate=False)
    bridge.events_changed.emit()
    assert window.event_card.name.text() == "Test Event"
    menu = window.event_card.build_menu()  # click 1 opens this popover
    target = next(a for a in menu.actions() if a.text() == "Other Event")
    target.trigger()  # click 2
    assert bridge.active().id == other.id
    assert window.event_card.name.text() == "Other Event"
    assert window.panel.subline.text() == "Photos go to Other Event"


def test_fr16_eject_reports_result(qtbot, window, bridge, event, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    card = insert_card(tmp_path)
    window._cards[str(card)] = "CARD_A"
    bridge.batch_updated.emit(
        batch("done", event_id=event.id, source=str(card), label="CARD_A", copied=7, processed=7)
    )
    assert window.panel.state == "safe"
    with qtbot.waitSignal(bridge.eject_finished, timeout=3000) as signal:
        qtbot.mouseClick(window.panel.eject, Qt.MouseButton.LeftButton)
    assert signal.args == [str(card), True, "Card ejected."]
    assert window.panel.state == "done"  # the card no longer counts as in
    assert "ejected" in window.notice_bar.text.text()
    assert sorted(p.name for p in (card / "DCIM" / "100TEST").iterdir())  # FR-17: untouched

    from WeFlow.core import cards

    failure = "The card could not be ejected. Close other apps using it and try again."
    monkeypatch.setattr(cards, "eject", lambda path: (False, failure))
    window._cards[str(card)] = "CARD_A"
    window.refresh()
    with qtbot.waitSignal(bridge.eject_finished, timeout=3000):
        qtbot.mouseClick(window.panel.eject, Qt.MouseButton.LeftButton)
    assert window.notice_bar.text.text() == failure
    assert window.panel.state == "safe" and window.panel.eject.isEnabled()
    assert window.drawer.entries[0][1:] == ("error", failure)


def test_auto_eject_once_copy_is_verified(
    qtbot, window, bridge, event, tmp_path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """1.10.0: with "Eject the card when it's copied" on, the card ejects itself once,
    and only after every file is verified. Off by default."""
    from WeFlow.core import cards

    calls: list[object] = []

    def eject(path: object) -> tuple[bool, str]:
        calls.append(str(path))
        return True, "Card ejected."

    monkeypatch.setattr(cards, "eject", eject)
    card = str(insert_card(tmp_path))
    fields = {"event_id": event.id, "source": card, "label": "CARD_A", "copied": 7}

    assert bridge.settings.auto_eject is False
    window._cards[card] = "CARD_A"
    bridge.batch_updated.emit(batch("copying", verified=False, **fields))
    bridge.batch_updated.emit(batch("safe", **fields))
    qtbot.wait(50)
    assert calls == []  # the setting is off

    bridge.update_settings(auto_eject=True)
    fields.update(id=2001, started=9e9)
    bridge.batch_updated.emit(batch("copying", verified=False, **fields))
    qtbot.wait(20)
    assert calls == []  # never before the copy is verified
    with qtbot.waitSignal(bridge.eject_finished, timeout=3000):
        bridge.batch_updated.emit(batch("safe", **fields))
    assert calls == [card]
    assert window.panel.stages.views()[0].note == "Ejected"
    window._cards[card] = "CARD_A"
    bridge.batch_updated.emit(batch("processing", processed=3, **fields))
    qtbot.wait(50)
    assert calls == [card]  # once only


def test_fr34_held_panel_release(
    qtbot, window, bridge, store, event, tmp_path, fake_uploader
) -> None:  # type: ignore[no-untyped-def]
    use_flickr(store, event, hold_uploads=True)
    bridge.events_changed.emit()
    assert window.event_card.summary.text() == "Flickr · Gala Night · Held"
    folder = tmp_path / "folder"
    for shot in make_card(tmp_path / "scratch", count=5):
        folder.mkdir(exist_ok=True)
        shutil.copy(shot, folder / shot.name)
    window.import_folder(str(folder))  # FR-18
    qtbot.waitUntil(lambda: window.panel.state == "held", timeout=10000)
    assert window.panel.headline.text() == "Ready to upload"
    assert window.panel.subline.text() == "4 photos tuned. Upload them to Flickr?"
    assert window.panel.visible_buttons() == ["Upload", "Open folder"]
    assert fake_uploader.upload_calls == 0
    qtbot.mouseClick(window.panel.release, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.panel.state == "done", timeout=10000)
    assert len(fake_uploader.uploads) == 4


def test_fr35_attention_panel_retry_and_details(  # type: ignore[no-untyped-def]
    qtbot, window, bridge, store, event, tmp_path, fake_uploader
) -> None:
    use_flickr(store, event)
    fake_uploader.fail_times, fake_uploader.permanent = 4, True
    folder = tmp_path / "folder"
    for shot in make_card(tmp_path / "scratch", count=5):
        folder.mkdir(exist_ok=True)
        shutil.copy(shot, folder / shot.name)
    notes: list[str] = []
    window.notify.connect(lambda kind, title, message: notes.append(kind))
    window.import_folder(str(folder))
    qtbot.waitUntil(lambda: window.panel.state == "attention", timeout=10000)
    assert window.panel.subline.text() == "4 photos failed. Fake upload failure."
    qtbot.waitUntil(lambda: "attention" in notes, timeout=3000)  # may land a moment later
    assert window.status() == "attention"
    assert window.drawer.entries[0][1] == "error"  # also in the log, never only there
    qtbot.mouseClick(window.panel.details, Qt.MouseButton.LeftButton)
    assert window.drawer.is_open()
    window.drawer.close_drawer()
    qtbot.mouseClick(window.panel.retry, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.panel.state == "done", timeout=10000)
    assert len(fake_uploader.uploads) == 4


def test_copy_error_panel_never_safe(qtbot, window, bridge, event, tmp_path) -> None:  # type: ignore[no-untyped-def]
    missing = tmp_path / "volumes" / "GONE"
    window._cards[str(missing)] = "GONE"
    bridge.submit(str(missing), "GONE", True)
    qtbot.waitUntil(lambda: window.panel.state == "copy_error", timeout=5000)
    assert window.panel.headline.text() == "Don't remove yet — copy failed"
    assert "Re-insert the card to try again." in window.panel.subline.text()
    assert window.panel.visible_buttons() == ["Retry"] and window.status() == "attention"


def test_pause_stops_card_watching(qtbot, window, bridge, tmp_path) -> None:  # type: ignore[no-untyped-def]
    bridge.start()
    qtbot.mouseClick(window.pause, Qt.MouseButton.LeftButton)
    assert bridge.settings.paused and window.pause.text() == "Resume"
    assert window.pause.toolTip() == "Start watching for new cards again."
    assert window.panel.state == "paused" and window.panel.visible_buttons() == ["Resume"]
    insert_card(tmp_path)
    qtbot.wait(300)
    assert not window.notice_bar.isVisible()
    qtbot.mouseClick(window.panel.resume, Qt.MouseButton.LeftButton)
    assert not bridge.settings.paused and window.pause.text() == "Pause"
    assert "keep processing and uploading" in window.pause.toolTip()
    assert "Paced asks" in window.mode.toolTip()  # covers the words, not just the switch
    qtbot.waitUntil(lambda: window.notice_bar.isVisible(), timeout=3000)  # card already in


def test_ui05_at_most_four_always_visible_controls(qtbot, window) -> None:  # type: ignore[no-untyped-def]
    visible = [b for b in window.findChildren(Button) if b.isVisible()]
    names = sorted(b.accessibleName() or b.text() for b in visible)
    # Auto/Paced is one control in two halves (DECISIONS 52); Speed sits beside it (1.14.0).
    assert names == ["Auto mode", "Paced mode", "Pause", "Settings", "Switch event"]
    assert window.speed.isVisible()
    assert not window.earlier_label.isVisible() and not window.activity.isVisible()
    assert (window.width(), window.height()) == (960, 720)
    assert (window.minimumWidth(), window.minimumHeight()) == (760, 640)


def test_earlier_cards_newest_first_and_previous_sessions_hidden(  # type: ignore[no-untyped-def]
    qtbot, window, bridge, event
) -> None:
    for index in range(8):
        bridge.batch_updated.emit(
            batch("done", id=100 + index, label=f"CARD_{index}", started=100.0 + index)
        )
    bridge.batch_updated.emit(
        batch("done", id=50, label="OLD", started=50.0, previous_session=True)
    )
    bridge.batch_updated.emit(
        batch("attention", id=40, label="OLD_STUCK", started=40.0, previous_session=True)
    )
    rows = window.rows()
    labels = [row.batch.label for row in rows]
    assert labels == [f"CARD_{i}" for i in range(6, -1, -1)] + ["OLD_STUCK"]  # newest first
    assert "OLD" not in labels  # done in an earlier session
    assert window.earlier.height() == 2 * 44  # two rows show, the rest scroll
    assert window.panel.view.batch_id == 107


def test_close_hides_to_tray_while_working(qtbot, window, bridge, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    quits: list[bool] = []
    window.quit_requested.connect(lambda: quits.append(True))
    window.can_hide_to_tray = True
    monkeypatch.setattr(bridge, "has_pending", lambda: True)
    window.close()
    assert not window.isVisible() and quits == []
    window.show()
    monkeypatch.setattr(bridge, "has_pending", lambda: False)
    window.close()
    assert quits == [True]


def test_tray_states_menu_and_throttle(qtbot, bridge) -> None:  # type: ignore[no-untyped-def]
    tray = Tray()
    assert [a.text() for a in tray.menu.actions() if a.text()] == [
        "Show WeFlow",
        "Pause watching",
        "Quit",
    ]
    assert STATES == ("idle", "working", "safe", "attention")
    images = set()
    for state in STATES:
        tray.set_state(state)
        assert tray.state == state
        images.add(bytes(tray_icon(state).pixmap(22, 22).toImage().constBits()))
    assert len(images) == 4  # four different icons

    with qtbot.waitSignal(tray.pause_toggled) as signal:
        tray.pause_action.trigger()
    assert signal.args == [True]

    assert tray.notify("safe", "Safe to remove", "CARD_A") is True
    assert tray.notify("safe", "Safe to remove", "CARD_B") is False  # same type within 5 s
    assert tray.notify("done", "All done", "CARD_A") is True  # other types are separate
    throttle = Throttle()
    assert throttle.allow("card", now=0.0) and not throttle.allow("card", now=4.9)
    assert throttle.allow("card", now=5.1)


def test_nfr04_bridge_keeps_store_writes_off_the_ui_thread(qtbot, bridge, store, event) -> None:  # type: ignore[no-untyped-def]
    import threading

    writers: list[str] = []
    real = store._write

    def spy(path: Path, text: str) -> None:
        writers.append(threading.current_thread().name)
        real(path, text)

    store._write = spy  # type: ignore[method-assign]
    bridge.update_settings(card_sound=False)
    event.keywords = "x"
    bridge.save_event(event)
    qtbot.waitUntil(lambda: len(writers) >= 2, timeout=3000)
    assert all(name.startswith("weflow-io") for name in writers)


def test_bridge_ignores_work_after_shutdown(bridge, event) -> None:  # type: ignore[no-untyped-def]
    """A card countdown that fires while the app quits must not crash it."""
    bridge.shutdown()
    assert bridge.submit("/nowhere", "CARD", True) is True
    bridge.retry(None)
    bridge.run(lambda: None)


def test_fr25_speed_in_top_bar_saves_and_applies(qtbot, window, bridge, store, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("WeFlow_NO_MOTION", raising=False)
    window.show()
    slider = window.speed.slider
    before = slider.pos()
    assert window.speed.level == "medium"
    slider.setFocus()
    qtbot.keyClick(slider, Qt.Key.Key_Right)
    assert store.settings.speed == "high" and "Max" in window.speed.toolTip()
    qtbot.waitUntil(lambda: bridge.pipeline.speed == "high", timeout=3000)
    qtbot.wait(250)  # let the knob finish sliding
    # 1.12.0 glitch: the knob animation moved the whole slider, not just the knob.
    assert slider.pos() == before and slider._pos == 2.0
    qtbot.keyClick(slider, Qt.Key.Key_Home)
    assert store.settings.speed == "low"


def test_fr47_erase_card_needs_two_clicks(  # type: ignore[no-untyped-def]
    qtbot, window, bridge, tmp_path
) -> None:
    bridge.start()
    card = insert_card(tmp_path)
    qtbot.waitUntil(lambda: window.notice_bar.isVisible(), timeout=3000)
    window.notice_bar.start_now()
    qtbot.waitUntil(lambda: window.panel.state == "safe", timeout=10000)

    window.panel.erase.click()
    assert window.panel.erase.text() == "Really erase?"
    assert ingest.list_jpegs(card)  # one click never erases
    window.panel.erase.click()
    qtbot.waitUntil(lambda: window.panel.erase.text() == "Erase card", timeout=10000)
    assert ingest.list_jpegs(card) == []
    assert window.notice_bar.text.text() == "Erased 8 photos from the card."
