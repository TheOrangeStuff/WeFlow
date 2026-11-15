"""The three stages of the working face: Transfer · Process · Upload (1.10.0, UI-02).

Each stage is a big line icon inside a ring. While a stage works, the ring fills with
its progress and a soft halo breathes around it; once it is finished the ring becomes
a green tick. Colour always comes with a word under the ring (§7.1 rule 4).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy, QWidget

from ..bridge import Batch, plural, service_name, speed
from ..theme import TYPE, theme
from .common import motion_enabled

STAGES = ("Transfer", "Process", "Upload")
ICONS = ("card-sim", "sliders-horizontal", "cloud-upload")
STATE_NAMES = ("pending", "active", "waiting", "done", "error", "off")
RING = 64  # ring diameter
ICON = 28
STROKE = 3
HALO = 6  # how far the halo breathes out
PULSE_MS = 1600
POP_MS = 240
HEIGHT = RING + HALO * 2 + 12 + 16 + 4 + 16 + 2 + 16 + 2 + 14


@dataclass(frozen=True)
class StageView:
    """What one stage shows."""

    state: str = "pending"  # pending | active | waiting | done | error | off
    progress: float | None = None  # while active; None = not known yet
    stat: str = ""  # "120 / 248 · 38.2 MB/s"
    note: str = ""  # the word that goes with the colour: "Safe to remove", "Done"…
    card: str = ""  # which card this stage is showing


IDLE_STAGES = (StageView(), StageView(), StageView())


def _fraction(done: int, total: int) -> float:
    return min(1.0, done / total) if total else 1.0


def transfer_stage(batch: Batch | None, card_in: bool = False, ejected: bool = False) -> StageView:
    if batch is None:
        return StageView()
    card = batch.label
    if batch.state == "copying":
        seen = batch.copied + batch.skipped
        if not batch.total:
            return StageView("active", None, "Reading the card…", "Keep card in", card)
        stat = f"{seen} / {batch.total}"
        if batch.rate > 0:
            stat += f" · {speed(batch.rate)}"
        return StageView("active", _fraction(seen, batch.total), stat, "Keep card in", card)
    if batch.state == "error":
        return StageView("error", None, "", "Copy failed", card)
    stat = f"{batch.copied} copied" if batch.copied else "Nothing new"
    if batch.is_card and ejected:
        note = "Ejected"
    elif batch.is_card and card_in:
        note = "Safe to remove"
    else:
        note = "Verified"
    return StageView("done", None, stat, note, card)


def process_stage(batch: Batch | None) -> StageView:
    if batch is None or batch.state in ("copying", "error"):
        return StageView()
    card, count = batch.label, batch.copied
    if batch.state == "ready":
        return StageView("waiting", None, f"{plural(batch.ready)} waiting", "Press Process", card)
    if batch.state in ("safe", "processing"):
        stat = f"{batch.processed} / {count}"
        return StageView("active", _fraction(batch.processed, count), stat, "Tuning", card)
    if batch.state == "attention" and batch.processed < count:
        return StageView("error", None, f"{batch.processed} / {count}", "Needs attention", card)
    stat = f"{batch.processed} processed" if count else "Nothing new"
    return StageView("done", None, stat, "Done", card)


def upload_stage(batch: Batch | None) -> StageView:
    if batch is None:
        return StageView()
    card, count = batch.label, batch.copied
    if batch.destination == "none":
        return StageView("off", None, "", "Library only", card)
    if batch.state in ("copying", "error", "ready", "safe", "processing"):
        return StageView()
    if batch.state == "held":
        return StageView("waiting", None, f"{plural(batch.held)} ready", "Press Upload", card)
    if batch.state == "uploading":
        stat = f"{batch.uploaded} / {count}"
        if batch.rate > 0:
            stat += f" · {speed(batch.rate)}"
        return StageView("active", _fraction(batch.uploaded, count), stat, "Uploading", card)
    if batch.state == "attention":
        return StageView("error", None, f"{batch.uploaded} / {count}", "Needs attention", card)
    stat = f"{batch.uploaded} uploaded" if count else "Nothing new"
    return StageView("done", None, stat, service_name(batch.destination), card)


def stages_for(
    transfer: Batch | None,
    process: Batch | None = None,
    upload: Batch | None = None,
    *,
    card_in: bool = False,
    ejected: bool = False,
) -> tuple[StageView, StageView, StageView]:
    """Each stage can follow a different card: a new card copies while the one before it
    is still uploading. ``process`` and ``upload`` default to ``transfer``'s batch."""
    process = process if process is not None else transfer
    upload = upload if upload is not None else transfer
    return (
        transfer_stage(transfer, card_in, ejected),
        process_stage(process),
        upload_stage(upload),
    )


