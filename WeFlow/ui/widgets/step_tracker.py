"""The 4-step tracker: Copy · Process · Upload · Done (SPEC §7.3)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QFont, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..theme import TYPE, theme

STEPS = ("Copy", "Process", "Upload", "Done")
CIRCLE = 18


class StepTracker(QWidget):
    """Steps before ``current`` are done, ``current`` is ringed, the rest are pending."""

    def __init__(self) -> None:
        super().__init__()
        self._current = 0
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        theme.changed.connect(self.update)

    def current(self) -> int:
        return self._current

    def set_current(self, step: int) -> None:
        """0–3 marks that step as current; 4 marks every step done."""
        step = min(len(STEPS), max(0, step))
        if step != self._current:
            self._current = step
            self.update()
        self.setAccessibleName(
            "All steps done" if step >= len(STEPS) else f"Step {step + 1} of 4: {STEPS[step]}"
        )

    def step_state(self, index: int) -> str:
        if index < self._current:
            return "done"
        return "current" if index == self._current else "pending"

    def sizeHint(self) -> QSize:
        return QSize(320, CIRCLE + 8 + 14)

    def minimumSizeHint(self) -> QSize:
        return QSize(240, CIRCLE + 8 + 14)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        column = self.width() / len(STEPS)
        centre_y = CIRCLE / 2 + 1
        centres = [QPointF(column * (i + 0.5), centre_y) for i in range(len(STEPS))]
        good, border = theme.color("good"), theme.color("border")

        for i in range(len(STEPS) - 1):  # the 1 px joining line
            painter.setPen(QPen(good if i < self._current else border, 1))
            painter.drawLine(
                QPointF(centres[i].x() + CIRCLE / 2 + 4, centre_y),
                QPointF(centres[i + 1].x() - CIRCLE / 2 - 4, centre_y),
            )

        font = QFont(self.font())
        font.setPixelSize(TYPE["label"][0])
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        radius = CIRCLE / 2
        for i, centre in enumerate(centres):
            state = self.step_state(i)
            if state == "done":
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(good)
                painter.drawEllipse(centre, radius, radius)
                tick = QPen(theme.color("surface"), 1.5)
                tick.setCapStyle(Qt.PenCapStyle.RoundCap)
                tick.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(tick)
                painter.drawPolyline(
                    [
                        QPointF(centre.x() - 4, centre.y()),
                        QPointF(centre.x() - 1, centre.y() + 3),
                        QPointF(centre.x() + 4, centre.y() - 3),
                    ]
                )
            else:
                width = 2 if state == "current" else 1
                painter.setPen(QPen(good if state == "current" else border, width))
                painter.setBrush(theme.color("surface"))
                painter.drawEllipse(centre, radius - width / 2, radius - width / 2)
            painter.setPen(theme.color("text" if state == "current" else "text-2"))
            painter.drawText(
                QRectF(centre.x() - column / 2, CIRCLE + 8, column, 14),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                STEPS[i],
            )
