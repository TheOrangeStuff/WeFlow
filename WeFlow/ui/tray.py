"""Tray / menu-bar icon with four states, and throttled notifications (§7.4)."""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .theme import theme

STATES = ("idle", "working", "safe", "attention")
DOTS = {"working": "warn", "safe": "good", "attention": "bad"}
TIPS = {
    "idle": "WeFlow — waiting for a card",
    "working": "WeFlow — working",
    "safe": "WeFlow — safe to remove the card",
    "attention": "WeFlow — needs attention",
}
THROTTLE_SECONDS = 5.0


def tray_icon(state: str, size: int = 22) -> QIcon:
    """The card glyph, with a coloured dot for every state but idle."""
    ratio = 2.0
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(ratio, ratio)
    painter.drawPixmap(
        QRectF(2, 2, size - 4, size - 4), theme.pixmap("card-sim", "text", 20), QRectF()
    )
    if state in DOTS:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.color("surface"))
        painter.drawEllipse(QRectF(size - 11, size - 11, 11, 11))
        painter.setBrush(theme.color(DOTS[state]))
        painter.drawEllipse(QRectF(size - 9.5, size - 9.5, 8, 8))
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return QIcon(pixmap)


class Throttle:
    """At most one notification per 5 s per type."""

    def __init__(self, seconds: float = THROTTLE_SECONDS) -> None:
        self.seconds = seconds
        self._last: dict[str, float] = {}

    def allow(self, kind: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        last = self._last.get(kind)
        if last is not None and now - last < self.seconds:
            return False
        self._last[kind] = now
        return True


class Tray(QObject):
    show_requested = Signal()
    pause_toggled = Signal(bool)
    quit_requested = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.state = "idle"
        self.throttle = Throttle()
        self.sent: list[tuple[str, str, str]] = []  # kind, title, message
        self.icon = QSystemTrayIcon(tray_icon("idle"), self)
        self.menu = QMenu()
        self.show_action = QAction("Show WeFlow", self.menu)
        self.pause_action = QAction("Pause watching", self.menu)
        self.pause_action.setCheckable(True)
        self.quit_action = QAction("Quit", self.menu)
        self.show_action.triggered.connect(self.show_requested)
        self.pause_action.toggled.connect(self.pause_toggled)
        self.quit_action.triggered.connect(self.quit_requested)
        self.menu.addAction(self.show_action)
        self.menu.addAction(self.pause_action)
        self.menu.addSeparator()
        self.menu.addAction(self.quit_action)
        self.icon.setContextMenu(self.menu)
        self.icon.setToolTip(TIPS["idle"])
        self.icon.activated.connect(self._activated)
        theme.changed.connect(self._retheme)

    def available(self) -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def show(self) -> None:
        self.icon.show()

    def hide(self) -> None:
        self.icon.hide()

    def set_state(self, state: str) -> None:
        self.state = state if state in STATES else "idle"
        self._retheme()
        self.icon.setToolTip(TIPS[self.state])

    def set_paused(self, paused: bool) -> None:
        self.pause_action.blockSignals(True)
        self.pause_action.setChecked(paused)
        self.pause_action.blockSignals(False)

    def notify(self, kind: str, title: str, message: str) -> bool:
        """Show a notification unless one of this kind was shown in the last 5 s."""
        if not self.throttle.allow(kind):
            return False
        self.sent.append((kind, title, message))
        icon = (
            QSystemTrayIcon.MessageIcon.Warning
            if kind == "attention"
            else QSystemTrayIcon.MessageIcon.Information
        )
        if self.icon.isVisible() and QSystemTrayIcon.supportsMessages():
            self.icon.showMessage(title, message, icon, 6000)
        return True

    def _retheme(self) -> None:
        self.icon.setIcon(tray_icon(self.state))

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_requested.emit()
