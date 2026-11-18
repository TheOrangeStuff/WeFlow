"""The three stages along the bottom of the working face: Transfer · Process · Upload.

1.14.0: each stage is a big line icon with the stage name and a status word beside it
("Idle" until it has something to do). While a stage works, its icon breathes, a thin
bar under the words fills with progress and the stats show; once it is finished the
icon turns into a green tick. Colour always comes with a word (§7.1 rule 4).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QEasingCurve, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QFont, QFontMetricsF, QPainter, QPaintEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy, QWidget

from ..bridge import Batch, plural, service_name, speed
from ..theme import TYPE, theme
from .common import motion_enabled

STAGES = ("Transfer", "Process", "Upload")
ICONS = ("card-sim", "sliders-horizontal", "cloud-upload")
STATE_NAMES = ("pending", "active", "waiting", "done", "error", "off")
ICON = 44
GAP = 20  # between the icon and the words
PULSE_MS = 1600
POP_MS = 240
HEIGHT = 112
IDLE_WORD = "Idle"
TONES = {"active": "warn", "waiting": "warn", "done": "good", "error": "bad"}


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
    """One stage: icon, name, status word, stats and progress, all painted."""

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

    @property
    def word(self) -> str:
        """The status word under the name."""
        if self.view.state == "pending":
            return IDLE_WORD
        return self.view.note

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
        parts = [self.name, self.word, self.view.stat, self.view.card]
        self.setAccessibleName(". ".join(p for p in parts if p))

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(240, HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(ICON + GAP + 80, HEIGHT)

    # ------------------------------------------------------------ painting
    def _font(self, kind: str) -> QFont:
        font = QFont(self.font())
        px, weight, _ = TYPE[kind]
        font.setPixelSize(px)
        font.setWeight(QFont.Weight(weight))
        return font

    def _block_width(self, name_font: QFont, word_font: QFont) -> float:
        texts = [(self.name, name_font), (self.word, word_font)]
        widest = max(QFontMetricsF(f).horizontalAdvance(t) for t, f in texts if t)
        return min(float(self.width() - 16), ICON + GAP + max(widest, 120.0))

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        view = self.view
        tone = TONES.get(view.state, "")
        strong = f"{tone}-strong" if tone else ""
        name_font = self._font("stage")
        word_font = self._font("hero-sub")
        word_font.setPixelSize(17)
        stat_font = self._font("secondary")

        # The icon and words sit together, centred in the column.
        block = self._block_width(name_font, word_font)
        left = max(8.0, (self.width() - block) / 2)
        top = (HEIGHT - ICON) / 2 - 6
        icon_rect = QRectF(left, top, ICON, ICON)

        if view.state == "done":
            scale = max(0.0, min(1.08, self._pop))
            size = ICON * scale
            box = QRectF(
                icon_rect.center().x() - size / 2, icon_rect.center().y() - size / 2, size, size
            )
            pixmap = theme.pixmap("circle-check", strong, ICON, stroke=2.0)
            painter.drawPixmap(box, pixmap, QRectF(pixmap.rect()))
        else:
            token = strong if tone else "text"
            name = "triangle-alert" if view.state == "error" else ICONS[self.index]
            pixmap = theme.pixmap(name, token, ICON, stroke=2.0)
            if view.state == "active":
                painter.setOpacity(1.0 - 0.45 * self._strip.phase)
            elif view.state == "off":
                painter.setOpacity(0.45)
            painter.drawPixmap(icon_rect, pixmap, QRectF(pixmap.rect()))
            painter.setOpacity(1.0)

        # Words beside the icon.
        x = left + ICON + GAP
        width = max(40.0, self.width() - x - 8)
        y = top - 6
        lines: list[tuple[str, QFont, str, float]] = [
            (self.name, name_font, "text" if view.state != "off" else "text-2", 30),
            (self.word, word_font, strong or "text-2", 24),
        ]
        stat = " · ".join(p for p in (view.card, view.stat) if p)
        if view.state not in ("pending", "off") and stat:
            lines.append((stat, stat_font, "text-2", 18))
        for text, font, token, step in lines:
            if text:
                painter.setFont(font)
                painter.setPen(theme.color(token))
                shown = painter.fontMetrics().elidedText(
                    text, Qt.TextElideMode.ElideRight, int(width)
                )
                painter.drawText(
                    QRectF(x, y, width, step),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    shown,
                )
            y += step

        # Progress under the words while working (a short bar slides while unknown).
        if view.state == "active":
            bar = QRectF(x, y + 6, min(width, 180.0), 3)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.color("border"))
            painter.drawRoundedRect(bar, 1.5, 1.5)
            painter.setBrush(theme.color(tone))
            if view.progress is None:
                span = bar.width() * 0.3
                start = bar.left() + (bar.width() - span) * self._strip.phase
                painter.drawRoundedRect(QRectF(start, bar.top(), span, 3), 1.5, 1.5)
            else:
                share = max(0.02, min(1.0, view.progress))
                painter.drawRoundedRect(
                    QRectF(bar.left(), bar.top(), bar.width() * share, 3), 1.5, 1.5
                )


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
                line.setFixedHeight(HEIGHT - 16)
                self._separators.append(line)
                row.addWidget(line, 0, Qt.AlignmentFlag.AlignVCenter)
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
