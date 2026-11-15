"""Settings: library, SmugMug / Flickr, sound, auto-eject, start at login (§7.4)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from WeFlow import __version__

from .bridge import SERVICES, Bridge, PendingAuth, service_name
from .widgets import busy as busy_cover
from .widgets.chip import Chip
from .widgets.common import Button, Toggle, label, separator


class ServiceRow(QFrame):
    """One service: status, Connect / Reconnect, Disconnect, and the code step."""

    def __init__(self, bridge: Bridge, service: str) -> None:
        super().__init__()
        self.bridge = bridge
        self.service = service
        self.title = service_name(service)
        self._pending: PendingAuth | None = None

        self.chip = Chip("Not connected", "neutral", "info")
        self.connect_button = Button("Connect")
        self.connect_button.clicked.connect(self._begin)
        self.disconnect = Button("Disconnect", "link")
        self.disconnect.clicked.connect(self._disconnect)
        self.own_key = Button("Change API key…", "link")
        self.own_key.clicked.connect(self._show_keys)
        self.get_key = Button(f"Get a {self.title} API key…", "link")
        self.get_key.clicked.connect(lambda: bridge.open_api_key_page(service))

        self.api_key = QLineEdit()
        self.api_key.setPlaceholderText("API key")
        self.api_key.setAccessibleName(f"{self.title} API key")
        self.api_secret = QLineEdit()
        self.api_secret.setPlaceholderText("API secret")
        self.api_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_secret.setAccessibleName(f"{self.title} API secret")
        self.keys_box = QWidget()
        keys = QHBoxLayout(self.keys_box)
        keys.setContentsMargins(0, 0, 0, 0)
        keys.setSpacing(8)
        keys.addWidget(self.api_key)
        keys.addWidget(self.api_secret)
        self.keys_box.hide()

        self.code = QLineEdit()
        self.code.setPlaceholderText("Code from your browser")
        self.code.setAccessibleName(f"{self.title} code")
        self.finish = Button("Finish", "primary")
        self.finish.clicked.connect(self._finish)
        self.code.returnPressed.connect(self._finish)
        self.code_box = QWidget()
        code = QHBoxLayout(self.code_box)
        code.setContentsMargins(0, 0, 0, 0)
        code.setSpacing(8)
        code.addWidget(self.code, 1)
        code.addWidget(self.finish)
        self.code_box.hide()

        self.help = label("", "secondary", wrap=True)
        self.help.hide()
        self.error = label("", "error", wrap=True)
        self.error.hide()

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(label(self.title, "body"))
        head.addWidget(self.chip)
        head.addStretch(1)
        head.addWidget(self.disconnect)
        head.addWidget(self.connect_button)
        links = QHBoxLayout()
        links.setContentsMargins(0, 0, 0, 0)
        links.setSpacing(12)
        links.addWidget(self.own_key)
        links.addWidget(self.get_key)
        links.addStretch(1)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)
        column.addLayout(head)
        column.addWidget(self.keys_box)
        column.addWidget(self.help)
        column.addWidget(self.code_box)
        column.addWidget(self.error)
        column.addLayout(links)

        bridge.services_changed.connect(self.refresh)
        self.refresh()

    def connected(self) -> bool:
        return bool(self.bridge.connected.get(self.service))

    def refresh(self) -> None:
        on = self.connected()
        if on:
            self.chip.set("Connected", "good", "check")
        else:
            self.chip.set("Not connected", "neutral", "info")
        self.connect_button.setText("Reconnect" if on else "Connect")
        self.disconnect.setVisible(on)
        if not self.bridge.has_keys.get(self.service):
            self.keys_box.show()  # WeFlow ships with no key: each user enters their own
        self.own_key.setVisible(self.keys_box.isHidden())
        self.get_key.setVisible(not self.keys_box.isHidden())

    def _show_keys(self) -> None:
        self.keys_box.show()
        self.own_key.hide()
        self.get_key.show()
        self.api_key.setFocus()

    def _fail(self, message: str) -> None:
        self.error.setText(message)
        self.error.show()

    def _busy(self, busy: bool) -> None:
        self.connect_button.setEnabled(not busy)
        self.finish.setEnabled(not busy)
        (busy_cover.begin if busy else busy_cover.end)(self)

    def _disconnect(self) -> None:
        busy_cover.begin(self)  # the keychain may take a moment, or ask first
        self.bridge.disconnect_service(self.service, lambda _result: busy_cover.end(self))

    def _begin(self) -> None:
        self.error.hide()
        self.help.hide()
        key, secret = self.api_key.text().strip(), self.api_secret.text().strip()
        if self.keys_box.isVisible() and bool(key) != bool(secret):
            self._fail("Enter both the API key and the API secret.")
            return
        self._busy(True)
        self.bridge.connect_begin(self.service, key, secret, self._begun)

    def _begun(self, result: Any) -> None:
        self._busy(False)
        if not isinstance(result, PendingAuth):
            message = getattr(result, "message", "") or f"Can't reach {self.title}. Try again."
            if "API key" in message:
                self._show_keys()
            self._fail(message)
            return
        self._pending = result
        self.bridge.open_url(result.url)
        self.help.setText(
            f"Approve WeFlow in your browser, then paste the code {self.title} shows."
        )
        self.help.show()
        self.code.clear()
        self.code_box.show()
        self.code.setFocus()

    def _finish(self) -> None:
        if self._pending is None or not self.code.text().strip():
            return
        self.error.hide()
        self._busy(True)
        self.bridge.connect_finish(
            self.service, self._pending, self.code.text().strip(), self._finished
        )

    def _finished(self, result: Any) -> None:
        self._busy(False)
        if result is True:
            self._pending = None
            self.code_box.hide()
            self.help.hide()
            self.keys_box.hide()
            self.api_key.clear()
            self.api_secret.clear()
        else:
            self._fail(getattr(result, "message", "") or "That did not work. Press Connect again.")
        self.refresh()


class LibraryRow(QWidget):
    """Shows the library folder and lets the user move it."""

    def __init__(self, bridge: Bridge) -> None:
        super().__init__()
        self.bridge = bridge
        self.path = label("", "body")
        self.path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.change = Button("Change…")
        self.change.clicked.connect(self._choose)
        self.note = label("", "secondary", wrap=True)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.path, 1)
        row.addWidget(self.change)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        column.addLayout(row)
        column.addWidget(self.note)
        self.refresh()

    def refresh(self) -> None:
        text = self.bridge.library()
        self.path.setText(text if len(text) <= 46 else "…" + text[-45:])
        self.path.setToolTip(text)
        busy = self.bridge.has_pending()
        self.change.setEnabled(not busy)
        self.note.setText(
            "WeFlow is still working. Change the folder when it has finished."
            if busy
            else "New photos are saved here, in one folder per event."
        )

    def _choose(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Library folder", self.bridge.library())
        if folder:
            self.set_library(folder)

    def set_library(self, folder: str) -> None:
        self.bridge.update_settings(library=folder)
        self.refresh()


class SettingsDialog(QDialog):
    def __init__(self, bridge: Bridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.setWindowTitle("Settings")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setFixedWidth(520)

        self.library = LibraryRow(bridge)
        self.services = {service: ServiceRow(bridge, service) for service in SERVICES}
        self.sound = Toggle(bridge.settings.card_sound)
        self.sound.setAccessibleName("Card-detected sound")
        self.sound.toggled.connect(lambda on: bridge.update_settings(card_sound=on))
        self.eject = Toggle(bridge.settings.auto_eject)
        self.eject.setAccessibleName("Eject the card when it's copied")
        self.eject.setToolTip("Once every photo is copied and checked, WeFlow ejects the card.")
        self.eject.toggled.connect(lambda on: bridge.update_settings(auto_eject=on))
        self.login = Toggle(bridge.settings.start_at_login)
        self.login.setAccessibleName("Start at login")
        self.login.toggled.connect(bridge.set_start_at_login)
        self.updates = Toggle(bridge.settings.check_updates)
        self.updates.setAccessibleName("Check for new versions")
        self.updates.toggled.connect(bridge.set_check_updates)
        self.check_now = Button("Check for updates", "text")
        self.check_now.clicked.connect(self._check_now)
        self.update_link = Button("", "link")
        self.update_link.clicked.connect(bridge.get_update)
        self.copy_log = Button("Copy log", icon="copy")
        self.copy_log.setToolTip(
            "Copy recent activity, to paste into a message when asking for help."
        )
        self.copy_log.clicked.connect(self._copy_log)
        self.done_button = Button("Done", "primary")
        self.done_button.clicked.connect(self.accept)

        column = QVBoxLayout(self)
        column.setContentsMargins(24, 24, 24, 16)
        column.setSpacing(12)
        column.addWidget(label("Settings", "title"))
        column.addSpacing(4)
        column.addWidget(label("Library folder", "heading"))
        column.addWidget(self.library)
        column.addWidget(separator())
        column.addWidget(label("Services", "heading"))
        for index, row in enumerate(self.services.values()):
            if index:
                column.addSpacing(4)
            column.addWidget(row)
        column.addWidget(separator())
        for caption, toggle in (
            ("Play a sound when a card is detected", self.sound),
            ("Eject the card when it's copied", self.eject),
            ("Start WeFlow at login", self.login),
            ("Check for new versions", self.updates),
        ):
            line = QHBoxLayout()
            line.addWidget(label(caption, "body"), 1)
            line.addWidget(toggle)
            column.addLayout(line)
        line = QHBoxLayout()
        line.addWidget(
            label(
                "Something not working? Copy the log and paste it in your message.",
                "secondary",
                wrap=True,
            ),
            1,
        )
        line.addWidget(self.copy_log)
        column.addLayout(line)
        column.addStretch(1)
        footer = QHBoxLayout()
        footer.setSpacing(8)
        footer.addWidget(label(f"WeFlow {__version__}", "label"))
        footer.addWidget(self.check_now)
        footer.addWidget(self.update_link)
        footer.addStretch(1)
        footer.addWidget(self.done_button)
        column.addLayout(footer)
        bridge.update_changed.connect(self._show_update)
        self._show_update()
        bridge.refresh_services()

    def _copy_log(self) -> None:
        self.copy_log.setEnabled(False)
        self.bridge.read_log(self._on_log)

    def _on_log(self, text: object) -> None:
        self.copy_log.setEnabled(True)
        if not isinstance(text, str):
            self.copy_log.setText("Can't read the log")
        else:
            clipboard = QGuiApplication.clipboard()
            clipboard.setText(text or "The log is empty.")
            self.copy_log.setText("Copied")
        QTimer.singleShot(2000, self.copy_log, lambda: self.copy_log.setText("Copy log"))

    def _check_now(self) -> None:
        self.check_now.setEnabled(False)
        self.check_now.setText("Checking…")
        busy_cover.begin(self)
        self.bridge.check_for_updates_now(self._on_checked)

    def _on_checked(self, outcome: str) -> None:
        busy_cover.end(self)
        self.check_now.setEnabled(True)
        if outcome == "update":
            self.check_now.setText("Check for updates")
            self._show_update()
            return
        self.check_now.setText(
            "Up to date" if outcome == "current" else "Can't check right now. Try again later."
        )
        QTimer.singleShot(4000, self.check_now, lambda: self.check_now.setText("Check for updates"))

    def _show_update(self) -> None:
        update = self.bridge.update
        self.check_now.setVisible(update is None)
        verb = "Update to" if self.bridge.can_self_update else "Download"
        self.update_link.setText(f"{verb} {update.version}" if update else "")
        self.update_link.setVisible(update is not None)
