"""M5: event editor with live preview, Settings with Connect flows, first run."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from tests.fixtures.make_card import save_photo
from WeFlow.core import secrets, uploaders
from WeFlow.core.config import Store
from WeFlow.core.uploaders import PendingAuth, UploadError
from WeFlow.ui.event_editor import PREVIEW_DEBOUNCE_MS, EventEditor
from WeFlow.ui.first_run import FirstRun
from WeFlow.ui.settings_dialog import SettingsDialog

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"


def shoot(widget, name: str) -> None:  # type: ignore[no-untyped-def]
    ARTIFACTS.mkdir(exist_ok=True)
    QApplication.processEvents()
    assert widget.grab().save(str(ARTIFACTS / f"{name}.png"))


@pytest.fixture
def sample(tmp_path: Path) -> str:
    path = tmp_path / "sample.jpg"
    save_photo(path, 3, size=(900, 600))
    return str(path)


@pytest.fixture
def watermark(tmp_path: Path) -> str:
    path = tmp_path / "logo.png"
    Image.new("RGBA", (300, 100), (255, 255, 255, 220)).save(path)
    return str(path)


# ------------------------------------------------------------------ event editor
def test_m5_create_event_copies_active_and_becomes_active(
    qtbot, window, bridge, store, event
) -> None:  # type: ignore[no-untyped-def]
    event.destination, event.album, event.keywords = "flickr", "Gala", "gala"
    bridge.save_event(event)
    editor = EventEditor(bridge, bridge.draft_event(), window)
    qtbot.addWidget(editor)
    editor.show()
    assert editor.is_new and editor.width() == 640
    assert editor.name.text().endswith("New Event")  # FR-03: dated name, copied settings
    assert editor.destination.currentData() == "flickr" and editor.album.text() == "Gala"
    assert not editor.delete.isVisible() and not editor.duplicate.isVisible()
    editor.name.setText("Smith Wedding")
    assert editor.save.isEnabled()
    qtbot.mouseClick(editor.save, Qt.MouseButton.LeftButton)
    assert editor.saved is not None and editor.saved.folder == "Smith-Wedding"
    assert bridge.active().name == "Smith Wedding"
    assert window.event_card.name.text() == "Smith Wedding"
    assert len(store.events()) == 2


def test_m5_edit_event_and_frozen_folder(qtbot, window, bridge, store, event) -> None:  # type: ignore[no-untyped-def]
    editor = EventEditor(bridge, bridge.active(), window)
    qtbot.addWidget(editor)
    editor.show()
    assert not editor.is_new and editor.delete.isVisible() and editor.duplicate.isVisible()
    editor.name.setText("Renamed")
    editor.destination.setCurrentIndex(1)  # SmugMug
    editor.album.setText("https://me.smugmug.com/Events/Gala/n-Ab12Cd")
    editor.keywords.setText("gala, 2026")
    editor.hold.setChecked(True)
    editor.auto_wb.setChecked(False)
    editor.wb_strength.slider.setValue(35)
    editor.quality.setValue(80)
    editor.resize_edge.setValue(2400)
    qtbot.mouseClick(editor.save, Qt.MouseButton.LeftButton)
    saved = store.get(event.id)
    assert saved.name == "Renamed" and saved.folder == "Test-Event"
    assert (saved.destination, saved.keywords, saved.hold_uploads) == (
        "smugmug",
        "gala, 2026",
        True,
    )
    assert (saved.auto_wb, saved.wb_strength) == (False, 0.35)
    assert (saved.jpeg_quality, saved.max_long_edge) == (80, 2400)
    assert window.event_card.summary.text() == "SmugMug · Gala · Held"


def test_fr39_pick_smugmug_gallery_from_list(
    qtbot, window, bridge, store, event, fake_uploader
) -> None:  # type: ignore[no-untyped-def]
    fake_uploader.albums = [
        uploaders.AlbumChoice("/api/v2/album/B2", "Smith Wedding", "NB2", "Weddings"),
        uploaders.AlbumChoice("/api/v2/album/A1", "Gala", "NA1", "Events"),
        uploaders.AlbumChoice("/api/v2/album/C3", "Gala", "NC3", "Archive"),
    ]
    event.destination, event.album = "smugmug", "https://me.smugmug.com/Events/Gala/n-NA1"
    bridge.save_event(event)
    editor = EventEditor(bridge, bridge.active(), window)
    qtbot.addWidget(editor)
    editor.show()
    qtbot.waitUntil(lambda: editor.gallery.count() == 3, timeout=3000)
    assert editor.album.text() == "Gala — Events"  # the pasted link, shown by name
    assert not editor.album_hint.isVisible()
    editor.gallery.setCurrentIndex(0)
    assert editor.album.text() == "Smith Wedding"
    qtbot.mouseClick(editor.save, Qt.MouseButton.LeftButton)
    saved = store.get(event.id)
    assert (saved.album, saved.album_name) == ("/api/v2/album/B2", "Smith Wedding")
    assert window.event_card.summary.text() == "SmugMug · Smith Wedding"

    # Reopened while the list is unavailable: the saved gallery is kept as it is.
    fake_uploader.list_error = UploadError("SmugMug is not connected. Connect it in Settings.")
    again = EventEditor(bridge, bridge.active(), window)
    qtbot.addWidget(again)
    again.show()
    qtbot.waitUntil(lambda: again.album_hint.text().startswith("SmugMug is not"), timeout=3000)
    assert again.album.text() == "Smith Wedding" and again.save.isEnabled()
    assert (again.current().album, again.current().album_name) == (
        "/api/v2/album/B2",
        "Smith Wedding",
    )
    again.album.setText("https://me.smugmug.com/Other/n-Zz9")  # pasting still works
    assert (again.current().album, again.current().album_name) == (
        "https://me.smugmug.com/Other/n-Zz9",
        "",
    )


def test_m5_inline_validation_disables_save(qtbot, window, bridge, store, event, watermark) -> None:  # type: ignore[no-untyped-def]
    store.create_event("Taken Name", activate=False)
    editor = EventEditor(bridge, bridge.active(), window)
    qtbot.addWidget(editor)
    editor.show()
    assert editor.save.isEnabled() and not editor.name_error.isVisible()

    editor.name.setText("")
    assert not editor.save.isEnabled()
    assert editor.name_error.isVisible() and editor.name_error.text() == "Give the event a name."
    assert editor.name.property("invalid") is True
    editor.name.setText("taken name")
    assert editor.name_error.text() == "Another event already uses this name."
    editor.name.setText("Fine")
    assert editor.save.isEnabled() and not editor.name_error.isVisible()

    assert not editor.album_field.isVisible()  # only shown when it applies
    editor.destination.setCurrentIndex(2)  # Flickr
    assert editor.album_field.isVisible() and editor.privacy_field.isVisible()
    assert editor.album_error.text() == "Type the Flickr album title."
    assert not editor.save.isEnabled()
    editor.album.setText("Gala Night")
    assert editor.save.isEnabled()

    assert not editor.watermark_box.isVisible()
    editor.watermark.setChecked(True)
    assert editor.watermark_box.isVisible() and not editor.save.isEnabled()
    assert editor.watermark_error.text() == "Choose a PNG file for the watermark."
    editor.set_watermark_path(watermark)
    qtbot.waitUntil(lambda: editor.watermark_thumb.pixmap() is not None, timeout=3000)
    qtbot.waitUntil(lambda: not editor.watermark_thumb.pixmap().isNull(), timeout=3000)
    assert editor.save.isEnabled() and editor.watermark_name.text() == "logo.png"

    broken = Path(watermark).with_name("broken.png")
    broken.write_bytes(b"not a png")
    editor.set_watermark_path(str(broken))
    qtbot.waitUntil(lambda: editor.watermark_error.isVisible(), timeout=3000)
    assert editor.watermark_error.text() == "This file can't be opened. Choose a PNG image."
    assert not editor.save.isEnabled()
    shoot(editor, "editor-validation-light")


def test_m5_position_grid_only_corners_and_center(qtbot, window, bridge, event) -> None:  # type: ignore[no-untyped-def]
    editor = EventEditor(bridge, bridge.active(), window)
    qtbot.addWidget(editor)
    cells = editor.position.findChildren(type(editor.save))
    assert len(cells) == 9 and sum(c.isEnabled() for c in cells) == 5
    assert set(editor.position.cells) == {"tl", "tr", "center", "bl", "br"}
    assert editor.position.position() == "br"
    editor.position.cells["tl"].click()
    assert editor.position.position() == "tl" and editor.current().watermark_position == "tl"
    assert editor.more_box.isHidden()  # rarely changed fields start collapsed
    editor.more.click()
    assert not editor.more_box.isHidden()


def test_fr04_live_preview_renders_off_the_ui_thread_within_500ms(  # type: ignore[no-untyped-def]
    qtbot, window, bridge, event, sample, watermark, monkeypatch
) -> None:
    from WeFlow.core import imaging

    threads: list[str] = []
    real = imaging.render

    def spy(*args, **kwargs):  # type: ignore[no-untyped-def]
        threads.append(threading.current_thread().name)
        return real(*args, **kwargs)

    monkeypatch.setattr(imaging, "render", spy)
    editor = EventEditor(bridge, bridge.active(), window)
    qtbot.addWidget(editor)
    editor.show()
    assert not editor.preview.has_images()
    assert "Choose a sample photo" in editor.preview.message
    editor.set_sample(sample)
    qtbot.waitUntil(editor.preview.has_images, timeout=3000)
    assert bridge.settings.sample_photo == sample

    assert PREVIEW_DEBOUNCE_MS < 500
    for value in (20, 60, 90):  # three quick changes become one render
        editor.wb_strength.slider.setValue(value)
    rendered = editor.previews_rendered
    with qtbot.waitSignal(bridge.preview_ready, timeout=500):  # FR-04: within 500 ms
        pass
    qtbot.waitUntil(lambda: editor.previews_rendered == rendered + 1, timeout=500)
    qtbot.wait(PREVIEW_DEBOUNCE_MS + 100)
    assert editor.previews_rendered == rendered + 1  # debounced

    editor.watermark.setChecked(True)
    editor.set_watermark_path(watermark)
    qtbot.waitUntil(lambda: editor.previews_rendered >= rendered + 2, timeout=3000)
    assert threads and all(name.startswith("weflow-job") for name in threads)
    assert threading.current_thread().name == "MainThread"

    editor.preview.resize(280, 300)
    start = editor.preview.split
    qtbot.mousePress(editor.preview, Qt.MouseButton.LeftButton, pos=editor.preview.rect().center())
    qtbot.keyClick(editor.preview, Qt.Key.Key_Right)
    assert editor.preview.split != start or start == 0.55
    shoot(editor, "editor-preview-light")


def test_fr01_duplicate_and_delete_from_editor(
    qtbot, window, bridge, store, event, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    editor = EventEditor(bridge, bridge.active(), window)
    qtbot.addWidget(editor)
    editor.show()
    qtbot.mouseClick(editor.duplicate, Qt.MouseButton.LeftButton)
    assert sorted(e.name for e in store.events()) == ["Test Event", "Test Event copy"]
    assert bridge.active().id == event.id

    editor = EventEditor(bridge, bridge.active(), window)
    qtbot.addWidget(editor)
    editor.show()
    qtbot.mouseClick(editor.delete, Qt.MouseButton.LeftButton)
    assert editor.delete.text() == "Really delete?" and store.get(event.id) is not None
    monkeypatch.setattr(bridge.pipeline, "has_pending", lambda event_id=None: True)
    qtbot.mouseClick(editor.delete, Qt.MouseButton.LeftButton)
    assert store.get(event.id) is not None and editor.footer_error.isVisible()
    monkeypatch.setattr(bridge.pipeline, "has_pending", lambda event_id=None: False)
    qtbot.mouseClick(editor.delete, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(editor.delete, Qt.MouseButton.LeftButton)
    assert store.get(event.id) is None and bridge.active() is None
    assert window.event_card.name.text() == "No event chosen"


# ---------------------------------------------------------------------- settings
def fake_services(monkeypatch: pytest.MonkeyPatch, opened: list[str]) -> None:
    """Replace the network steps of both connect flows. No real API is called."""

    def begin(service: str):  # type: ignore[no-untyped-def]
        def run() -> PendingAuth:
            key, secret = uploaders.api_credentials(service)
            if not key or not secret:
                raise UploadError(f"Enter a {uploaders.service_name(service)} API key first.", True)
            return PendingAuth(f"https://example.test/{service}/authorize", "rt", "rs")

        return run

    def finish(service: str):  # type: ignore[no-untyped-def]
        def run(pending: PendingAuth, code: str) -> None:
            if code != "123456":
                raise UploadError("That code was not accepted. Press Connect and try again.", True)
            secrets.set_secret(f"{service}_token", "access")
            secrets.set_secret(f"{service}_token_secret", "access-secret")

        return run

    from WeFlow.ui import bridge as bridge_module

    for service in ("smugmug", "flickr"):
        monkeypatch.setitem(bridge_module._BEGIN, service, begin(service))
        monkeypatch.setitem(bridge_module._FINISH, service, finish(service))
    monkeypatch.setattr(bridge_module.Bridge, "open_url", staticmethod(opened.append))


def test_fr37_connect_flow_in_settings(qtbot, window, bridge, store, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    opened: list[str] = []
    fake_services(monkeypatch, opened)
    dialog = SettingsDialog(bridge, window)
    qtbot.addWidget(dialog)
    dialog.show()
    row = dialog.services["smugmug"]
    assert row.chip.text() == "Not connected" and row.connect_button.text() == "Connect"
    assert not row.disconnect.isVisible()
    # WeFlow ships with no API key: the fields are there from the start.
    assert row.keys_box.isVisible() and not row.own_key.isVisible()
    assert row.get_key.isVisible() and row.get_key.text() == "Get a SmugMug API key…"
    row.get_key.click()
    assert opened == [uploaders.API_KEY_PAGES["smugmug"]]
    opened.clear()

    qtbot.mouseClick(row.connect_button, Qt.MouseButton.LeftButton)  # no API key yet
    qtbot.waitUntil(lambda: row.error.isVisible(), timeout=3000)
    assert row.error.text() == "Enter a SmugMug API key first." and row.keys_box.isVisible()

    row.api_key.setText("my-key")
    qtbot.mouseClick(row.connect_button, Qt.MouseButton.LeftButton)
    assert row.error.text() == "Enter both the API key and the API secret."
    row.api_secret.setText("my-secret")
    qtbot.mouseClick(row.connect_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: row.code_box.isVisible(), timeout=3000)
    assert opened == ["https://example.test/smugmug/authorize"]
    assert secrets.get_secret(secrets.SMUGMUG_API_KEY) == "my-key"

    row.code.setText("000000")
    qtbot.mouseClick(row.finish, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: row.error.isVisible(), timeout=3000)
    assert "not accepted" in row.error.text() and row.chip.text() == "Not connected"

    row.code.setText("123456")
    qtbot.mouseClick(row.finish, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: row.chip.text() == "Connected", timeout=3000)
    assert row.connect_button.text() == "Reconnect" and row.disconnect.isVisible()
    assert not row.code_box.isVisible()
    # The key is stored now, so its fields fold away behind a link.
    assert not row.keys_box.isVisible() and row.own_key.text() == "Change API key…"
    assert row.own_key.isVisible() and not row.get_key.isVisible()
    assert dialog.services["flickr"].chip.text() == "Not connected"
    assert dialog.services["flickr"].keys_box.isVisible()
    shoot(dialog, "settings-light")

    # NFR-06: nothing secret reached the JSON files.
    QApplication.processEvents()
    bridge._io.submit(lambda: None).result()
    on_disk = "".join(p.read_text() for p in store.data_dir.glob("*.json"))
    assert "my-secret" not in on_disk and "access-secret" not in on_disk and "my-key" not in on_disk

    qtbot.mouseClick(row.disconnect, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: row.chip.text() == "Not connected", timeout=3000)
    assert secrets.get_secret(secrets.SMUGMUG_TOKEN) == ""


def test_settings_library_sound_and_login(
    qtbot, window, bridge, store, tmp_path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from WeFlow.core import autostart

    calls: list[bool] = []
    monkeypatch.setattr(autostart, "set_start_at_login", lambda on: calls.append(on) or True)
    dialog = SettingsDialog(bridge, window)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.library.path.toolTip() == str(tmp_path / "library")
    dialog.library.set_library(str(tmp_path / "elsewhere"))
    assert store.library == tmp_path / "elsewhere"

    assert dialog.sound.isChecked()
    dialog.sound.click()
    assert store.settings.card_sound is False
    dialog.login.click()
    qtbot.waitUntil(lambda: calls == [True], timeout=3000)
    assert store.settings.start_at_login is True

    monkeypatch.setattr(bridge.pipeline, "has_pending", lambda event_id=None: True)
    dialog.library.refresh()
    assert not dialog.library.change.isEnabled()
    assert "still working" in dialog.library.note.text()


def test_nfr10_copy_log_from_settings(qtbot, window, bridge, store) -> None:  # type: ignore[no-untyped-def]
    folder = store.data_dir / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "weflow.log").write_text("line one\nSmugMug GET /api/v2!authuser -> 200\n")
    dialog = SettingsDialog(bridge, window)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.mouseClick(dialog.copy_log, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: dialog.copy_log.text() == "Copied", timeout=3000)
    shoot(dialog, "settings-copy-log-light")
    assert "authuser -> 200" in QApplication.clipboard().text()


def test_fr41_shows_twenty_newest_then_more(
    qtbot, window, bridge, store, event, fake_uploader
) -> None:  # type: ignore[no-untyped-def]
    fake_uploader.albums = [
        uploaders.AlbumChoice(f"/api/v2/album/G{i}", f"Gallery {i}") for i in range(45)
    ]
    event.destination, event.album = "smugmug", "/api/v2/album/G30"  # an older one
    bridge.save_event(event)
    editor = EventEditor(bridge, bridge.active(), window)
    qtbot.addWidget(editor)
    editor.show()
    gallery = editor.gallery
    qtbot.waitUntil(lambda: gallery.count() == 22, timeout=3000)  # 20 + saved + More
    assert gallery.itemText(0) == "Gallery 0" and gallery.itemText(20) == "Gallery 30"
    assert gallery.itemText(21) == "More galleries…" and editor.album.text() == "Gallery 30"
    gallery.setCurrentIndex(21)  # pick "More galleries…"
    qtbot.waitUntil(lambda: gallery.count() == 41, timeout=3000)  # 40 + More
    assert editor.album.text() == "Gallery 30"  # the choice is kept
    assert editor.current().album == "/api/v2/album/G30"
    gallery.hidePopup()
    editor.album.setText("Gallery 44")  # typing an older name still finds it
    assert editor.current().album == "/api/v2/album/G44"


# --------------------------------------------------------------------- first run
def test_fr41_new_gallery_from_the_editor(
    qtbot, window, bridge, store, event, fake_uploader
) -> None:  # type: ignore[no-untyped-def]
    fake_uploader.albums = [uploaders.AlbumChoice("/api/v2/album/A1", "Old Gala", "NA1")]
    event.destination, event.album = "smugmug", "/api/v2/album/A1"
    bridge.save_event(event)
    editor = EventEditor(bridge, bridge.active(), window)
    qtbot.addWidget(editor)
    editor.show()
    picker = editor.album_picker
    qtbot.waitUntil(lambda: editor.gallery.count() == 1, timeout=3000)
    assert picker.new.toolTip() == "New gallery" and not picker.new_row.isVisible()
    qtbot.mouseClick(picker.new, Qt.MouseButton.LeftButton)
    assert picker.new_row.isVisible() and picker.new_name.text() == event.name  # suggested
    picker.new_name.setText("Smith Wedding")
    shoot(editor, "fr41-new-gallery-open-light")
    qtbot.mouseClick(picker.create_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: editor.gallery.count() == 2, timeout=3000)
    assert fake_uploader.created == ["Smith Wedding"] and not picker.new_row.isVisible()
    shoot(editor, "fr41-new-gallery-light")
    assert editor.gallery.itemText(0) == "Smith Wedding"  # newest first
    assert editor.album.text() == "Smith Wedding"
    qtbot.mouseClick(editor.save, Qt.MouseButton.LeftButton)
    saved = store.get(event.id)
    assert (saved.album, saved.album_name) == ("/api/v2/album/new1", "Smith Wedding")


def test_fr41_first_run_lists_galleries_after_sign_in(qtbot, bridge, fake_uploader) -> None:  # type: ignore[no-untyped-def]
    fake_uploader.albums = [uploaders.AlbumChoice("/api/v2/album/B2", "Smith Wedding", "NB2")]
    wizard = FirstRun(bridge)
    qtbot.addWidget(wizard)
    wizard.show()
    wizard.destination.setCurrentIndex(1)  # SmugMug
    wizard.go(2)
    qtbot.waitUntil(lambda: wizard.album_picker.gallery.count() == 1, timeout=3000)
    wizard.album_picker.gallery.setCurrentIndex(0)
    event = wizard.current_event()
    assert (event.album, event.album_name) == ("/api/v2/album/B2", "Smith Wedding")


def test_first_run_three_steps_then_event_exists(qtbot, qapp, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from WeFlow.ui import theme as theme_module
    from WeFlow.ui.bridge import Bridge

    theme_module.install(qapp)
    theme_module.theme.set_scheme("light")
    fresh = Store(tmp_path / "fresh-appdata")
    fresh.load()
    assert not fresh.settings.first_run_done
    bridge = Bridge(fresh, uploader_factory=lambda d: None)  # type: ignore[arg-type,return-value]
    try:
        opened: list[str] = []
        fake_services(monkeypatch, opened)
        wizard = FirstRun(bridge)
        qtbot.addWidget(wizard)
        wizard.show()
        assert wizard.step_label.text() == "Step 1 of 3" and not wizard.back.isVisible()
        assert fresh.settings.library  # the default folder is filled in
        wizard.library.set_library(str(tmp_path / "my-library"))
        shoot(wizard, "first-run-1-light")

        qtbot.mouseClick(wizard.next, Qt.MouseButton.LeftButton)
        assert wizard.step_label.text() == "Step 2 of 3"
        assert wizard.next.text() == "Skip for now"  # connecting is optional
        shoot(wizard, "first-run-2-light")

        qtbot.mouseClick(wizard.next, Qt.MouseButton.LeftButton)
        assert wizard.step_label.text() == "Step 3 of 3" and wizard.next.text() == "Start"
        assert wizard.name.text().endswith("New Event")
        wizard.name.setText("")
        assert not wizard.next.isEnabled() and wizard.name_error.isVisible()
        wizard.name.setText("First Wedding")
        wizard.destination.setCurrentIndex(2)
        assert not wizard.next.isEnabled() and wizard.album_error.isVisible()
        wizard.album.setText("First Album")
        shoot(wizard, "first-run-3-light")
        qtbot.mouseClick(wizard.back, Qt.MouseButton.LeftButton)
        assert wizard.step_label.text() == "Step 2 of 3"
        wizard.go(2)
        qtbot.mouseClick(wizard.next, Qt.MouseButton.LeftButton)

        assert wizard.result() == 1 and wizard.created is not None
        assert fresh.settings.first_run_done and fresh.library == tmp_path / "my-library"
        active = fresh.active()
        assert active is not None and (active.name, active.destination) == (
            "First Wedding",
            "flickr",
        )
    finally:
        bridge.shutdown()


# ---------------------------------------------------------- export and presets
def test_fr26_fr27_export_fields_show_only_when_they_apply(
    qtbot, window, bridge, store, event
) -> None:  # type: ignore[no-untyped-def]
    editor = EventEditor(bridge, bridge.active(), window)
    qtbot.addWidget(editor)
    editor.show()
    assert editor.naming.currentData() == "import"
    assert not editor.custom_field.isVisible() and not editor.start_field.isVisible()
    assert editor.example.text() == "Example: Test-Event_20261111-143000_DSC_0001.jpg"
    editor.naming.setCurrentIndex(editor.naming.findData("custom_seq"))
    assert editor.custom_field.isVisible() and editor.start_field.isVisible()
    assert editor.custom_name.placeholderText() == "Test Event"
    assert editor.example.text() == "Example: Test-Event-0001.jpg"
    editor.custom_name.setText("Smith Wedding")
    editor.start_number.setValue(50)
    assert editor.example.text() == "Example: Smith-Wedding-0050.jpg"

    assert editor.quality.isEnabled() and not editor.size_field.isVisible()
    editor.limit_size.setChecked(True)
    assert not editor.quality.isEnabled() and editor.size_field.isVisible()
    editor.size_kb.setValue(800)
    qtbot.mouseClick(editor.save, Qt.MouseButton.LeftButton)
    saved = store.get(event.id)
    assert (saved.file_naming, saved.custom_name, saved.start_number) == (
        "custom_seq",
        "Smith Wedding",
        50,
    )
    assert (saved.limit_file_size, saved.file_size_kb) == (True, 800)


def test_fr28_save_and_use_a_preset_in_a_new_event(qtbot, window, bridge, store, event) -> None:  # type: ignore[no-untyped-def]
    editor = EventEditor(bridge, bridge.active(), window)
    qtbot.addWidget(editor)
    editor.show()
    assert editor.preset.currentText() == "No preset"
    assert editor.preset_action.text() == "Save as preset…"
    editor.naming.setCurrentIndex(editor.naming.findData("custom_seq"))
    editor.quality.setValue(75)
    qtbot.mouseClick(editor.preset_action, Qt.MouseButton.LeftButton)
    assert editor.preset_namer.isVisible()
    editor.preset_name.setText("Web")
    qtbot.mouseClick(editor.preset_save, Qt.MouseButton.LeftButton)
    assert [p.name for p in store.presets()] == ["Web"]
    assert editor.preset.currentText() == "Web" and editor.preset_action.text() == "Delete preset"
    editor.quality.setValue(76)  # no longer matches
    assert editor.preset.currentText() == "No preset"
    editor.reject()

    fresh = EventEditor(bridge, bridge.draft_event(), window)
    qtbot.addWidget(fresh)
    fresh.show()
    fresh.preset.setCurrentIndex(fresh.preset.findText("Web"))
    fresh.preset.activated.emit(fresh.preset.currentIndex())
    assert fresh.naming.currentData() == "custom_seq" and fresh.quality.value() == 75
    fresh.name.setText("Jones Party")
    qtbot.mouseClick(fresh.save, Qt.MouseButton.LeftButton)
    assert fresh.saved is not None and fresh.saved.jpeg_quality == 75
    assert fresh.saved.file_naming == "custom_seq"


def test_fr28_delete_preset_takes_two_clicks(qtbot, window, bridge, store, event) -> None:  # type: ignore[no-untyped-def]
    store.save_preset("Web", event)  # matches the event as it is
    editor = EventEditor(bridge, bridge.active(), window)
    qtbot.addWidget(editor)
    editor.show()
    assert editor.preset.currentText() == "Web"
    qtbot.mouseClick(editor.preset_action, Qt.MouseButton.LeftButton)
    assert editor.preset_action.text() == "Really delete?" and store.presets()
    qtbot.mouseClick(editor.preset_action, Qt.MouseButton.LeftButton)
    assert store.presets() == [] and editor.preset.count() == 1
    assert editor.preset_action.text() == "Save as preset…"
    shoot(editor, "event-editor-export")
