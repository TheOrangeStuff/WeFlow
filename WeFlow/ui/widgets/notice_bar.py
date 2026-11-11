"""A non-modal bar above the status panel.

Used for the card notice ("Card X → Event", Cancel, auto-start after 5 s; FR-11)
and for short results such as an eject failure.
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

    def __init__(self, countdown_ms: int = 5000, message_ms: int = 6000) -> None:
        super().__init__()
        self.setObjectName("NoticeBar")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.countdown_ms = countdown_ms
        self.message_ms = message_ms
        self._card: tuple[str, str] | None = None
        self._remaining = 0
        self.icon = Icon("card-sim", "text", 16)
        self.text = label("", "body")
        self.detail = label("", "secondary")
        self.cancel = Button("Cancel", "text")
        self.cancel.clicked.connect(self._cancel)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 8, 8, 8)
        row.setSpacing(8)
        row.addWidget(self.icon)
        row.addWidget(self.text, 1)
        row.addWidget(self.detail)
        row.addWidget(self.cancel)
        self._tick = QTimer(self)
        self._tick.setInterval(250)
        self._tick.timeout.connect(self._on_tick)
        self._hide = QTimer(self)
        self._hide.setSingleShot(True)
        self._hide.timeout.connect(self.hide)
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
        self.cancel.setText("Cancel")
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
        self.cancel.setText("Dismiss")
        self.cancel.show()
        self.show()
        self._hide.start(self.message_ms)

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
        self.hide()

    def _cancel(self) -> None:
        if self._card:
            path = self._card[0]
            self._stop()
            self.cancelled.emit(path)
        else:
            self._hide.stop()
            self.hide()
