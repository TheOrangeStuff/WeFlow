"""The status panel: the one thing to read from across the room (SPEC §7.3)."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPropertyAnimation, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..bridge import Batch, plural, service_name
from .common import MOTION_MS, Button, Icon, ThinProgress, label, motion_enabled, restyle
from .step_tracker import StepTracker

# state -> (panel tone, icon, icon colour token)
LOOKS = {
    "idle": ("neutral", "card-sim", "text-2"),
    "paused": ("neutral", "circle-pause", "text-2"),
    "copying": ("warn", "hourglass", "warn-strong"),
    "safe": ("good-edge", "circle-check", "good-strong"),
    "working": ("neutral", "refresh-cw", "text-2"),
    "held": ("warn", "clock", "warn-strong"),
    "done": ("good", "circle-check", "good-strong"),
    "attention": ("bad-edge", "triangle-alert", "bad-strong"),
    "copy_error": ("bad", "triangle-alert", "bad-strong"),
}
STATES = tuple(LOOKS)


@dataclass(frozen=True)
class PanelView:
    """Everything the panel shows, worked out from a batch."""

    state: str = "idle"
    headline: str = "Insert a card"
    subline: str = ""
    step: int = -1  # tracker position; -1 hides the tracker
    progress: float | None = None  # None hides the bar
    batch_id: int | None = None
    event_id: str = ""
    source: str = ""


def _fraction(done: int, total: int) -> float:
    return done / total if total else 1.0


def view_for(
    batch: Batch | None, *, event_name: str = "", paused: bool = False, card_present: bool = False
) -> PanelView:
    """Map the most recent batch (or none) to what the panel should show."""
    if batch is None:
        if paused:
            return PanelView("paused", "Paused", "Card watching is off")
        target = f"Photos go to {event_name}" if event_name else "Choose an event to begin"
        return PanelView("idle", "Insert a card", target)

    common = {"batch_id": batch.id, "event_id": batch.event_id, "source": batch.source}
    count = batch.copied
    if batch.state == "error":
        reason = batch.error or "The copy failed."
        if "Re-insert" not in reason:
            reason = f"{reason} Re-insert the card to try again."
        return PanelView("copy_error", "Don't remove yet — copy failed", reason, **common)
    if batch.state == "copying":
        seen = batch.copied + batch.skipped
        sub = f"{seen} of {plural(batch.total)}" if batch.total else "Reading the card…"
        return PanelView(
            "copying", "Copying — keep card in", sub, 0, _fraction(seen, batch.total), **common
        )

    # From here on every file is copied and verified.
    step, progress = 4, None
    if batch.state in ("safe", "processing"):
        step, progress = 1, _fraction(batch.processed, count)
    elif batch.state == "uploading":
        step, progress = 2, _fraction(batch.uploaded, count)
    elif batch.state == "held":
        step = 2
    elif batch.state == "attention":
        step = 1 if batch.processed < count else 2

    if batch.is_card and card_present:
        copied = f"{plural(count)} copied and verified" if count else "nothing new on this card"
        return PanelView(
            "safe", "Safe to remove", f"{batch.label} · {copied}", step, progress, **common
        )
    if batch.state in ("safe", "processing"):
        sub = f"{batch.processed} of {plural(count)}"
        return PanelView("working", "Processing", sub, step, progress, **common)
    if batch.state == "uploading":
        sub = f"{batch.uploaded} of {plural(count)}"
        if batch.error:
            sub = f"{sub} · {batch.error}"
        return PanelView("working", "Uploading", sub, step, progress, **common)
    if batch.state == "held":
        return PanelView("held", "Ready for review", f"{plural(batch.held)} held", **common)
    if batch.state == "attention":
        reason = batch.error or "Some photos failed. Press Retry."
        return PanelView("attention", "Needs attention", reason, **common)
    if not count:
        sub = "Nothing new on this card"
    elif batch.destination == "none":
        sub = f"{plural(batch.processed)} ready in the library"
    else:
        sub = f"{plural(batch.uploaded)} uploaded to {service_name(batch.destination)}"
    return PanelView("done", "All done", sub, **common)


class StatusPanel(QFrame):
    """Shows one ``PanelView``. Buttons appear only in the states they apply to."""

    eject_clicked = Signal()
    release_clicked = Signal()
    retry_clicked = Signal()
    details_clicked = Signal()
    open_folder_clicked = Signal()
    resume_clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("StatusPanel")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(240)
        self.view = PanelView()
        self._fade: QLabel | None = None
        self._fade_anim: QPropertyAnimation | None = None

        self.icon = Icon("card-sim", "text-2", 20)
        self.headline = label("", "display", wrap=True)
        self.subline = label("", "body", wrap=True)
        self.tracker = StepTracker()
        self.progress = ThinProgress()

        self.eject = Button("Eject", "good", icon="eject", icon_token="good-strong")
        self.open_folder = Button("Open folder", "outline")
        self.release = Button("Release", "primary")
        self.retry = Button("Retry", "primary")
        self.details = Button("Details", "outline")
        self.resume = Button("Resume", "outline", icon="play")
        self.eject.clicked.connect(self.eject_clicked)
        self.open_folder.clicked.connect(self.open_folder_clicked)
        self.release.clicked.connect(self.release_clicked)
        self.retry.clicked.connect(self.retry_clicked)
        self.details.clicked.connect(self.details_clicked)
        self.resume.clicked.connect(self.resume_clicked)
        self._buttons = {
            "paused": (self.resume,),
            "safe": (self.eject,),
            "held": (self.release, self.open_folder),
            "attention": (self.retry, self.details),
            "copy_error": (self.retry,),
        }

        self._track_box = QWidget()
        track = QVBoxLayout(self._track_box)
        track.setContentsMargins(0, 24, 0, 0)
        track.setSpacing(12)
        track.addWidget(self.tracker)
        track.addWidget(self.progress)

        self._button_box = QWidget()
        button_row = QHBoxLayout(self._button_box)
        button_row.setContentsMargins(0, 24, 0, 0)
        button_row.setSpacing(8)
        self._all_buttons = (
            self.eject,
            self.release,
            self.retry,
            self.open_folder,
            self.details,
            self.resume,
        )
        for button in self._all_buttons:
            button_row.addWidget(button)
        button_row.addStretch(1)

        column = QVBoxLayout(self)
        column.setContentsMargins(24, 24, 24, 24)
        column.setSpacing(0)
        column.addStretch(1)
        column.addWidget(self.icon)
        column.addSpacing(12)
        column.addWidget(self.headline)
        column.addSpacing(4)
        column.addWidget(self.subline)
        column.addWidget(self._track_box)
        column.addWidget(self._button_box)
        column.addStretch(1)
        self._apply(self.view)

    @property
    def state(self) -> str:
        return self.view.state

    def visible_buttons(self) -> list[str]:
        return [b.text() for b in self._buttons.get(self.view.state, ()) if not b.isHidden()]

    def show_view(self, view: PanelView) -> None:
        if view == self.view:
            return
        if view.state != self.view.state:
            self._crossfade()
        self._apply(view)

    def _apply(self, view: PanelView) -> None:
        self.view = view
        tone, icon, token = LOOKS[view.state]
        restyle(self, tone=tone)
        tinted = tone != "neutral"
        restyle(self.subline, tinted=tinted)
        self.icon.set_icon(icon, token)
        self.headline.setText(view.headline)
        self.subline.setText(view.subline)
        self.subline.setVisible(bool(view.subline))
        self.tracker.setVisible(view.step >= 0)
        if view.step >= 0:
            self.tracker.set_current(view.step)
        self.progress.setVisible(view.progress is not None)
        if view.progress is not None:
            self.progress.set_value(view.progress)
        self._track_box.setVisible(view.step >= 0 or view.progress is not None)
        shown = self._buttons.get(view.state, ())
        for button in self._all_buttons:
            button.setVisible(button in shown)
        self._button_box.setVisible(bool(shown))
        self.setAccessibleName(f"{view.headline}. {view.subline}")

    def set_ejecting(self, busy: bool) -> None:
        self.eject.setEnabled(not busy)
        self.eject.setText("Ejecting…" if busy else "Eject")

    def _crossfade(self) -> None:
        """Fade the old look out over the new one (200 ms)."""
        if not motion_enabled() or not self.isVisible():
            return
        if self._fade is not None:
            self._fade.deleteLater()
        fade = QLabel(self)
        fade.setPixmap(self.grab())
        fade.setGeometry(self.rect())
        fade.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        effect = QGraphicsOpacityEffect(fade)
        fade.setGraphicsEffect(effect)
        fade.show()
        anim = QPropertyAnimation(effect, b"opacity", fade)
        anim.setDuration(MOTION_MS)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.finished.connect(self._fade_done)
        self._fade, self._fade_anim = fade, anim
        anim.start()

    def _fade_done(self) -> None:
        if self._fade is not None:
            self._fade.deleteLater()
            self._fade = None
            self._fade_anim = None
