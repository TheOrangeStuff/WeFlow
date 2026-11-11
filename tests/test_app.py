"""The assembled app: window, tray, dialogs and shutdown."""

from __future__ import annotations

import pytest

from WeFlow.ui.app import App
from WeFlow.ui.event_editor import EventEditor
from WeFlow.ui.settings_dialog import SettingsDialog


@pytest.fixture
def app(qtbot, qapp, bridge, event, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(qapp, "quit", lambda: None)
    made = App(qapp, bridge)
    qtbot.addWidget(made.window)
    made.start()
    yield made
    made.tray.hide()


def test_app_wires_window_tray_and_dialogs(qtbot, app, bridge) -> None:  # type: ignore[no-untyped-def]
    assert app.window.isVisible() and app.tray.state == "idle"
    app.window.status_changed.emit("safe")
    assert app.tray.state == "safe"

    app.window.notify.emit("safe", "Safe to remove", "CARD_A")
    app.window.notify.emit("safe", "Safe to remove", "CARD_A")
    assert app.tray.sent == [("safe", "Safe to remove", "CARD_A")]  # throttled

    app.tray.pause_action.trigger()
    assert bridge.settings.paused and app.window.panel.state == "paused"
    assert app.tray.pause_action.isChecked()
    app.window.pause.click()
    assert not bridge.settings.paused and not app.tray.pause_action.isChecked()

    settings = app.open_settings()
    assert isinstance(settings, SettingsDialog) and settings.isVisible()
    settings.done_button.click()
    assert not settings.isVisible()

    editors: list[EventEditor] = []
    real = app.open_event_editor
    app.window.edit_event_requested.disconnect()
    app.window.edit_event_requested.connect(lambda e: editors.append(real(e)))
    app.window.event_card.new_requested.emit()
    assert editors and editors[0].is_new and editors[0].isVisible()
    editors[0].reject()
    app.window.event_card.edit_requested.emit()
    assert not editors[1].is_new and editors[1].name.text() == "Test Event"
    editors[1].reject()

    app.window.hide()
    app.tray.show_action.trigger()
    assert app.window.isVisible()


def test_app_quit_stops_the_pipeline(qtbot, app, bridge) -> None:  # type: ignore[no-untyped-def]
    app.tray.quit_action.trigger()
    assert bridge._closed and bridge.pipeline._stopping.is_set()
    assert not app.tray.icon.isVisible()
