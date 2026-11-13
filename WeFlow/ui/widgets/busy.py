"""The "Flowing…" cover: greys a window while a slow button's job runs.

``begin(widget)`` and ``end(widget)`` are counted per window, so overlapping jobs keep
the cover up until the last one finishes. It appears only after ``SHOW_AFTER_MS``,
so a quick answer never flickers.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QKeyEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ..theme import theme
from .common import label, motion_enabled

SHOW_AFTER_MS = 300
TEXT = "Flowing…"
SCRIM_ALPHA = 0.82  # how much of the window's background covers the page
BAR_WIDTH, BAR_HEIGHT, SEGMENT = 120, 4, 36
TICK_MS = 16


class FlowBar(QWidget):
    """A thin track with a segment flowing along it: busy, with no known end."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(BAR_WIDTH, BAR_HEIGHT)
        self._offset = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._step)
        theme.changed.connect(self.update)

    def start(self) -> None:
        if motion_enabled():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _step(self) -> None:
        self._offset = (self._offset + 2.0) % (BAR_WIDTH + SEGMENT)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        radius = BAR_HEIGHT / 2
        painter.setBrush(theme.color("border"))
        painter.drawRoundedRect(QRectF(0, 0, BAR_WIDTH, BAR_HEIGHT), radius, radius)
        left = self._offset - SEGMENT if motion_enabled() else (BAR_WIDTH - SEGMENT) / 2
        segment = QRectF(left, 0, SEGMENT, BAR_HEIGHT).intersected(
            QRectF(0, 0, BAR_WIDTH, BAR_HEIGHT)
        )
        if segment.width() > 0:
            painter.setBrush(theme.color("text-2"))
            painter.drawRoundedRect(segment, radius, radius)


class BusyOverlay(QWidget):
    """Covers its window, swallows clicks and keys, and says the app is working."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self.setObjectName("BusyOverlay")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(TEXT)
        self.count = 0
        self.text = label(TEXT, "heading")
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bar = FlowBar()
        column = QVBoxLayout(self)
        column.setSpacing(12)
        column.addStretch(1)
        column.addWidget(self.text, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(self.bar, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addStretch(1)
        self._delay = QTimer(self)
        self._delay.setSingleShot(True)
        self._delay.setInterval(SHOW_AFTER_MS)
        self._delay.timeout.connect(self._appear)
        self._focus: QWidget | None = None
        window.installEventFilter(self)
        theme.changed.connect(self.update)
        self.hide()

    def begin(self) -> None:
        self.count += 1
        if self.count == 1:
            self._delay.start()

    def end(self) -> None:
        self.count = max(0, self.count - 1)
        if self.count:
            return
        self._delay.stop()
        if self.isVisible():
            self.bar.stop()
            self.hide()
            if self._focus is not None:
                self._focus.setFocus()
            self._focus = None

    def _appear(self) -> None:
        parent = self.parentWidget()
        if parent is None or not self.count:
            return
        app_focus = parent.focusWidget()
        self._focus = app_focus if app_focus is not self else None
        self.setGeometry(parent.rect())
        self.raise_()
        self.show()
        self.setFocus()
        self.bar.start()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self.setGeometry(self.parentWidget().rect())  # type: ignore[union-attr]
        return False

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.KeyPress and self.isVisible():
            return True  # Tab, Esc and the rest wait until the job is done
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        event.accept()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        scrim = theme.color("bg")
        scrim.setAlphaF(SCRIM_ALPHA)
        painter.fillRect(self.rect(), scrim)


def overlay(widget: QWidget) -> BusyOverlay:
    """The cover for ``widget``'s window, made the first time it is needed."""
    window = widget.window()
    found = window.findChild(BusyOverlay, options=Qt.FindChildOption.FindDirectChildrenOnly)
    return found if isinstance(found, BusyOverlay) else BusyOverlay(window)


def begin(widget: QWidget) -> None:
    overlay(widget).begin()


def end(widget: QWidget) -> None:
    overlay(widget).end()
