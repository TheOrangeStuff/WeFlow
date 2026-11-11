"""The only door between the UI and the core.

Core events arrive on worker threads and leave here as Qt signals, which Qt
delivers to the UI thread (queued). Everything slow the UI asks for (file I/O,
keychain, network, image work) is run on worker threads from here.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage

from WeFlow.core import autostart, cards, imaging, secrets, uploaders
from WeFlow.core.config import (
    DESTINATIONS,
    FLICKR_PRIVACY,
    WATERMARK_POSITIONS,
    Profile,
    Settings,
    Store,
    app_data_dir,
    default_library,
)
from WeFlow.core.events import BatchUpdated, CardDetected, CardRemoved, Event, Notice
from WeFlow.core.logs import setup_logging
from WeFlow.core.pipeline import ACTIVE_STATES, Pipeline, plural
from WeFlow.core.state import Batch
from WeFlow.core.uploaders import PendingAuth, Uploader, UploadError, service_name

__all__ = [
    "ACTIVE_STATES",
    "DESTINATIONS",
    "FLICKR_PRIVACY",
    "SERVICES",
    "WATERMARK_POSITIONS",
    "Batch",
    "Bridge",
    "PendingAuth",
    "Profile",
    "Settings",
    "Store",
    "app_data_dir",
    "default_library",
    "plural",
    "service_name",
    "setup_logging",
]

log = logging.getLogger("WeFlow.ui")
SERVICES = ("smugmug", "flickr")
PREVIEW_EDGE = 900
_BEGIN = {"smugmug": uploaders.smugmug_begin, "flickr": uploaders.flickr_begin}
_FINISH = {"smugmug": uploaders.smugmug_finish, "flickr": uploaders.flickr_finish}


def _to_qimage(img: Any) -> QImage:
    rgb = img.convert("RGB")
    data = rgb.tobytes()
    return QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888).copy()


class Bridge(QObject):
    batch_updated = Signal(object)  # Batch
    card_detected = Signal(str, str)  # path, label
    card_removed = Signal(str)  # path
    notice = Signal(str, str, object)  # level, message, batch id or None
    events_changed = Signal()
    settings_changed = Signal()
    services_changed = Signal()
    eject_finished = Signal(str, bool, str)  # path, ok, message
    preview_ready = Signal(int, object, object, str)  # token, before, after, error
    _deliver = Signal(object, object)

    def __init__(
        self,
        store: Store,
        uploader_factory: Callable[[str], Uploader] | None = None,
        watch_interval: float = cards.POLL_SECONDS,
    ) -> None:
        super().__init__()
        self.store = store
        self._io = ThreadPoolExecutor(max_workers=1, thread_name_prefix="weflow-io")
        self._jobs = ThreadPoolExecutor(max_workers=3, thread_name_prefix="weflow-job")
        self._closed = False
        self.store.saver = self._save_later
        self.pipeline = Pipeline(
            store, self._on_core_event, uploader_factory or uploaders.make_uploader
        )
        self.watcher = cards.CardWatcher(self._on_core_event, watch_interval)
        self.watcher.set_paused(store.settings.paused)
        self.connected: dict[str, bool] = dict.fromkeys(SERVICES, False)
        self.builtin_keys = {s: uploaders.has_builtin_keys(s) for s in SERVICES}
        self._sample: tuple[str, Any] | None = None
        self._sample_lock = threading.Lock()
        self._deliver.connect(self._on_deliver, Qt.ConnectionType.QueuedConnection)

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        """Resume unfinished work and begin watching for cards."""
        self._io.submit(self._guard, self.pipeline.resume)
        self.refresh_services()
        self.watcher.start()

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.watcher.stop()
        self.pipeline.stop()
        self._io.shutdown(wait=True)
        self._jobs.shutdown(wait=False, cancel_futures=True)

    def _save_later(self, job: Callable[[], None]) -> None:
        if self._closed:
            job()
        else:
            self._io.submit(self._guard, job)

    @staticmethod
    def _guard(job: Callable[[], Any]) -> None:
        try:
            job()
        except Exception:
            log.exception("background job failed")

    def run(self, job: Callable[[], Any], done: Callable[[Any], None] | None = None) -> None:
        """Run ``job`` off the UI thread; call ``done(result)`` back on the UI thread.

        If the job raises, ``done`` receives the exception object.
        """

        def work() -> None:
            try:
                result = job()
            except Exception as exc:
                if not isinstance(exc, UploadError):
                    log.exception("job failed")
                result = exc
            if done is not None and not self._closed:
                self._deliver.emit(done, result)

        self._jobs.submit(work)

    @staticmethod
    def _on_deliver(done: Callable[[Any], None], result: Any) -> None:
        done(result)

    def _on_core_event(self, event: Event) -> None:
        if self._closed:
            return
        if isinstance(event, BatchUpdated):
            self.batch_updated.emit(event.batch)
        elif isinstance(event, CardDetected):
            self.card_detected.emit(event.path, event.label)
        elif isinstance(event, CardRemoved):
            self.card_removed.emit(event.path)
        elif isinstance(event, Notice):
            self.notice.emit(event.level, event.message, event.batch_id)

    # ---------------------------------------------------------------- events
    @property
    def settings(self) -> Settings:
        return self.store.settings

    def events(self) -> list[Profile]:
        return self.store.events()

    def active(self) -> Profile | None:
        return self.store.active()

    def set_active(self, event_id: str) -> None:
        self.store.set_active(event_id)
        self.events_changed.emit()

    def draft_event(self) -> Profile:
        return self.store.draft_event()

    def other_names(self, event_id: str) -> set[str]:
        return self.store.names(exclude_id=event_id)

    def save_event(self, event: Profile) -> Profile:
        """Add the event if it is new (and make it active), else save the edits."""
        if self.store.get(event.id) is None:
            saved = self.store.add_event(event, activate=True)
        else:
            saved = self.store.update_event(event)
        self.events_changed.emit()
        return saved

    def duplicate_event(self, event_id: str) -> Profile:
        copy = self.store.duplicate_event(event_id)
        self.events_changed.emit()
        return copy

    def delete_event(self, event_id: str) -> bool:
        """Forget an event (its photos stay). Refused while it still has work running."""
        if self.pipeline.has_pending(event_id):
            return False
        self.store.delete_event(event_id)
        self.events_changed.emit()
        return True

    # ------------------------------------------------------------------ work
    def submit(self, source: str, label: str, is_card: bool, event_id: str = "") -> bool:
        """Start importing ``source`` into the active event (or the one given)."""
        event = self.store.get(event_id) if event_id else self.active()
        if event is None:
            return False
        self._io.submit(
            self._guard, lambda: self.pipeline.submit(Path(source), event, label, is_card)
        )
        return True

    def release(self, event_id: str | None) -> None:
        self._io.submit(self._guard, lambda: self.pipeline.release_held(event_id))

    def retry(self, event_id: str | None) -> None:
        self._io.submit(self._guard, lambda: self.pipeline.retry_failed(event_id))

    def snapshot(self) -> list[Batch]:
        return self.pipeline.snapshot()

    def has_pending(self) -> bool:
        return self.pipeline.has_pending()

    def eject(self, path: str) -> None:
        def done(result: Any) -> None:
            ok, message = (
                result if isinstance(result, tuple) else (False, "The card could not be ejected.")
            )
            self.eject_finished.emit(path, ok, message)

        self.run(lambda: cards.eject(Path(path)), done)

    def set_paused(self, paused: bool) -> None:
        self.watcher.set_paused(paused)
        self.store.update_settings(paused=paused)
        self.settings_changed.emit()

    def event_folder(self, event_id: str, processed: bool = True) -> str:
        event = self.store.get(event_id)
        if event is None:
            return str(self.store.library)
        return str(self.store.event_root(event) / ("processed" if processed else "originals"))

    def open_folder(self, path: str) -> None:
        """Show a folder in Finder / Explorer (creating it first if needed)."""

        def reveal() -> str:
            Path(path).mkdir(parents=True, exist_ok=True)
            return path

        self.run(reveal, lambda result: self._open_url(result))

    @staticmethod
    def _open_url(result: Any) -> None:
        if isinstance(result, str):
            QDesktopServices.openUrl(QUrl.fromLocalFile(result))

    # -------------------------------------------------------------- settings
    def update_settings(self, **changes: Any) -> None:
        self.store.update_settings(**changes)
        self.settings_changed.emit()

    def library(self) -> str:
        return str(self.store.library)

    def set_start_at_login(self, enabled: bool) -> None:
        self.update_settings(start_at_login=enabled)
        self.run(lambda: autostart.set_start_at_login(enabled))

    # -------------------------------------------------------------- services
    def refresh_services(self) -> None:
        def check() -> dict[str, bool]:
            return {service: uploaders.is_connected(service) for service in SERVICES}

        def done(result: Any) -> None:
            if isinstance(result, dict):
                self.connected = result
                self.services_changed.emit()

        self.run(check, done)

    def connect_begin(
        self, service: str, api_key: str, api_secret: str, done: Callable[[Any], None]
    ) -> None:
        """Start authorising. ``done`` gets a ``PendingAuth`` or an ``UploadError``."""

        def job() -> PendingAuth:
            try:
                if api_key and api_secret:
                    secrets.set_secret(f"{service}_api_key", api_key)
                    secrets.set_secret(f"{service}_api_secret", api_secret)
                return _BEGIN[service]()
            except secrets.SecretsError as exc:
                raise UploadError(f"{exc} Unlock it and try again.", True) from exc

        self.run(job, done)

    def connect_finish(
        self, service: str, pending: PendingAuth, code: str, done: Callable[[Any], None]
    ) -> None:
        """Finish authorising. ``done`` gets ``True`` or an ``UploadError``."""

        def job() -> bool:
            try:
                _FINISH[service](pending, code)
            except secrets.SecretsError as exc:
                raise UploadError(f"{exc} Unlock it and try again.", True) from exc
            return True

        def finished(result: Any) -> None:
            self.refresh_services()
            done(result)

        self.run(job, finished)

    def disconnect_service(self, service: str) -> None:
        self.run(lambda: uploaders.disconnect(service), lambda _result: self.refresh_services())

    @staticmethod
    def open_url(url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    # --------------------------------------------------------------- preview
    def _load_sample(self, path: str) -> Any:
        with self._sample_lock:
            if self._sample is None or self._sample[0] != path:
                self._sample = (path, imaging.open_upright(Path(path), PREVIEW_EDGE))
            return self._sample[1]

    def render_preview(self, token: int, sample: str, event: Profile) -> None:
        """FR-04: render before/after off the UI thread; answer with ``preview_ready``."""

        def job() -> tuple[QImage, QImage]:
            before = self._load_sample(sample)
            mark = imaging.load_watermark(event.watermark_path) if event.watermark else None
            preview = Profile(**{**event.__dict__, "max_long_edge": 0})
            return _to_qimage(before), _to_qimage(imaging.render(before, preview, mark))

        def done(result: Any) -> None:
            if isinstance(result, tuple):
                self.preview_ready.emit(token, result[0], result[1], "")
            else:
                message = str(result) if isinstance(result, imaging.ProcessError) else ""
                self.preview_ready.emit(token, None, None, message or "The preview failed.")

        self.run(job, done)

    def load_thumbnail(self, path: str, edge: int, done: Callable[[Any], None]) -> None:
        """Load a small image (for the watermark picker). ``done`` gets a QImage or None."""

        def job() -> QImage | None:
            image = QImage(path)
            if image.isNull():
                return None
            return image.scaled(
                edge,
                edge,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

        self.run(job, lambda result: done(result if isinstance(result, QImage) else None))
