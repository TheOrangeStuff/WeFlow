"""Speed: a three-stop slider (Low, Medium, High) with a rising-bars meter."""

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
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ..theme import TYPE, theme
from .common import label, motion_enabled

LEVELS = ("low", "medium", "high")
NAMES = {"low": "Low", "medium": "Medium", "high": "High"}
CAPTIONS = {
    "low": "Keeps most of your computer free for other work.",
    "medium": "A balance of speed and a responsive computer.",
    "high": "Uses nearly all of your computer to finish sooner.",
}
TRACK_H = 6
KNOB = 18
PAD = KNOB // 2 + 2  # room for the knob and its focus ring at either end


class SpeedSlider(QWidget):
    """The track itself: three detents, an accent fill and a sliding knob."""

    changed = Signal(str)

    def __init__(self, level: str = "medium") -> None:
        super().__init__()
        self._index = LEVELS.index(level) if level in LEVELS else 1
        self._pos = float(self._index)
        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setDuration(160)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setFixedHeight(KNOB + 14)
        self.setMinimumWidth(180)
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

    def _get_pos(self) -> float:
        return self._pos

    def _set_pos(self, value: float) -> None:
        self._pos = value
        self.update()

    pos = Property(float, _get_pos, _set_pos)

    def _go(self, index: int, emit: bool = True) -> None:
        index = max(0, min(len(LEVELS) - 1, index))
        if motion_enabled() and self.isVisible():
            self._slide.stop()
            self._slide.setStartValue(self._pos)
            self._slide.setEndValue(float(index))
            self._slide.start()
        else:
            self._set_pos(float(index))
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
        return QSize(240, KNOB + 14)

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
        mid = self.height() / 2
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


class SpeedBars(QWidget):
    """Three rising bars, filled up to the chosen level, like a signal meter."""

    def __init__(self, level: str = "medium") -> None:
        super().__init__()
        self._fill = float(LEVELS.index(level) + 1 if level in LEVELS else 2)
        self._grow = QPropertyAnimation(self, b"fill", self)
        self._grow.setDuration(200)
        self._grow.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setFixedSize(22, 18)
        theme.changed.connect(self.update)

    def _get_fill(self) -> float:
        return self._fill

    def _set_fill(self, value: float) -> None:
        self._fill = value
        self.update()

    fill = Property(float, _get_fill, _set_fill)

    def show_level(self, level: str) -> None:
        target = float(LEVELS.index(level) + 1)
        if motion_enabled() and self.isVisible():
            self._grow.stop()
            self._grow.setStartValue(self._fill)
            self._grow.setEndValue(target)
            self._grow.start()
        else:
            self._set_fill(target)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        width, gap, bottom = 6, 2, float(self.height())
        for i in range(3):
            height = bottom * (i + 1) / 3
            rect = QRectF(i * (width + gap), bottom - height, width, height)
            painter.setBrush(theme.color("border"))
            painter.drawRoundedRect(rect, 1.5, 1.5)
            share = max(0.0, min(1.0, self._fill - i))  # how much of this bar is lit
            if share > 0:
                lit = QRectF(
                    rect.left(), rect.bottom() - rect.height() * share, width, rect.height() * share
                )
                painter.setBrush(theme.color("accent"))
                painter.drawRoundedRect(lit, 1.5, 1.5)


class SpeedMeter(QWidget):
    """The whole control: heading row with the meter and level name, the slider,
    stop labels underneath and one line saying what the level does."""

    changed = Signal(str)

    def __init__(self, level: str = "medium") -> None:
        super().__init__()
        level = level if level in LEVELS else "medium"
        self.slider = SpeedSlider(level)
        self.bars = SpeedBars(level)
        self.name = label(NAMES[level], "body")
        bold = QFont(self.name.font())
        bold.setWeight(QFont.Weight(TYPE["heading"][1]))
        self.name.setFont(bold)
        self.caption = label(CAPTIONS[level], "secondary", wrap=True)
        self.stops = [label(NAMES[name], "secondary") for name in LEVELS]

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(label("Speed", "heading"), 1)
        head.addWidget(self.bars, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.name)
        stops = QHBoxLayout()
        stops.setContentsMargins(PAD - 6, 0, PAD - 6, 0)
        stops.addWidget(self.stops[0])
        stops.addStretch(1)
        stops.addWidget(self.stops[1])
        stops.addStretch(1)
        stops.addWidget(self.stops[2])
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        column.addLayout(head)
        column.addWidget(self.slider)
        column.addLayout(stops)
        column.addWidget(self.caption)
        self.slider.changed.connect(self._show)
        self._mark(level)

    @property
    def level(self) -> str:
        return self.slider.level

    def _show(self, level: str) -> None:
        self.bars.show_level(level)
        self.name.setText(NAMES[level])
        self.caption.setText(CAPTIONS[level])
        self._mark(level)
        self.changed.emit(level)

    def _mark(self, level: str) -> None:
        for name, stop in zip(LEVELS, self.stops, strict=True):
            stop.setProperty("type", "body" if name == level else "secondary")
            stop.style().unpolish(stop)
            stop.style().polish(stop)
