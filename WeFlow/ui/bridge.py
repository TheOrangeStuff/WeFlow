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

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage, QImageReader

from WeFlow import __version__
from WeFlow.core import (
    autostart,
    cards,
    imaging,
    naming,
    secrets,
    self_update,
    updates,
    uploaders,
)
from WeFlow.core.config import (
    DESTINATIONS,
    FILE_NAMING,
    FILE_SIZE_KB_MAX,
    FILE_SIZE_KB_MIN,
    FLICKR_PRIVACY,
    WATERMARK_POSITIONS,
    ExportPreset,
    Profile,
    Settings,
    Store,
    app_data_dir,
    default_library,
)
from WeFlow.core.events import BatchUpdated, CardDetected, CardRemoved, Event, Notice
from WeFlow.core.logs import recent_log, setup_logging
from WeFlow.core.pipeline import ACTIVE_STATES, Pipeline, duration, plural, speed
from WeFlow.core.state import Batch
from WeFlow.core.updates import Notes, Update
from WeFlow.core.uploaders import (
    AlbumChoice,
    PendingAuth,
    Uploader,
    UploadError,
    service_name,
)

__all__ = [
    "ACTIVE_STATES",
    "AlbumChoice",
    "DESTINATIONS",
    "FILE_NAMING",
    "FILE_SIZE_KB_MAX",
    "FILE_SIZE_KB_MIN",
    "FLICKR_PRIVACY",
    "SERVICES",
    "WATERMARK_POSITIONS",
    "Batch",
    "Bridge",
    "ExportPreset",
    "Notes",
    "PendingAuth",
    "Profile",
    "Settings",
    "Store",
    "Update",
    "UploadError",
    "app_data_dir",
    "default_library",
    "duration",
    "plural",
    "service_name",
    "setup_logging",
    "speed",
]