class StageColumn(QWidget):
    """One stage: ring, name, stats, status word and card name, all painted."""

    def __init__(self, index: int, strip: StageStrip) -> None:
        super().__init__()
        self.index = index
        self._strip = strip
        self.view = StageView()
        self._pop = 1.0  # 0 → 1 as the tick appears
        self._pop_anim = QVariantAnimation(self)
        self._pop_anim.setDuration(POP_MS)
        self._pop_anim.setStartValue(0.0)
        self._pop_anim.setEndValue(1.0)
        self._pop_anim.setEasingCurve(QEasingCurve.Type.OutBack)
        self._pop_anim.valueChanged.connect(self._on_pop)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(HEIGHT)
        theme.changed.connect(self.update)
        self._describe()

    @property
    def name(self) -> str:
        return STAGES[self.index]

    def set_view(self, view: StageView) -> None:
        if view == self.view:
            return
        became_done = view.state == "done" and self.view.state != "done"
        self.view = view
        if became_done and motion_enabled() and self.isVisible():
            self._pop = 0.0
            self._pop_anim.start()
        self._describe()
        self.update()

    def _on_pop(self, value: object) -> None:
        self._pop = float(value)  # type: ignore[arg-type]
        self.update()

    def _describe(self) -> None:
        parts = [self.name, self.view.note, self.view.stat, self.view.card]
        self.setAccessibleName(". ".join(p for p in parts if p))

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(150, HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(RING + HALO * 2, HEIGHT)

    # ------------------------------------------------------------ painting
    def _font(self, kind: str) -> QFont:
        font = QFont(self.font())
        px, weight, _ = TYPE[kind]
        font.setPixelSize(px)
        font.setWeight(QFont.Weight(weight))
        return font

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        view = self.view
        centre = QPointF(self.width() / 2, HALO + RING / 2)
        radius = RING / 2 - STROKE / 2
        border = theme.color("border")
        tone = {
            "active": "warn",
            "waiting": "warn",
            "done": "good",
            "error": "bad",
        }.get(view.state, "")
        colour = theme.color(tone) if tone else border

        # Breathing halo while working.
        if view.state == "active":
            phase = self._strip.phase
            halo = QColor(colour)
            halo.setAlphaF(0.28 * (1.0 - phase))
            painter.setPen(QPen(halo, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            grow = radius + STROKE + phase * HALO
            painter.drawEllipse(centre, grow, grow)

        ring = QRectF(centre.x() - radius, centre.y() - radius, radius * 2, radius * 2)
        if view.state == "done":
            scale = max(0.0, self._pop)
            r = RING / 2 * min(1.08, scale)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            painter.drawEllipse(centre, r, r)
            tick = QPen(theme.color("surface"), 3.5)
            tick.setCapStyle(Qt.PenCapStyle.RoundCap)
            tick.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(tick)
            s = RING / 64 * min(1.0, scale)
            painter.drawPolyline(
                [
                    QPointF(centre.x() - 12 * s, centre.y() + 1 * s),
                    QPointF(centre.x() - 4 * s, centre.y() + 9 * s),
                    QPointF(centre.x() + 13 * s, centre.y() - 8 * s),
                ]
            )
        else:
            track = QPen(border, STROKE if view.state != "off" else 1.5)
            if view.state == "off":
                track.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(track)
            painter.setBrush(theme.color("surface"))
            painter.drawEllipse(ring)
            arc = QPen(colour, STROKE)
            arc.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(arc)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if view.state == "active" and view.progress is None:
                # Progress not known yet: a quarter arc goes round.
                start = int((90 - self._strip.turn * 360) * 16)
                painter.drawArc(ring, start, -90 * 16)
            elif view.state == "active":
                span = max(0.02, min(1.0, view.progress or 0.0))
                painter.drawArc(ring, 90 * 16, -int(span * 360 * 16))
            elif view.state in ("waiting", "error"):
                painter.drawEllipse(ring)
            if view.state == "error":
                cross = QPen(theme.color("bad-strong"), 3)
                cross.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(cross)
                d = 9
                painter.drawLine(centre + QPointF(-d, -d), centre + QPointF(d, d))
                painter.drawLine(centre + QPointF(-d, d), centre + QPointF(d, -d))
            else:
                token = {"active": "warn-strong", "waiting": "warn-strong"}.get(
                    view.state, "text-2"
                )
                pixmap = theme.pixmap(ICONS[self.index], token, ICON)
                if view.state in ("pending", "off"):
                    painter.setOpacity(0.55)
                painter.drawPixmap(
                    QRectF(centre.x() - ICON / 2, centre.y() - ICON / 2, ICON, ICON),
                    pixmap,
                    QRectF(pixmap.rect()),
                )
                painter.setOpacity(1.0)

        # Words under the ring.
        width = float(self.width())
        y = float(HALO * 2 + RING + 12)
        lit = view.state not in ("pending", "off")
        name_font = self._font("heading")
        name_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
        lines: list[tuple[str, QFont, str, float]] = [
            (self.name.upper(), name_font, "text" if lit else "text-2", 16 + 4),
            (view.stat, self._font("secondary"), "text-2", 16 + 2),
            (view.note, self._font("body"), f"{tone}-strong" if tone else "text-2", 16 + 2),
            (view.card, self._font("label"), "text-2", 14),
        ]
        for text, font, token, step in lines:
            if text:
                painter.setFont(font)
                painter.setPen(theme.color(token))
                metrics = painter.fontMetrics()
                shown = metrics.elidedText(text, Qt.TextElideMode.ElideRight, int(width) - 8)
                painter.drawText(QRectF(0, y, width, step), Qt.AlignmentFlag.AlignHCenter, shown)
            y += step


class StageStrip(QWidget):
    """Three stage columns with faint separators, and the shared animation clock."""

    def __init__(self) -> None:
        super().__init__()
        self.phase = 0.0  # eased 0 → 1, for the halo
        self.turn = 0.0  # linear 0 → 1, for the arc that goes round
        self.columns = [StageColumn(i, self) for i in range(len(STAGES))]
        self._separators: list[QFrame] = []
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        for index, column in enumerate(self.columns):
            if index:
                line = QFrame()
                line.setObjectName("StageSeparator")
                line.setFixedWidth(1)
                line.setFixedHeight(RING + HALO * 2)
                self._separators.append(line)
                row.addWidget(line, 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(column, 1)
        self.setFixedHeight(HEIGHT)
        self._clock = QVariantAnimation(self)
        self._clock.setDuration(PULSE_MS)
        self._clock.setStartValue(0.0)
        self._clock.setEndValue(1.0)
        self._clock.setLoopCount(-1)
        self._clock.valueChanged.connect(self._tick)

    def views(self) -> tuple[StageView, ...]:
        return tuple(c.view for c in self.columns)

    def states(self) -> list[str]:
        return [c.view.state for c in self.columns]

    def set_views(self, views: tuple[StageView, StageView, StageView]) -> None:
        for column, view in zip(self.columns, views, strict=True):
            column.set_view(view)
        working = any(v.state == "active" for v in views)
        if working and motion_enabled():
            if self._clock.state() != QVariantAnimation.State.Running:
                self._clock.start()
        else:
            self._clock.stop()
            self.phase = 0.0

    def animating(self) -> bool:
        return self._clock.state() == QVariantAnimation.State.Running

    def _tick(self, value: object) -> None:
        self.turn = float(value)  # type: ignore[arg-type]
        # Ease in and out so the halo breathes rather than ticks.
        self.phase = 0.5 - 0.5 * math.cos(math.pi * float(value))  # type: ignore[arg-type]
        for column in self.columns:
            if column.view.state == "active":
                column.update()
