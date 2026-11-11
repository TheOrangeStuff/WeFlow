"""The activity drawer: a 320 px log that slides in from the right edge (UI-07)."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from .common import MOTION_MS, Button, Icon, label, motion_enabled

WIDTH = 320
MAX_ENTRIES = 200
LEVEL_ICONS = {
    "error": ("triangle-alert", "bad-strong"),
    "safe": ("circle-check", "good-strong"),
    "info": ("info", "text-2"),
}


class ActivityDrawer(QFrame):
    """Overlays its parent. The log repeats what rows and the panel already show."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("Drawer")
        self.setFixedWidth(WIDTH)
        self.entries: list[tuple[str, str, str]] = []  # time, level, message
        self._open = False
        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setDuration(MOTION_MS)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._slide.finished.connect(self._slide_done)

        self.close_button = Button(kind="icon", icon="x", tip="Close activity")
        self.close_button.clicked.connect(self.close_drawer)
        header = QHBoxLayout()
        header.addWidget(label("Activity", "heading"), 1)
        header.addWidget(self.close_button)

        self._empty = label(
            "Nothing yet. Card and upload activity will show here.", "secondary", True
        )
        self._list = QVBoxLayout()
        self._list.setSpacing(12)
        self._list.setContentsMargins(0, 0, 8, 0)
        self._list.addWidget(self._empty)
        self._list.addStretch(1)
        body = QWidget()
        body.setLayout(self._list)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(body)

        column = QVBoxLayout(self)
        column.setContentsMargins(24, 16, 16, 24)
        column.setSpacing(16)
        column.addLayout(header)
        column.addWidget(scroll, 1)
        parent.installEventFilter(self)
        self.hide()

    def is_open(self) -> bool:
        return self._open

    def add(self, level: str, message: str, when: datetime | None = None) -> None:
        stamp = (when or datetime.now()).strftime("%H:%M")
        self.entries.insert(0, (stamp, level, message))
        self._empty.hide()
        icon_name, token = LEVEL_ICONS.get(level, LEVEL_ICONS["info"])
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        line.addWidget(Icon(icon_name, token, 16), 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(0)
        text.addWidget(label(message, "body", wrap=True))
        text.addWidget(label(stamp, "label"))
        line.addLayout(text, 1)
        self._list.insertWidget(0, row)
        if len(self.entries) > MAX_ENTRIES:
            self.entries.pop()
            old = self._list.takeAt(MAX_ENTRIES)
            if old is not None and old.widget() is not None:
                old.widget().deleteLater()

    # ------------------------------------------------------------------ slide
    def _spot(self, opened: bool) -> QPoint:
        parent = self.parentWidget()
        return QPoint(parent.width() - (WIDTH if opened else 0), 0)

    def open_drawer(self) -> None:
        if self._open:
            return
        self._open = True
        self.setFixedHeight(self.parentWidget().height())
        self.raise_()
        self.show()
        self._move(self._spot(False), self._spot(True))
        self.close_button.setFocus()

    def close_drawer(self) -> None:
        if not self._open:
            return
        self._open = False
        self._move(self.pos(), self._spot(False))

    def toggle(self) -> None:
        if self._open:
            self.close_drawer()
        else:
            self.open_drawer()

    def _move(self, start: QPoint, end: QPoint) -> None:
        self._slide.stop()
        if motion_enabled():
            self._slide.setStartValue(start)
            self._slide.setEndValue(end)
            self._slide.start()
        else:
            self.move(end)
            self._slide_done()

    def _slide_done(self) -> None:
        if not self._open:
            self.hide()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self.setFixedHeight(self.parentWidget().height())
            if self._open:
                self.move(self._spot(True))
        return False

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close_drawer()
        else:
            super().keyPressEvent(event)
