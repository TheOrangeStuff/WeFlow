"""The status panel: the one thing to read from across the room (SPEC §7.3).

1.10.0: the small 4-step tracker and progress bar became three big stages (Transfer,
Process, Upload) in ``stages.py``. 1.14.0: a big headline with its status icon on the
left of a hero area, the newest photo in black and white fading in from the right,
and the stages in a row along the bottom.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from PySide6.QtCore import QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPaintEvent, QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..bridge import Batch, duration, plural, service_name, speed
from ..theme import theme
from .common import MOTION_MS, Button, Icon, label, motion_enabled, restyle, separator
from .stages import IDLE_STAGES, StageStrip, StageView, stages_for

# state -> (panel tone, icon, icon colour token)
LOOKS = {
    "idle": ("neutral", "card-sim", "text-2"),
    "paused": ("neutral", "pause", "accent"),
    "copying": ("warn", "hourglass", "warn-strong"),
    "safe": ("good-edge", "circle-check", "good-strong"),
    "working": ("neutral", "refresh-cw", "text-2"),
    "ready": ("good-edge", "circle-check", "good-strong"),
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
    step: int = -1  # furthest stage reached (0 copy … 3 done); -1 = no batch
    progress: float | None = None  # of the current step, when it is working
    batch_id: int | None = None
    event_id: str = ""
    source: str = ""
    card: bool = False  # the card is still in, so Eject applies
    stages: tuple[StageView, StageView, StageView] = field(default=IDLE_STAGES)


def _fraction(done: int, total: int) -> float:
    return done / total if total else 1.0


def copy_line(batch: Batch) -> str:
    """While copying: ``12 of 25 photos · 13 left · 38.2 MB/s · about 20 s left``."""
    seen = batch.copied + batch.skipped
    parts = [f"{seen} of {plural(batch.total)}", f"{batch.total - seen} left"]
    if batch.rate > 0:
        parts.append(speed(batch.rate))
        remaining = batch.bytes_total - batch.bytes_done
        if remaining > 0:
            parts.append(f"about {duration(max(1.0, remaining / batch.rate))} left")
    return " · ".join(parts)


def upload_line(batch: Batch) -> str:
    """While uploading: ``5 of 25 photos · 20 left · 3.1 MB/s · about 1 min left``."""
    left = batch.copied - batch.uploaded - batch.failed
    parts = [f"{batch.uploaded} of {plural(batch.copied)}", f"{max(0, left)} left"]
    if batch.rate > 0:
        parts.append(speed(batch.rate))
        if batch.uploaded and left > 0:
            remaining = left * batch.upload_bytes / batch.uploaded
            parts.append(f"about {duration(max(1.0, remaining / batch.rate))} left")
    return " · ".join(parts)


def copied_text(batch: Batch) -> str:
    """Once verified: ``CARD_A · 25 photos copied and verified in 14 s``."""
    if not batch.copied:
        return f"{batch.label} · nothing new on this card"
    took = f" in {duration(batch.copy_seconds)}" if batch.copy_seconds >= 1 else ""
    return f"{batch.label} · {plural(batch.copied)} copied and verified{took}"


def view_for(
    batch: Batch | None,
    *,
    event_name: str = "",
    paused: bool = False,
    card_present: bool = False,
    watching: str = "",
) -> PanelView:
    """Map the most recent batch (or none) to what the panel should show. All three
    stages follow this batch; the main window points them at other cards when needed.
    ``watching`` names the folder the camera sends photos to (FR-48), if any."""
    view = _headline_for(
        batch, event_name=event_name, paused=paused, card_present=card_present, watching=watching
    )
    if batch is None:
        return view
    stages = stages_for(batch, card_in=batch.is_card and card_present)
    return replace(view, stages=stages)


def _headline_for(
    batch: Batch | None,
    *,
    event_name: str = "",
    paused: bool = False,
    card_present: bool = False,
    watching: str = "",
) -> PanelView:
    if batch is None:
        if paused:
            return PanelView("paused", "Paused", "Card watching is off")
        target = f"Photos go to {event_name}" if event_name else "Choose an event to begin"
        if watching:
            target = f"Watching {watching} · {target}"
        return PanelView("idle", "Insert a card", target)

    common = {"batch_id": batch.id, "event_id": batch.event_id, "source": batch.source}
    count = batch.copied
    if batch.state == "error":
        reason = batch.error or "The copy failed."
        if batch.is_card and "Re-insert" not in reason:
            reason = f"{reason} Re-insert the card to try again."
        return PanelView("copy_error", "Don't remove yet — copy failed", reason, **common)
    if batch.state == "copying":
        seen = batch.copied + batch.skipped
        sub = copy_line(batch) if batch.total else "Reading the card…"
        return PanelView(
            "copying",
            "Copying — keep card in" if batch.is_card else "Copying",
            sub,
            0,
            _fraction(seen, batch.total),
            **common,
        )

    # From here on every file is copied and verified.
    step, progress = 4, None
    if batch.state in ("safe", "processing"):
        step, progress = 1, _fraction(batch.processed, count)
    elif batch.state == "uploading":
        step, progress = 2, _fraction(batch.uploaded, count)
    elif batch.state == "held":
        step = 2
    elif batch.state == "ready":
        step = 1

    if batch.state == "ready":  # paced mode: copied and verified, waiting for the go-ahead
        card = batch.is_card and card_present
        headline = "Safe to remove" if card else "Copied and verified"
        sub = f"{plural(batch.ready)} copied. Process and auto-tune them?"
        return PanelView("ready", headline, sub, step, None, **common, card=card)
    elif batch.state == "attention":
        step = 1 if batch.processed < count else 2

    if batch.is_card and card_present:
        return PanelView("safe", "Safe to remove", copied_text(batch), step, progress, **common)
    if batch.state in ("safe", "processing"):
        sub = f"Copy done · {batch.processed} of {plural(count)} processed"
        return PanelView("working", "Processing", sub, step, progress, **common)
    if batch.state == "uploading":
        sub = upload_line(batch)
        if batch.error:
            sub = f"{sub} · {batch.error}"
        return PanelView("working", "Uploading", sub, step, progress, **common)
    if batch.state == "held":
        service = service_name(batch.destination)
        sub = f"{plural(batch.held)} tuned. Upload them to {service}?"
        return PanelView("held", "Ready to upload", sub, step, None, **common)
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


FILLED = ("pause",)  # glyphs drawn solid, as in the design
HERO_ICON = 52
PHOTO_SHARE = 0.68  # of the hero's width, from the right edge
FADE_SHARE = 0.45  # of the photo's width that fades into the background


class HeroArea(QWidget):
    """The space behind the headline: the photo on the right, fading out to the left."""

    def __init__(self) -> None:
        super().__init__()
        self.photo: QImage | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        theme.changed.connect(self.update)

    def set_photo(self, photo: QImage | None) -> None:
        self.photo = None if photo is None or photo.isNull() else photo
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt naming
        if self.photo is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        area = QRectF(
            self.width() * (1 - PHOTO_SHARE), 0, self.width() * PHOTO_SHARE, self.height()
        )
        if area.width() < 8 or area.height() < 8:
            return
        # Cover the area, cropping the photo's centre.
        image = self.photo
        scale = max(area.width() / image.width(), area.height() / image.height())
        crop_w, crop_h = area.width() / scale, area.height() / scale
        source = QRectF((image.width() - crop_w) / 2, (image.height() - crop_h) / 2, crop_w, crop_h)
        painter.drawImage(area, image, source)
        # Fade into the window colour, so the words on the left stay easy to read.
        ground = theme.color("bg")
        clear = QColor(ground)
        clear.setAlpha(0)
        fade = QLinearGradient(area.left(), 0, area.left() + area.width() * FADE_SHARE, 0)
        fade.setColorAt(0.0, ground)
        fade.setColorAt(1.0, clear)
        painter.fillRect(area, fade)
        soft = QColor(ground)
        soft.setAlphaF(0.18)  # calm the photo a little
        painter.fillRect(area, soft)


class StatusPanel(QFrame):
    """Shows one ``PanelView``. Buttons appear only in the states they apply to."""

    eject_clicked = Signal()
    erase_clicked = Signal()  # second click of "Erase card" → "Really erase?"
    release_clicked = Signal()
    process_clicked = Signal()
    retry_clicked = Signal()
    details_clicked = Signal()
    open_folder_clicked = Signal()
    resume_clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("StatusPanel")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(280)
        self.view = PanelView()
        self._erase_batch: int | None = None
        self._fade: QLabel | None = None
        self._fade_anim: QPropertyAnimation | None = None

        self.hero = HeroArea()
        self.icon = Icon("card-sim", "text-2", HERO_ICON, stroke=4.0)
        self.headline = label("", "hero", wrap=True)
        self.subline = label("", "hero-sub", wrap=True)
        self.stages = StageStrip()

        self.eject = Button("Eject", "good", icon="eject", icon_token="good-strong", size="large")
        self.erase = Button(
            "Erase card", "quiet", icon="trash-2", icon_token="text-2", size="large"
        )
        self._erase_armed = QTimer(self)
        self._erase_armed.setSingleShot(True)
        self._erase_armed.timeout.connect(self._disarm_erase)
        self.open_folder = Button("Open folder", "outline", size="large")
        self.release = Button(
            "Upload", "primary", icon="cloud-upload", icon_token="on-accent", size="large"
        )
        self.process = Button("Process", "primary", size="large")
        self.retry = Button("Retry", "primary", size="large")
        self.details = Button("Details", "outline", size="large")
        self.resume = Button(
            "Resume", "primary", icon="play", icon_token="on-accent", size="large", filled=True
        )
        for button, tip in (
            (self.eject, "Eject the card so you can take it out."),
            (self.erase, "Delete the copied photos from the card. Other files stay."),
            (self.open_folder, "Show this event's photos on your computer."),
            (self.release, "Send the waiting photos now."),
            (self.process, "Auto-tune the copied photos now."),
            (self.retry, "Try the photos that failed again."),
            (self.details, "See what happened, photo by photo."),
            (self.resume, "Start watching for new cards again."),
        ):
            button.setToolTip(tip)
        self.eject.clicked.connect(self.eject_clicked)
        self.erase.clicked.connect(self._erase)
        self.open_folder.clicked.connect(self.open_folder_clicked)
        self.release.clicked.connect(self.release_clicked)
        self.process.clicked.connect(self.process_clicked)
        self.retry.clicked.connect(self.retry_clicked)
        self.details.clicked.connect(self.details_clicked)
        self.resume.clicked.connect(self.resume_clicked)
        self._buttons = {
            "paused": (self.resume,),
            "safe": (self.eject, self.erase),
            "ready": (self.process, self.eject, self.erase),
            "held": (self.release, self.open_folder),
            "attention": (self.retry, self.details),
            "copy_error": (self.retry,),
        }

        self._button_box = QWidget()
        button_row = QHBoxLayout(self._button_box)
        button_row.setContentsMargins(0, 32, 0, 0)
        button_row.setSpacing(12)
        self._all_buttons = (
            self.process,
            self.eject,
            self.erase,
            self.release,
            self.retry,
            self.resume,
            self.open_folder,
            self.details,
        )
        for button in self._all_buttons:
            button_row.addWidget(button)
        button_row.addStretch(1)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(24)
        head.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.headline, 1)
        self._words = QWidget()
        words = QVBoxLayout(self._words)
        words.setContentsMargins(40, 0, 0, 0)
        words.setSpacing(4)
        words.addStretch(1)
        words.addLayout(head)
        words.addWidget(self.subline)
        words.addWidget(self._button_box)
        words.addStretch(1)
        hero_row = QHBoxLayout(self.hero)
        hero_row.setContentsMargins(0, 0, 0, 0)
        hero_row.addWidget(self._words)
        hero_row.addStretch(1)

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.hero, 1)
        column.addWidget(separator())
        column.addSpacing(8)
        column.addWidget(self.stages)
        self._apply(self.view)

    @property
    def state(self) -> str:
        return self.view.state

    def set_photo(self, photo: QImage | None) -> None:
        """The newest photo of the event, already black and white (or None)."""
        self.hero.set_photo(photo)

    def visible_buttons(self) -> list[str]:
        return [b.text() for b in self._all_buttons if not b.isHidden()]

    def show_view(self, view: PanelView) -> None:
        if view == self.view:
            return
        if view.state != self.view.state:
            self._crossfade()  # the stages draw their own motion; only states crossfade
        self._apply(view)

    def _apply(self, view: PanelView) -> None:
        self.view = view
        tone, icon, token = LOOKS[view.state]
        restyle(self, tone=tone)
        self.icon.set_icon(icon, token, filled=icon in FILLED)
        self.headline.setText(view.headline)
        self.subline.setText(view.subline)
        self.subline.setVisible(bool(view.subline))
        self.stages.set_views(view.stages)
        shown = self._buttons.get(view.state, ())
        if view.state == "ready" and not view.card:
            shown = (self.process,)
        if view.batch_id != self._erase_batch:
            self._disarm_erase()
            self._erase_batch = view.batch_id
        for button in self._all_buttons:
            button.setVisible(button in shown)
        self._button_box.setVisible(bool(shown))
        self.setAccessibleName(f"{view.headline}. {view.subline}")

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        # The words take the left part of the hero; the headline grows with the window.
        self._words.setMaximumWidth(max(320, int(self.width() * 0.7)))
        px = max(34, min(60, self.width() // 18))
        self.headline.setStyleSheet(f"font-size: {px}px;")

    def set_ejecting(self, busy: bool) -> None:
        self.eject.setEnabled(not busy)
        self.eject.setText("Ejecting…" if busy else "Eject")
        self.erase.setEnabled(not busy)

    def _erase(self) -> None:
        if not self._erase_armed.isActive():
            self.erase.setText("Really erase?")
            self._erase_armed.start(4000)
            return
        self._erase_armed.stop()
        self.erase_clicked.emit()

    def _disarm_erase(self) -> None:
        self._erase_armed.stop()
        if self.erase.isEnabled():
            self.erase.setText("Erase card")

    def set_erasing(self, busy: bool) -> None:
        self._erase_armed.stop()
        self.erase.setEnabled(not busy)
        self.erase.setText("Erasing…" if busy else "Erase card")
        self.eject.setEnabled(not busy)

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
