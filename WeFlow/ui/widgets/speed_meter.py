"""Speed: a three-stop slider (LOW, DEFAULT, MAX) in the main window's top bar.

1.14.0: moved out of Settings. The stored values stay "low", "medium" and "high", so
older settings files keep working; only the words on screen changed.
"""

from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QFont, QKeyEvent, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QHBoxLayout, QWidget

from ..theme import TYPE, theme
from .common import label, motion_enabled

LEVELS = ("low", "medium", "high")
NAMES = {"low": "LOW", "medium": "DEFAULT", "high": "MAX"}
CAPTIONS = {
    "low": "Low: keeps most of your computer free for other work.",
    "medium": "Default: a balance of speed and a responsive computer.",
    "high": "Max: uses nearly all of your computer to finish sooner.",
}
TRACK_H = 4
KNOB = 16
PAD = 24  # room for the knob, its focus ring and the end labels
LABELS_H = 16


class SpeedSlider(QWidget):
    """The track itself: three detents, an accent fill and a sliding knob."""

    changed = Signal(str)

    def __init__(self, level: str = "medium") -> None:
        super().__init__()
        self._index = LEVELS.index(level) if level in LEVELS else 1
        self._pos = float(self._index)
        self._slide = QPropertyAnimation(self, b"knob", self)
        self._slide.setDuration(160)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setFixedHeight(KNOB + 8 + LABELS_H)
        self.setMinimumWidth(190)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Speed")
        theme.changed.connect(self.update)

    # ---------------------------------------------------------------- value
    @property
    def level(self) -> str:
        return LEVELS[self._index]

    def set_level(self, level: str) -> None:
        if level in LEVELS:
            self._go(LEVELS.index(level))

    def _get_knob(self) -> float:
        return self._pos

    def _set_knob(self, value: float) -> None:
        self._pos = value
        self.update()

    # Not "pos": QWidget already has a "pos" property, and animating that one moved the
    # whole slider around on macOS (the 1.12.0 glitch).
    knob = Property(float, _get_knob, _set_knob)

    def _go(self, index: int, emit: bool = True) -> None:
        index = max(0, min(len(LEVELS) - 1, index))
        if motion_enabled() and self.isVisible():
            self._slide.stop()
            self._slide.setStartValue(self._pos)
            self._slide.setEndValue(float(index))
            self._slide.start()
        else:
            self._set_knob(float(index))
        if index != self._index:
            self._index = index
            self.setAccessibleDescription(NAMES[self.level])
            if emit:
                self.changed.emit(self.level)

    # ------------------------------------------------------------- geometry
    def _x(self, pos: float) -> float:
        span = self.width() - 2 * PAD
        return PAD + span * pos / (len(LEVELS) - 1)

    def _nearest(self, x: float) -> int:
        span = max(1, self.width() - 2 * PAD)
        return round((x - PAD) / span * (len(LEVELS) - 1))

    def sizeHint(self) -> QSize:
        return QSize(220, KNOB + 8 + LABELS_H)

    # ---------------------------------------------------------------- input
    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._go(self._nearest(event.position().x()))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._go(self._nearest(event.position().x()))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Down):
            self._go(self._index - 1)
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Up):
            self._go(self._index + 1)
        elif key == Qt.Key.Key_Home:
            self._go(0)
        elif key == Qt.Key.Key_End:
            self._go(len(LEVELS) - 1)
        else:
            super().keyPressEvent(event)

    # ---------------------------------------------------------------- paint
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        mid = (KNOB + 8) / 2
        left, right, knob_x = self._x(0), self._x(len(LEVELS) - 1), self._x(self._pos)
        enabled = self.isEnabled()
        accent = theme.color("accent" if enabled else "text-2")

        painter.setBrush(theme.color("border"))
        painter.drawRoundedRect(
            QRectF(left, mid - TRACK_H / 2, right - left, TRACK_H), TRACK_H / 2, TRACK_H / 2
        )
        painter.setBrush(accent)
        painter.drawRoundedRect(
            QRectF(left, mid - TRACK_H / 2, knob_x - left, TRACK_H), TRACK_H / 2, TRACK_H / 2
        )
        for i in range(len(LEVELS)):  # detents: filled up to the knob, hollow beyond
            x = self._x(i)
            painter.setBrush(theme.color("on-accent") if i <= self._pos else theme.color("text-2"))
            painter.drawEllipse(QPointF(x, mid), 2, 2)

        painter.setBrush(theme.color("surface"))
        pen = painter.pen()
        pen.setStyle(Qt.PenStyle.SolidLine)
        pen.setColor(accent)
        pen.setWidthF(2)
        painter.setPen(pen)
        painter.drawEllipse(QPointF(knob_x, mid), KNOB / 2 - 1, KNOB / 2 - 1)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        painter.drawEllipse(QPointF(knob_x, mid), 3.5, 3.5)
        if self.hasFocus():
            ring = painter.pen()
            ring.setStyle(Qt.PenStyle.SolidLine)
            ring.setColor(theme.color("accent"))
            ring.setWidthF(1.5)
            painter.setPen(ring)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(knob_x, mid), KNOB / 2 + 2, KNOB / 2 + 2)

        # LOW · DEFAULT · MAX, each centred under its stop; the chosen one stands out.
        for i, level in enumerate(LEVELS):
            chosen = i == self._index
            font = QFont(self.font())
            font.setPixelSize(TYPE["label"][0])
            font.setWeight(QFont.Weight.Bold if chosen else QFont.Weight(TYPE["label"][1]))
            painter.setFont(font)
            painter.setPen(theme.color("text" if chosen else "text-2"))
            x = self._x(i)
            painter.drawText(
                QRectF(x - 40, KNOB + 8, 80, LABELS_H), Qt.AlignmentFlag.AlignCenter, NAMES[level]
            )


class SpeedControl(QWidget):
    """The top-bar control: "SPEED" and the slider with LOW · DEFAULT · MAX under it.
    Hovering says what the level does."""

    changed = Signal(str)

    def __init__(self, level: str = "medium") -> None:
        super().__init__()
        level = level if level in LEVELS else "medium"
        self.slider = SpeedSlider(level)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        title = label("SPEED", "label")
        title.setContentsMargins(0, 0, 0, LABELS_H)  # level with the track, not the words
        row.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.slider)
        self.slider.changed.connect(self._show)
        self._mark(level)

    @property
    def level(self) -> str:
        return self.slider.level

    def set_level(self, level: str) -> None:
        if level in LEVELS and level != self.level:
            self.slider.blockSignals(True)
            self.slider.set_level(level)
            self.slider.blockSignals(False)
            self._mark(level)

    def _show(self, level: str) -> None:
        self._mark(level)
        self.changed.emit(level)

    def _mark(self, level: str) -> None:
        tip = CAPTIONS[level]
        self.setToolTip(tip)
        self.slider.setAccessibleDescription(f"{NAMES[level]}. {tip}")
