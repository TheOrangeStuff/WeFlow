"""Watch a folder for photos a camera sends over the network (FR-48, 1.21.0).

Canon Image Transfer Utility, or the camera's FTP transfer, saves each shot into a
folder on this computer. This watcher reports each JPEG once it has finished
arriving, and the pipeline imports it like a folder import (FR-18). Files here are
only ever opened ``"rb"``; nothing in the folder is moved, renamed or deleted.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from . import ingest
from .events import Emit, FolderPhotos

log = logging.getLogger(__name__)

POLL_SECONDS = 2.0
SETTLE_SECONDS = 2.0  # a file must be this old and unchanged for one poll
STALLED_SECONDS = 30.0  # unchanged this long and still incomplete: not arriving any more
BURST_LIMIT = 50  # report at once when this many are waiting
JPEG_END = b"\xff\xd9"

Stamp = tuple[int, int]  # (size, mtime_ns)


def _stamp(path: Path) -> Stamp | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


def _complete(path: Path) -> bool:
    """A whole JPEG ends with the end-of-image marker; a half-sent one does not."""
    try:
        with open(path, "rb") as fh:
            fh.seek(-2, os.SEEK_END)
            return fh.read(2) == JPEG_END
    except OSError:
        return False


class FolderWatcher:
    """Polls one folder and emits ``FolderPhotos`` with the photos that have settled.

    Photos that settle while others are still arriving wait, so a burst becomes one
    batch (at most ``BURST_LIMIT``); a single shot goes on the poll it settles."""

    def __init__(self, emit: Emit, interval: float = POLL_SECONDS) -> None:
        self._emit = emit
        self._interval = interval
        self._folder: Path | None = None
        self._paused = False
        self._last: dict[Path, Stamp] = {}  # what each file looked like last poll
        self._seen: dict[Path, Stamp] = {}  # already reported
        self._waiting: list[Path] = []  # settled, not reported yet
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def folder(self) -> Path | None:
        return self._folder

    def set_folder(self, folder: Path | None) -> None:
        with self._lock:
            if folder != self._folder:
                self._folder = folder
                self._reset()

    def set_paused(self, paused: bool) -> None:
        """While paused nothing is reported; photos that came meanwhile come on resume."""
        with self._lock:
            self._paused = paused
            if paused:
                self._reset()

    def _reset(self) -> None:
        self._last.clear()
        self._seen.clear()
        self._waiting.clear()

    def poll(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            folder = self._folder
            if self._paused or folder is None:
                return
            try:
                files = ingest.list_jpegs(folder) if folder.is_dir() else []
            except OSError:
                log.info("The watched folder could not be read")
                return
            current: dict[Path, Stamp] = {}
            arriving = False  # a file still on its way: hold the others to make one batch
            for path in files:
                stamp = _stamp(path)
                if stamp is None:
                    continue
                current[path] = stamp
                if self._seen.get(path) == stamp or path in self._waiting:
                    continue
                old_enough = now - stamp[1] / 1e9 >= SETTLE_SECONDS
                if self._last.get(path) == stamp and old_enough and _complete(path):
                    self._seen[path] = stamp
                    self._waiting.append(path)
                elif now - stamp[1] / 1e9 < STALLED_SECONDS:
                    arriving = True  # a broken file that never finishes holds nothing up
            self._last = current
            ready: list[Path] = []
            if self._waiting and (not arriving or len(self._waiting) >= BURST_LIMIT):
                ready, self._waiting = self._waiting, []
        if ready:
            log.info("%d new photo(s) in the watched folder", len(ready))
            self._emit(FolderPhotos(str(folder), tuple(str(p) for p in ready)))

    def start(self) -> None:
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="folder-watcher", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll()
            except Exception:  # a bad file must not stop the watcher for good
                log.exception("Watching the folder failed")
            self._stop.wait(self._interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