log = logging.getLogger("WeFlow.ui")
SERVICES = ("smugmug", "flickr", "dropbox")
PREVIEW_EDGE = 900
_BEGIN = {
    "smugmug": uploaders.smugmug_begin,
    "flickr": uploaders.flickr_begin,
    "dropbox": uploaders.dropbox_begin,
}
_FINISH = {
    "smugmug": uploaders.smugmug_finish,
    "flickr": uploaders.flickr_finish,
    "dropbox": uploaders.dropbox_finish,
}


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
    update_changed = Signal()
    # In-app update (FR-44): (state, percent); state is downloading, waiting, installing or failed
    update_install_changed = Signal(str, int)
    quit_for_update = Signal()  # the installer is running: quit so it can replace WeFlow
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
        self._uploader_factory = uploader_factory or uploaders.make_uploader
        self.pipeline = Pipeline(store, self._on_core_event, self._uploader_factory)
        self.watcher = cards.CardWatcher(self._on_core_event, watch_interval)
        self.watcher.set_paused(store.settings.paused)
        self.pipeline.set_paced(store.settings.paced)
        self.pipeline.set_speed(store.settings.speed)
        self.connected: dict[str, bool] = dict.fromkeys(SERVICES, False)
        self.has_keys: dict[str, bool] = dict.fromkeys(SERVICES, False)
        self._albums: dict[str, list[AlbumChoice]] = {}  # galleries already fetched, per service
        self._album_waiters: dict[str, list[Callable[[Any], None]]] = {}  # fetches in flight
        self._sample: tuple[str, Any] | None = None
        self._sample_lock = threading.Lock()
        self.update: Update | None = None  # a newer published version, once known
        self.can_self_update = self_update.supported()
        self.installing_update = False
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(updates.CHECK_EVERY_HOURS * 3_600_000)
        self._update_timer.timeout.connect(self.check_for_updates)
        self._deliver.connect(self._on_deliver, Qt.ConnectionType.QueuedConnection)

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        """Resume unfinished work and begin watching for cards."""
        self._queue(self.pipeline.resume)
        self.refresh_services()
        self.watcher.start()

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._update_timer.stop()
        self.watcher.stop()
        self.pipeline.stop()
        self._io.shutdown(wait=True)
        self._jobs.shutdown(wait=False, cancel_futures=True)

    def _save_later(self, job: Callable[[], None]) -> None:
        if self._closed:
            job()
        else:
            self._io.submit(self._guard, job)

    def _queue(self, job: Callable[[], Any]) -> None:
        """Run ``job`` on the I/O thread. After shutdown, late requests (a card's
        countdown, a button press while quitting) are dropped."""
        if not self._closed:
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
                if isinstance(exc, UploadError):
                    log.info("job stopped: %s", exc.message)
                else:
                    log.exception("job failed")
                result = exc
            if done is not None and not self._closed:
                self._deliver.emit(done, result)

        if not self._closed:
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
        before = self.store.get(event.id)
        if before is None:
            saved = self.store.add_event(event, activate=True)
        else:
            saved = self.store.update_event(event)
            if saved.makes_large and not before.makes_large:
                # FR-42: just turned on, so make full-size copies of photos already up.
                self._queue(lambda: self.pipeline.make_large(saved.id))
        self.events_changed.emit()
        return saved

    # --------------------------------------------------------------- presets
    def presets(self) -> list[ExportPreset]:
        return self.store.presets()

    def save_preset(self, name: str, event: Profile) -> ExportPreset:
        return self.store.save_preset(name, event)

    def delete_preset(self, preset_id: str) -> None:
        self.store.delete_preset(preset_id)

    @staticmethod
    def example_file_name(event: Profile) -> str:
        """What the event's first processed photo would be called (no I/O)."""
        return naming.example(event)

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
        self._queue(lambda: self.pipeline.submit(Path(source), event, label, is_card))
        return True

    def release(self, event_id: str | None) -> None:
        self._queue(lambda: self.pipeline.release_held(event_id))

    def process(self, event_id: str | None) -> None:
        """Paced mode: go ahead and process the photos waiting after copying."""
        self._queue(lambda: self.pipeline.process_ready(event_id))

    def set_paced(self, paced: bool) -> None:
        self.update_settings(paced=paced)
        self._queue(lambda: self.pipeline.set_paced(paced))

    def set_speed(self, level: str) -> None:
        self.update_settings(speed=level)
        self._queue(lambda: self.pipeline.set_speed(level))

    def retry(self, event_id: str | None) -> None:
        self._queue(lambda: self.pipeline.retry_failed(event_id))

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
        # Uploaded copies are deleted unless the event keeps them (1.5.0).
        keeps = event.destination == "none" or event.keep_processed
        folder = "processed" if processed and keeps else event.originals_dir
        return str(self.store.event_root(event) / folder)

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

    # --------------------------------------------------------------- updates
    def watch_updates(self) -> None:
        """Look for a newer version now, then once a day while the app stays open."""
        self._update_timer.start()
        self.check_for_updates()

    def check_for_updates(self) -> None:
        if not self.settings.check_updates:
            return

        def done(result: Any) -> None:
            # A failed check says nothing, so it never withdraws a known update.
            if isinstance(result, Update) and self.settings.check_updates:
                if result != self.update:
                    self.update = result
                    self.update_changed.emit()

        self.run(lambda: updates.check(__version__), done)

    def check_for_updates_now(self, done: Callable[[str], None]) -> None:
        """ "Check for updates" in Settings. Works even with the daily check off.

        ``done`` gets "update" (and ``self.update`` is set), "current" or "failed".
        """

        def finished(result: Any) -> None:
            if isinstance(result, Update):
                if result != self.update:
                    self.update = result
                    self.update_changed.emit()
                done("update")
            elif result is None:
                done("current")
            else:
                log.info("update check failed: %s", type(result).__name__)
                done("failed")

        def job() -> Update | Exception | None:
            try:
                return updates.latest(__version__)
            except (OSError, ValueError) as exc:
                return exc  # a failed check is an answer here, not a crash

        self.run(job, finished)

    def set_check_updates(self, enabled: bool) -> None:
        self.update_settings(check_updates=enabled)
        if enabled:
            self.check_for_updates()
        elif self.update is not None:
            self.update = None
            self.update_changed.emit()

    def finish_first_run(self) -> None:
        """A new install has nothing "new" to announce, so this version counts as seen."""
        self.update_settings(first_run_done=True, seen_version=__version__)

    def whats_new(self, done: Callable[[str, Notes], None]) -> None:
        """Once per version: hand ``done`` the changes in every version since the last seen."""
        seen = self.settings.seen_version
        if seen == __version__:
            return
        self.update_settings(seen_version=__version__)
        if seen and not updates.is_newer(__version__, seen):
            return  # an older build was installed over a newer one

        def deliver(result: Any) -> None:
            if isinstance(result, list) and result:
                done(__version__, result)

        self.run(lambda: updates.whats_new_since(seen, __version__), deliver)

    def release_notes(self, done: Callable[[Notes], None]) -> None:
        """Every version's changes, newest first, for Settings › What's new."""

        def deliver(result: Any) -> None:
            if isinstance(result, list):
                done(result)

        self.run(updates.release_notes, deliver)

    def skip_update(self) -> None:
        """Stop offering this version in the main window. Settings still lists it."""
        if self.update is not None:
            self.update_settings(skipped_update=self.update.version)

    def open_update_page(self) -> None:
        if self.update is not None:
            self.open_url(self.update.url)

    def get_update(self) -> None:
        """ "Update now" where WeFlow can update itself, else open the download page."""
        if self.can_self_update:
            self.install_update()
        else:
            self.open_update_page()

    def install_update(self) -> None:
        """Download and check the new installer, then run it once nothing is working."""
        update = self.update
        if update is None or self.installing_update:
            return
        self.installing_update = True
        self.update_install_changed.emit("downloading", 0)
        shown = [0]

        def report(percent: int) -> None:
            if self.installing_update:
                self.update_install_changed.emit("downloading", percent)

        def progress(done: int, total: int) -> None:  # on the job thread
            percent = min(99, done * 100 // total) if total else 0
            if percent != shown[0] and not self._closed:
                shown[0] = percent
                self._deliver.emit(report, percent)

        folder = self.store.data_dir / "updates"
        self.run(
            lambda: self_update.download(update.version, folder, progress),
            self._update_downloaded,
        )

    def _update_downloaded(self, result: Any) -> None:
        if not isinstance(result, Path):
            self._update_failed(result)
            return
        self._launch_when_idle(result)

    def _launch_when_idle(self, installer: Path) -> None:
        if self._closed:
            return
        if self.has_pending():  # never restart in the middle of a card or an upload
            self.update_install_changed.emit("waiting", 100)
            QTimer.singleShot(2000, self, lambda: self._launch_when_idle(installer))
            return
        self.update_install_changed.emit("installing", 100)

        def launched(result: Any) -> None:
            if isinstance(result, Exception):
                self._update_failed(result)
            else:
                self.quit_for_update.emit()

        self.run(lambda: self_update.launch(installer), launched)

    def _update_failed(self, error: Any) -> None:
        log.info("update not installed: %s", type(error).__name__)
        self.installing_update = False
        self.update_install_changed.emit("failed", 0)

    # -------------------------------------------------------------- services
    def refresh_services(self) -> None:
        def check() -> dict[str, tuple[bool, bool]]:
            return {s: (uploaders.is_connected(s), uploaders.has_api_keys(s)) for s in SERVICES}

        def done(result: Any) -> None:
            if isinstance(result, dict):
                self.connected = {s: found[0] for s, found in result.items()}
                self.has_keys = {s: found[1] for s, found in result.items()}
                self.services_changed.emit()
                self.prefetch_albums()

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
            self._albums.pop(service, None)  # maybe a different account now
            self.refresh_services()
            done(result)

        self.run(job, finished)

    def prefetch_albums(self) -> None:
        """FR-39: fetch the galleries of every signed-in service in the background,
        so the gallery chooser is ready the moment it opens."""
        for service, connected in self.connected.items():
            if connected and service not in self._albums:
                self.list_albums(service, lambda _result: None)

    def list_albums(
        self, destination: str, done: Callable[[Any], None], refresh: bool = False
    ) -> None:
        """FR-39: the user's galleries. ``done`` gets a list of ``AlbumChoice`` or an
        ``UploadError``. A list fetched earlier is reused unless ``refresh`` is set,
        and callers asking while a fetch is running share its answer."""
        if not refresh and destination in self._albums:
            done(list(self._albums[destination]))
            return
        waiters = self._album_waiters.get(destination)
        if waiters is not None:
            waiters.append(done)
            return
        self._album_waiters[destination] = [done]

        def job() -> list[AlbumChoice]:
            try:
                return self._uploader_factory(destination).list_albums()
            except secrets.SecretsError as exc:
                raise UploadError(f"{exc} Unlock it and try again.", True) from exc

        def finished(result: Any) -> None:
            if isinstance(result, list):
                self._albums[destination] = result
            for waiter in self._album_waiters.pop(destination, []):
                waiter(list(result) if isinstance(result, list) else result)

        self.run(job, finished)

    def create_album(self, destination: str, name: str, done: Callable[[Any], None]) -> None:
        """FR-41: make a new gallery or album. ``done`` gets the ``AlbumChoice``
        or an ``UploadError``."""

        def job() -> AlbumChoice:
            try:
                return self._uploader_factory(destination).create_album(name)
            except secrets.SecretsError as exc:
                raise UploadError(f"{exc} Unlock it and try again.", True) from exc

        def finished(result: Any) -> None:
            if isinstance(result, AlbumChoice) and destination in self._albums:
                self._albums[destination].insert(0, result)
            done(result)

        self.run(job, finished)

    @staticmethod
    def match_album(choices: list[AlbumChoice], album: str) -> AlbumChoice | None:
        return uploaders.match_album(choices, album)

    def read_log(self, done: Callable[[Any], None]) -> None:
        """The newest part of the log, for "Copy log". ``done`` gets the text."""
        self.run(lambda: recent_log(self.store.data_dir), done)

    def open_api_key_page(self, service: str) -> None:
        self.open_url(uploaders.API_KEY_PAGES[service])

    def disconnect_service(self, service: str, done: Callable[[Any], None] | None = None) -> None:
        self._albums.pop(service, None)

        def finished(result: Any) -> None:
            self.refresh_services()
            if done is not None:
                done(result)

        self.run(lambda: uploaders.disconnect(service), finished)

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
            return _to_qimage(before), _to_qimage(imaging.render(before, event, mark))

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

    def latest_photo(self, event_id: str, edge: int, done: Callable[[Any], None]) -> None:
        """The event's newest photo, upright, at most ``edge`` px and in black and white,
        for the main window's hero (1.14.0). ``done`` gets a QImage or None."""
        event = self.store.get(event_id)
        if event is None:
            done(None)
            return
        root = self.store.event_root(event)

        def job() -> QImage | None:
            newest: tuple[float, Path] | None = None
            for folder in (root / "processed", root / event.originals_dir):
                if not folder.is_dir():
                    continue
                for path in folder.rglob("*"):
                    if path.suffix.lower() not in (".jpg", ".jpeg"):
                        continue
                    try:
                        stamp = path.stat().st_mtime
                    except OSError:
                        continue
                    if newest is None or stamp > newest[0]:
                        newest = (stamp, path)
                if newest is not None:
                    break  # a processed photo is the better picture
            if newest is None:
                return None
            reader = QImageReader(str(newest[1]))
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid() and max(size.width(), size.height()) > edge:
                size.scale(edge, edge, Qt.AspectRatioMode.KeepAspectRatio)
                reader.setScaledSize(size)
            image = reader.read()
            if image.isNull():
                return None
            return image.convertToFormat(QImage.Format.Format_Grayscale8)

        self.run(job, lambda result: done(result if isinstance(result, QImage) else None))
