"""A non-modal bar above the status panel.

Used for the card notice ("Card X → Event", Cancel, auto-start after 15 s; FR-11),
for short results such as an eject failure, and to offer a newer version. The
update offer has the lowest priority: it waits behind the other two.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy

from .common import Button, Icon, label

LEVELS = {
    "info": ("info", "text-2"),
    "safe": ("circle-check", "good-strong"),
    "error": ("triangle-alert", "bad-strong"),
}


class NoticeBar(QFrame):
    started = Signal(str, str)  # card path, label: the countdown ran out
    cancelled = Signal(str)  # card path
    download_clicked = Signal()  # the update offer was accepted
    skipped = Signal()  # the update offer was turned down

    def __init__(self, countdown_ms: int = 15000, message_ms: int = 6000) -> None:
        super().__init__()
        self.setObjectName("NoticeBar")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.countdown_ms = countdown_ms
        self.message_ms = message_ms
        self._card: tuple[str, str] | None = None
        self._remaining = 0
        self._update = ""  # version on offer
        self._update_label = "Download"
        self._progress = ""  # an update being downloaded or installed (FR-44)
        self.icon = Icon("card-sim", "text", 16)
        self.text = label("", "body")
        self.detail = label("", "secondary")
        self.action = Button("Download", "outline")
        self.action.clicked.connect(self._download)
        self.action.hide()
        self.cancel = Button("Cancel", "text")
        self.cancel.clicked.connect(self._cancel)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 8, 8, 8)
        row.setSpacing(8)
        row.addWidget(self.icon)
        row.addWidget(self.text, 1)
        row.addWidget(self.detail)
        row.addWidget(self.action)
        row.addWidget(self.cancel)
        self._tick = QTimer(self)
        self._tick.setInterval(250)
        self._tick.timeout.connect(self._on_tick)
        self._hide = QTimer(self)
        self._hide.setSingleShot(True)
        self._hide.timeout.connect(self._rest)
        self.hide()

    def card(self) -> tuple[str, str] | None:
        return self._card

    def show_card(self, path: str, card_label: str, event_name: str) -> None:
        """Announce a card and start the countdown to importing it."""
        self._hide.stop()
        self._card = (path, card_label)
        self._remaining = self.countdown_ms
        self.icon.set_icon("card-sim", "text")
        self.text.setText(f"Card {card_label} → {event_name}")
        self.action.hide()
        self.cancel.setText("Cancel")
        self.cancel.setToolTip("")
        self.cancel.show()
        self._show_remaining()
        self.show()
        self._tick.start()

    def show_message(self, level: str, message: str) -> None:
        """Show a short result for a few seconds. Never interrupts a card countdown."""
        if self._card is not None:
            return
        icon, token = LEVELS.get(level, LEVELS["info"])
        self.icon.set_icon(icon, token)
        self.text.setText(message)
        self.detail.setText("")
        self.action.hide()
        self.cancel.setText("Dismiss")
        self.cancel.setToolTip("")
        self.cancel.show()
        self.show()
        self._hide.start(self.message_ms)

    def offer_update(self, version: str, action: str = "Download") -> None:
        """Offer a newer version until it is answered. Waits behind cards and messages."""
        if version != self._update or action != self._update_label or not self.offering_update():
            self._update = version
            self._update_label = action
            self._rest()

    def show_progress(self, text: str) -> None:
        """An update in progress, e.g. "Downloading WeFlow 1.9.0 · 40%". Empty clears it."""
        if text != self._progress:
            self._progress = text
            self._rest()

    def withdraw_update(self) -> None:
        self._update = ""
        self._rest()

    def offering_update(self) -> bool:
        return self.action.isVisibleTo(self)

    def _rest(self) -> None:
        """Nothing urgent is showing: fall back to the update offer, or hide."""
        if self._card is not None or self._hide.isActive():
            return
        if self._progress:
            self.icon.set_icon("refresh-cw", "text-2")
            self.text.setText(self._progress)
            self.detail.setText("")
            self.action.hide()
            self.cancel.hide()
            self.show()
            return
        if not self._update:
            self.action.hide()
            self.hide()
            return
        self.icon.set_icon(*LEVELS["info"])
        self.text.setText(f"WeFlow {self._update} is available")
        self.detail.setText("")
        self.action.setText(self._update_label)
        self.action.show()
        self.cancel.setText("Skip")
        self.cancel.setToolTip("Don't offer this version again")
        self.cancel.show()
        self.show()

    def _download(self) -> None:
        self._update = ""
        self._rest()
        self.download_clicked.emit()

    def dismiss_card(self, path: str) -> None:
        """The card went away before the import started."""
        if self._card and self._card[0] == path:
            self._stop()

    def start_now(self) -> None:
        if self._card:
            card = self._card
            self._stop()
            self.started.emit(*card)

    def _show_remaining(self) -> None:
        seconds = max(1, -(-self._remaining // 1000))
        self.detail.setText(f"Starts in {seconds} s")

    def _on_tick(self) -> None:
        self._remaining -= self._tick.interval()
        if self._remaining <= 0:
            self.start_now()
        else:
            self._show_remaining()

    def _stop(self) -> None:
        self._tick.stop()
        self._card = None
        self._rest()

    def _cancel(self) -> None:
        if self._card:
            path = self._card[0]
            self._stop()
            self.cancelled.emit(path)
        elif self._hide.isActive():
            self._hide.stop()
            self._rest()
        elif self._update:
            self._update = ""
            self._rest()
            self.skipped.emit()
        else:
            self.hide()
