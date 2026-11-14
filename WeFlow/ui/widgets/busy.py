"""The "Flowing…" cover: greys a window and loops the logo while a slow button's job runs.

``begin(widget)`` and ``end(widget)`` are counted per window, so overlapping jobs keep
the cover up until the last one finishes. It appears only after ``SHOW_AFTER_MS``,
so a quick answer never flickers.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QEvent, QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QImage, QKeyEvent, QPainter, QPainterPath, QPaintEvent
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ..theme import theme
from .common import label, motion_enabled

log = logging.getLogger(__name__)

SHOW_AFTER_MS = 300
TEXT = "Flowing…"
SCRIM_ALPHA = 0.82  # how much of the window's background covers the page
LOGO_WIDTH, LOGO_HEIGHT = 200, 260  # logical size; the artwork is drawn at 2x
LOGO_RADIUS = 16
LOADER_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "loader"


class _Frames:
    """The loading loop's frames, decoded once on a worker thread (never the UI thread)."""

    def __init__(self) -> None:
        self.frames: list[QImage] = []
        self.still: QImage | None = None
        self.frame_ms = 40
        self._lock = threading.Lock()
        self._started = False

    def preload(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        threading.Thread(target=self._load, name="weflow-loader", daemon=True).start()

    def _load(self) -> None:
        try:
            manifest = json.loads((LOADER_DIR / "manifest.json").read_text(encoding="utf-8"))
            still = QImage(str(LOADER_DIR / "still.png"))
            frames = [QImage(str(LOADER_DIR / name)) for name in manifest["frames"]]
            if any(image.isNull() for image in [still, *frames]):
                raise OSError("a loader frame could not be decoded")
        except (OSError, ValueError, KeyError) as error:
            log.warning("Loading animation unavailable: %s", error)
            return
        self.frame_ms = max(1, round(manifest["duration_ms"] / len(frames)))
        self.still = still
        self.frames = frames  # assigned last: a non-empty list means everything is ready


frames = _Frames()


def preload() -> None:
    """Start decoding the loading loop in the background, so it is ready when needed."""
    frames.preload()


class FlowLogo(QWidget):
    """The We Run Huntsville logo looping: busy, with no known end."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(LOGO_WIDTH, LOGO_HEIGHT)
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        frames.preload()

    def start(self) -> None:
        self._clock.start()
        if motion_enabled():
            self._timer.start(frames.frame_ms)
        self.update()

    def stop(self) -> None:
        self._timer.stop()

    def fit(self, height: int) -> None:
        """Shrink to fit a short window, keeping the artwork's shape."""
        scale = max(0.3, min(1.0, height / LOGO_HEIGHT))
        self.setFixedSize(round(LOGO_WIDTH * scale), round(LOGO_HEIGHT * scale))

    def current(self) -> QImage | None:
        loop = frames.frames
        if not loop:
            return None
        if not motion_enabled() or not self._clock.isValid():
            return frames.still
        return loop[(self._clock.elapsed() // frames.frame_ms) % len(loop)]

    def paintEvent(self, event: QPaintEvent) -> None:
        image = self.current()
        if image is None:
            return  # still decoding: the word "Flowing…" carries the message meanwhile
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        shape = QPainterPath()
        shape.addRoundedRect(QRectF(self.rect()), LOGO_RADIUS, LOGO_RADIUS)
        painter.setClipPath(shape)  # the artwork's own white panel, with rounded corners
        painter.drawImage(QRectF(self.rect()), image)


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
        self.logo = FlowLogo()
        column = QVBoxLayout(self)
        column.setSpacing(12)
        column.addStretch(1)
        column.addWidget(self.logo, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(self.text, 0, Qt.AlignmentFlag.AlignHCenter)
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
            self.logo.stop()
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
        self._cover(parent)
        self.raise_()
        self.show()
        self.setFocus()
        self.logo.start()

    def _cover(self, window: QWidget) -> None:
        self.setGeometry(window.rect())
        self.logo.fit(window.height() - self.text.sizeHint().height() - 64)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._cover(self.parentWidget())  # type: ignore[arg-type]
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
