"""Application start-up: theme, store, first run, main window, tray."""

from __future__ import annotations

import sys

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from . import theme as theme_module
from .bridge import Bridge, Notes, Profile, Store, app_data_dir, setup_logging
from .event_editor import EventEditor
from .first_run import FirstRun
from .main_window import MainWindow
from .settings_dialog import SettingsDialog
from .tray import Tray
from .whats_new import WhatsNew
from .widgets import busy


class App:
    """Owns the long-lived objects and wires the windows together."""

    def __init__(self, qt: QApplication, bridge: Bridge) -> None:
        self.qt = qt
        self.bridge = bridge
        self.window = MainWindow(bridge)
        self.tray = Tray()
        self._dialogs: list[object] = []

        self.window.can_hide_to_tray = self.tray.available()
        self.window.notify.connect(self.tray.notify)
        self.window.status_changed.connect(self.tray.set_state)
        self.window.settings_requested.connect(self.open_settings)
        self.window.edit_event_requested.connect(self.open_event_editor)
        self.window.quit_requested.connect(self.quit)
        self.tray.show_requested.connect(self.show_window)
        self.tray.pause_toggled.connect(self.window.set_paused)
        self.tray.quit_requested.connect(self.quit)
        bridge.settings_changed.connect(lambda: self.tray.set_paused(bridge.settings.paused))
        self.tray.set_paused(bridge.settings.paused)
        self.tray.set_state(self.window.status())

    def start(self) -> None:
        self.tray.show()
        self.window.show()
        self.bridge.start()

    def show_window(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def open_settings(self) -> SettingsDialog:
        dialog = SettingsDialog(self.bridge, self.window)
        dialog.open()
        return dialog

    def open_event_editor(self, event: Profile) -> EventEditor:
        dialog = EventEditor(self.bridge, event, self.window)
        dialog.open()
        return dialog

    def show_whats_new(self) -> None:
        """Once per version, after an update: say what changed."""
        self.bridge.whats_new(self.open_whats_new)

    def open_whats_new(self, version: str, notes: Notes) -> WhatsNew:
        dialog = WhatsNew(version, notes, self.window)
        dialog.open()
        return dialog

    def quit(self) -> None:
        self.tray.hide()
        self.bridge.shutdown()
        self.qt.quit()


def run(argv: list[str] | None = None) -> int:
    args = sys.argv if argv is None else argv
    smoke_test = "--smoke-test" in args  # packaging check: start, show, exit 0
    qt = QApplication([a for a in args if a != "--smoke-test"])
    qt.setApplicationName("WeFlow")
    qt.setApplicationDisplayName("WeFlow")
    qt.setQuitOnLastWindowClosed(False)
    theme_module.install(qt)
    theme_module.preload_icons()
    busy.preload()  # decodes the loading loop on a worker thread
    qt.setWindowIcon(QIcon(str(theme_module.ICON_DIR / "app.png")))

    data_dir = app_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(data_dir / "weflow.lock"))
    if not lock.tryLock(100):
        return 0  # WeFlow is already running
    setup_logging(data_dir)

    store = Store(data_dir)
    store.load()
    bridge = Bridge(store)
    if not store.settings.first_run_done and not smoke_test:
        wizard = FirstRun(bridge)
        if not wizard.exec():
            bridge.shutdown()
            return 0
    app = App(qt, bridge)
    app.start()
    if smoke_test:
        QTimer.singleShot(2000, app.quit)
    else:
        bridge.watch_updates()
        app.show_whats_new()
    code = qt.exec()
    bridge.shutdown()
    lock.unlock()
    return code
