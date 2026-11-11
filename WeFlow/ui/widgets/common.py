"""Small building blocks shared by every screen."""

from __future__ import annotations

import os

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt
from PySide6.QtGui import QKeyEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QAbstractButton, QFrame, QLabel, QPushButton, QSizePolicy, QWidget

from ..theme import theme

MOTION_MS = 200


def motion_enabled() -> bool:
    return not os.environ.get("WeFlow_NO_MOTION")


def restyle(widget: QWidget, **props: object) -> None:
    """Set dynamic properties and make the stylesheet notice them."""
    changed = False
    for name, value in props.items():
        if widget.property(name) != value:
            widget.setProperty(name, value)
            changed = True
    if changed:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()


def label(text: str = "", kind: str = "body", wrap: bool = False) -> QLabel:
    """A QLabel styled as one of the type tokens (display, title, heading, …)."""
    widget = QLabel(text)
    widget.setProperty("type", kind)
    widget.setWordWrap(wrap)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


def separator() -> QFrame:
    line = QFrame()
    line.setObjectName("Separator")
    line.setFixedHeight(1)
    return line


class Icon(QLabel):
    """A line icon that redraws itself when the theme changes."""

    def __init__(self, name: str, token: str = "text", size: int = 16) -> None:
        super().__init__()
        self._name, self._token, self._size = name, token, size
        self.setFixedSize(size, size)
        theme.changed.connect(self._retheme)
        self._retheme()

    def set_icon(self, name: str, token: str | None = None) -> None:
        self._name = name
        self._token = token or self._token
        self._retheme()

    def _retheme(self) -> None:
        self.setPixmap(theme.pixmap(self._name, self._token, self._size))


class Button(QPushButton):
    """A push button of one ``kind`` (see theme.build_qss) with an optional line icon.

    Focus comes from the keyboard only, so focus rings show for Tab navigation
    without lingering after a mouse click.
    """

    def __init__(
        self,
        text: str = "",
        kind: str = "",
        icon: str = "",
        icon_token: str = "text",
        icon_after: bool = False,
        tip: str = "",
    ) -> None:
        super().__init__(text)
        self._icon, self._icon_token = icon, icon_token
        if kind:
            self.setProperty("kind", kind)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        if icon_after:
            self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        if tip:
            self.setToolTip(tip)
            self.setAccessibleName(tip)
        self.setIconSize(QSize(16, 16))
        theme.changed.connect(self._retheme)
        self._retheme()

    def set_icon(self, icon: str, token: str | None = None) -> None:
        self._icon = icon
        self._icon_token = token or self._icon_token
        self._retheme()

    def _retheme(self) -> None:
        if self._icon:
            self.setIcon(theme.icon(self._icon, self._icon_token, 16))


class Toggle(QAbstractButton):
    """A small on/off switch."""

    def __init__(self, checked: bool = False) -> None:
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(36, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._knob = 1.0 if checked else 0.0
        self._slide = QPropertyAnimation(self, b"knob", self)
        self._slide.setDuration(120)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)
        theme.changed.connect(self.update)

    def _get_knob(self) -> float:
        return self._knob

    def _set_knob(self, value: float) -> None:
        self._knob = value
        self.update()

    knob = Property(float, _get_knob, _set_knob)

    def _animate(self, on: bool) -> None:
        if motion_enabled() and self.isVisible():
            self._slide.stop()
            self._slide.setStartValue(self._knob)
            self._slide.setEndValue(1.0 if on else 0.0)
            self._slide.start()
        else:
            self._set_knob(1.0 if on else 0.0)

    def sizeHint(self) -> QSize:
        return QSize(36, 20)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.toggle()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(2, 2, 32, 16)
        on = self.isChecked()
        painter.setPen(Qt.PenStyle.NoPen)
        fill = theme.color("accent" if on else "border")
        if not self.isEnabled():
            fill = theme.color("border")
        painter.setBrush(fill)
        painter.drawRoundedRect(track, 8, 8)
        painter.setBrush(theme.color("on-accent" if on else "surface"))
        if not on:
            painter.setPen(theme.color("text-2"))
        painter.drawEllipse(QRectF(4 + self._knob * 16, 4, 12, 12))
        if self.hasFocus():
            pen = painter.pen()
            pen.setColor(theme.color("accent"))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(1, 1, 34, 18), 9, 9)


class ThinProgress(QWidget):
    """A 4 px, fully rounded progress bar. Neutral: colour is kept for status."""

    def __init__(self) -> None:
        super().__init__()
        self._value = 0.0
        self.setFixedHeight(4)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        theme.changed.connect(self.update)

    def value(self) -> float:
        return self._value

    def set_value(self, value: float) -> None:
        value = min(1.0, max(0.0, value))
        if value != self._value:
            self._value = value
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.color("border"))
        painter.drawRoundedRect(QRectF(0, 0, self.width(), 4), 2, 2)
        if self._value > 0:
            painter.setBrush(theme.color("text-2"))
            painter.drawRoundedRect(QRectF(0, 0, max(4.0, self.width() * self._value), 4), 2, 2)
