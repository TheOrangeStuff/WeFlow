"""The engine: copy → verify → process → upload, with saved state and retries.

Threads: one copier, processing and upload workers (the speed level sets how many
run at once; at MAX they start on each photo while the card is still copying) and a
flusher that recounts changed batches and emits ``BatchUpdated``
at most 10 times a second. Processing workers also make the full-size SmugMug copies
of FR-42, after any photo still waiting to be processed.
"""

from __future__ import annotations

import contextlib
import heapq
import itertools
import logging
import os
import queue
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

from . import erase, imaging, ingest, naming
from .config import LARGE_FOLDER, Profile, Store
from .events import BatchUpdated, Emit, Event, Notice
from .state import Batch, EventState, PhotoRecord
from .uploaders import Uploader, UploadError, make_uploader, service_name

log = logging.getLogger("WeFlow.pipeline")

RETRY_DELAYS = (10.0, 20.0, 40.0, 60.0, 120.0)
RETRY_STEADY = 300.0
MAX_ATTEMPTS = 12
FLUSH_SECONDS = 0.1
PIXEL_BUDGET = 120_000_000  # decoded pixels in flight at once (NFR-08)
BYTES_PER_PIXEL = 9  # the most a worker holds: decoded, turned upright and adjusted
MAX_MEMORY_SHARE = 1 / 3  # of the computer's memory, for decoded photos at MAX
ACTIVE_STATES = ("copying", "safe", "processing", "uploading")
INTERRUPTED = "The copy was interrupted. Re-insert the card to try again."

Task = tuple[str, str]  # (event id, photo name)
# Processing queue items: (priority, order, stage, event id, photo name). Stage "process"
# goes before "large" (FR-42); a negative priority tells a worker to stop.
Job = tuple[int, int, str, str, str]


def retry_delay(attempts: int) -> float:
    """Seconds to wait after the ``attempts``-th failed try (FR-33)."""
    return RETRY_DELAYS[attempts - 1] if attempts <= len(RETRY_DELAYS) else RETRY_STEADY


def default_process_workers() -> int:
    return max(1, min(4, (os.cpu_count() or 2) // 2))


SPEED_LEVELS = ("low", "medium", "high")
# 1.17.0: at MAX, UploadTuner raises SmugMug uploads at once from 4 up to this.
MAX_UPLOADS = 12
# Never more uploads at once to these services, at any speed (Flickr's API call limit).
DESTINATION_LIMITS = {"flickr": 4}
GATE_WAIT = 0.25  # seconds a photo waits when its service's limit is full
TUNE_WINDOW = 15.0  # seconds of uploading per measurement
TUNE_HOLD = 60.0  # seconds to stay at a count before trying one more
TUNE_GAIN = 1.10  # one more upload at once must be this much faster to keep


def total_memory() -> int:
    """This computer's memory in bytes, or 0 when it can't be read."""
    try:
        if sys.platform == "win32":
            import ctypes

            class Status(ctypes.Structure):  # MEMORYSTATUSEX
                _fields_ = [  # noqa: RUF012
                    ("length", ctypes.c_ulong),
                    ("load", ctypes.c_ulong),
                    ("total", ctypes.c_ulonglong),
                    ("rest", ctypes.c_ulonglong * 6),
                ]

            status = Status()
            status.length = ctypes.sizeof(Status)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return 0
            return int(status.total)
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, AttributeError):
        return 0


def speed_limits(
    level: str, cpus: int | None = None, memory: int | None = None
) -> tuple[int, int, int]:
    """(processing workers, upload workers, pixel budget) for a speed level.

    Medium is the spec's default (FR-25, NFR-08). Low leaves most of the computer
    free. High (MAX on screen) runs a worker on every core, and lets decoded photos
    fill up to a third of the memory so big files don't leave workers waiting.
    """
    cores = cpus or os.cpu_count() or 2
    if level == "low":
        return max(1, min(2, cores // 4)), 1, PIXEL_BUDGET
    if level == "high":
        ram = total_memory() if memory is None else memory
        budget = max(PIXEL_BUDGET * 2, int(ram * MAX_MEMORY_SHARE) // BYTES_PER_PIXEL)
        return max(2, cores), 4, budget
    return max(1, min(4, cores // 2)), 2, PIXEL_BUDGET


class Slots:
    """An adjustable limit on how many workers run at once."""

    def __init__(self, limit: int) -> None:
        self._limit = max(1, limit)
        self._used = 0
        self._closed = False
        self._cond = threading.Condition()

    @property
    def limit(self) -> int:
        return self._limit

    def set_limit(self, limit: int) -> None:
        with self._cond:
            self._limit = max(1, limit)
            self._cond.notify_all()

    def acquire(self) -> bool:
        """Wait for a free slot. Returns ``False`` once closed."""
        with self._cond:
            while not self._closed and self._used >= self._limit:
                self._cond.wait()
            if self._closed:
                return False
            self._used += 1
            return True

    def release(self) -> None:
        with self._cond:
            self._used -= 1
            self._cond.notify_all()

    def try_acquire(self) -> bool:
        """Take a free slot without waiting. Returns ``False`` when none is free."""
        with self._cond:
            if self._closed or self._used >= self._limit:
                return False
            self._used += 1
            return True

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()


class UploadTuner:
    """Finds how many uploads at once move the most bytes (MAX speed, 1.17.0).

    Each window in which the uploads kept every slot busy is a measurement. While a
    count is at least 10% faster than the best so far, try one more. When it isn't, go
    back to the best count and hold for a while, then try one more again, since the
    connection can change. A "busy" answer from the service halves the count and holds.
    Windows that were short of photos (processing is behind) say nothing about the
    network and are ignored.
    """

    def __init__(
        self,
        start: int,
        ceiling: int,
        window: float = TUNE_WINDOW,
        hold: float = TUNE_HOLD,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ceiling = max(1, ceiling)
        self._limit = max(1, min(start, self._ceiling))
        self._window = window
        self._hold = hold
        self._clock = clock
        self._lock = threading.Lock()
        self._best_limit = self._limit
        self._best_rate: float | None = None
        self._hold_until = 0.0
        self._active = 0
        self._last = clock()
        self._reset(self._last)

    @property
    def limit(self) -> int:
        return self._limit

    def begin(self) -> None:
        """An upload started."""
        with self._lock:
            self._advance()
            self._active += 1

    def end(self, sent: int) -> int | None:
        """An upload ended, having sent ``sent`` bytes (0 if it failed).
        Returns a new limit when it changes."""
        with self._lock:
            now = self._advance()
            self._active = max(0, self._active - 1)
            self._bytes += sent
            self._done += 1 if sent else 0
            return self._judge(now)

    def busy(self) -> int | None:
        """The service said it is busy. Returns a new limit when it changes."""
        with self._lock:
            now = self._advance()
            old = self._limit
            self._limit = max(1, old // 2)
            self._best_limit, self._best_rate = self._limit, None
            self._hold_until = now + self._hold
            self._reset(now)
            return self._limit if self._limit != old else None

    def _advance(self) -> float:
        now = self._clock()
        self._busy_time += self._active * (now - self._last)
        self._last = now
        return now

    def _reset(self, now: float) -> None:
        self._start = now
        self._bytes = 0
        self._done = 0
        self._busy_time = 0.0

    def _judge(self, now: float) -> int | None:
        elapsed = now - self._start
        if elapsed < self._window or (self._done < 2 * self._limit and elapsed < 4 * self._window):
            return None
        full = self._busy_time >= 0.9 * elapsed * self._limit
        rate = self._bytes / elapsed
        self._reset(now)
        if not full:
            return None  # waiting on photos, not the network
        old = self._limit
        if now < self._hold_until:
            if self._limit == self._best_limit:
                self._best_rate = rate
            return None
        if self._best_rate is None or self._limit <= self._best_limit:
            self._best_rate, self._best_limit = rate, self._limit  # (re)measure, then probe
        elif rate > self._best_rate * TUNE_GAIN:
            self._best_rate, self._best_limit = rate, self._limit
        else:  # one more didn't help: back to the best count, and stay a while
            self._limit = self._best_limit
            self._hold_until = now + self._hold
            return self._limit if self._limit != old else None
        if self._limit < self._ceiling:
            self._limit += 1
        else:
            self._hold_until = now + self._hold
        return self._limit if self._limit != old else None


class _DelayQueue:
    """A queue whose items become available after a delay."""

    def __init__(self) -> None:
        self._heap: list[tuple[float, int, Task]] = []
        self._cond = threading.Condition()
        self._count = itertools.count()
        self._closed = False

    def put(self, item: Task, delay: float = 0.0) -> None:
        with self._cond:
            heapq.heappush(self._heap, (time.monotonic() + delay, next(self._count), item))
            self._cond.notify()

    def get(self) -> Task | None:
        """Block until an item is due. Returns ``None`` once closed."""
        with self._cond:
            while not self._closed:
                if self._heap:
                    wait = self._heap[0][0] - time.monotonic()
                    if wait <= 0:
                        return heapq.heappop(self._heap)[2]
                    self._cond.wait(wait)
                else:
                    self._cond.wait()
            return None

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()


class PixelBudget:
    """Caps how many decoded pixels the workers hold at once."""

    def __init__(self, limit: int = PIXEL_BUDGET) -> None:
        self._limit = limit
        self._used = 0
        self._cond = threading.Condition()

    def set_limit(self, limit: int) -> None:
        with self._cond:
            self._limit = limit
            self._cond.notify_all()

    @contextlib.contextmanager
    def reserve(self, pixels: int) -> Iterator[None]:
        pixels = min(pixels, self._limit)
        with self._cond:
            while self._used + pixels > self._limit:
                self._cond.wait()
            self._used += pixels
        try:
            yield
        finally:
            with self._cond:
                self._used -= pixels
                self._cond.notify_all()


class RateMeter:
    """Bytes per second over the last ``window`` seconds."""

    def __init__(self, window: float) -> None:
        self._window = window
        self._start = time.monotonic()
        self._samples: deque[tuple[float, int]] = deque()

    def add(self, count: int) -> float:
        now = time.monotonic()
        self._samples.append((now, count))
        cut = now - self._window
        while self._samples and self._samples[0][0] < cut:
            self._samples.popleft()
        span = now - max(self._start, cut)
        if span < 0.25:
            return 0.0
        return sum(n for _t, n in self._samples) / span


class Pipeline:
    def __init__(
        self,
        store: Store,
        emit: Emit,
        uploader_factory: Callable[[str], Uploader] = make_uploader,
        process_workers: int | None = None,
        upload_workers: int | None = None,
        retry_delay: Callable[[int], float] = retry_delay,
    ) -> None:
        self._store = store
        self._emit_cb = emit
        self._uploader_factory = uploader_factory
        self._retry_delay = retry_delay
        self._lock = threading.RLock()
        self._states: dict[str, EventState] = {}
        self._batches: dict[int, Batch] = {}
        self._uploaders: dict[str, Uploader] = {}
        self._sorted: dict[int, int] = {}  # batch id -> photos up at its last sort
        self._large_told: dict[int, tuple[int, int]] = {}  # batch id -> (done, failed) said
        self._order = itertools.count()
        self._queued: set[tuple[str, str, str]] = set()
        self._dirty: set[int] = set()
        self._waiting: dict[int, str] = {}
        self._upload_meters: dict[int, RateMeter] = {}
        self._last_id = 0
        self._paced = False
        self._copying = False
        self._stopping = threading.Event()
        self._budget = PixelBudget()
        self._copy_q: queue.Queue[int | None] = queue.Queue()
        self._process_q: queue.PriorityQueue[Job] = queue.PriorityQueue()
        self._upload_q = _DelayQueue()
        self._threads: list[threading.Thread] = []
        # Threads for the fastest level start once; Slots decide how many may run.
        # A fixed count (tests) ignores the speed level.
        self._fixed = (process_workers, upload_workers)
        self._speed = "medium"
        most_process, most_upload, _ = speed_limits("high")
        process, upload, _ = speed_limits("medium")
        self._process_slots = Slots(process_workers or process)
        self._upload_slots = Slots(upload_workers or upload)
        self._tuner: UploadTuner | None = None  # only at MAX
        self._gates = {dest: Slots(n) for dest, n in DESTINATION_LIMITS.items()}
        self._spawn("copy", self._copy_loop)
        for n in range(process_workers or max(most_process, process)):
            self._spawn(f"process-{n}", self._process_loop)
        for n in range(upload_workers or max(most_upload, MAX_UPLOADS)):
            self._spawn(f"upload-{n}", self._upload_loop)
        self._spawn("flush", self._flush_loop)

    def _spawn(self, name: str, target: Callable[[], None]) -> None:
        thread = threading.Thread(target=target, name=f"weflow-{name}", daemon=True)
        thread.start()
        self._threads.append(thread)

    # ------------------------------------------------------------ public API
    def submit(self, source: Path, event: Profile, label: str, is_card: bool) -> Batch:
        """Start importing ``source`` into ``event``. Returns at once."""
        state = self._state_for(event)
        with self._lock:
            self._last_id = max(self._last_id + 1, int(time.time() * 1000))
            batch = Batch(
                id=self._last_id,
                event_id=event.id,
                event_name=event.name,
                label=label,
                source=str(source),
                is_card=is_card,
                destination=event.destination,
                started=time.time(),
            )
            self._batches[batch.id] = batch
        state.put_batch(batch)
        self._emit(BatchUpdated(replace(batch)))
        self._copy_q.put(batch.id)
        return replace(batch)

    def erase_card(self, batch_id: int) -> erase.Erased:
        """Erase the card's photos that this event holds verified copies of (FR-47).

        Only for a card batch whose copy finished and verified, and only while no
        other batch is copying from that card. Runs on the caller's thread."""
        with self._lock:
            batch = self._batches.get(batch_id)
            state = self._states.get(batch.event_id) if batch else None
            busy = batch is not None and any(
                b.source == batch.source and b.state == "copying" for b in self._batches.values()
            )
        if batch is None or state is None or not (batch.is_card and batch.verified) or busy:
            return erase.Erased(False, message="Wait until the card is safe to remove.")
        with state.lock:
            copies = {p.sha256: state.originals / p.name for p in state.photos.values()}
        result = erase.erase_copied(Path(batch.source), copies)
        log.info("batch %s erase: %s", batch.id, result)
        return result

    @property
    def paced(self) -> bool:
        return self._paced

    @property
    def speed(self) -> str:
        return self._speed

    def set_speed(self, level: str) -> None:
        """Low, medium or high. Applies as soon as the photos in hand finish."""
        if level not in SPEED_LEVELS:
            level = "medium"
        self._speed = level
        process, upload = self._apply_limits()
        log.info("speed %s: %d processing, %d uploading", level, process, upload)

    def _apply_limits(self) -> tuple[int, int]:
        process, upload, budget = speed_limits(self._speed)
        if self._speed == "high" and self._copying:
            process = max(1, process - 1)  # MAX works during the copy: leave the copier a core
        fixed_process, fixed_upload = self._fixed
        with self._lock:
            if self._speed != "high" or fixed_upload:
                self._tuner = None
            elif self._tuner is None:
                self._tuner = UploadTuner(upload, MAX_UPLOADS)
            if self._tuner is not None:
                upload = self._tuner.limit
        self._process_slots.set_limit(fixed_process or process)
        self._upload_slots.set_limit(fixed_upload or upload)
        self._budget.set_limit(budget)
        return process, upload

    def set_paced(self, paced: bool) -> int:
        """Paced mode waits for a go-ahead after copying and again before uploading.

        Switching back to auto sends waiting photos on, except those an event holds for
        review (``hold_uploads``). Returns how many photos moved on.
        """
        self._paced = paced
        if paced:
            return 0
        moved = self.process_ready()
        for eid, _state in self._event_states(None):
            event = self._store.get(eid)
            if event is not None and not event.hold_uploads:
                moved += self.release_held(eid)
        return moved

    def process_ready(self, event_id: str | None = None) -> int:
        """Paced mode: start processing every copied photo waiting for the go-ahead."""
        started = 0
        for eid, state in self._event_states(event_id):
            for photo in list(state.photos.values()):
                if photo.status == "copied" and photo.held:
                    photo.held = False
                    state.put_photo(photo)
                    self._enqueue("process", eid, photo.name)
                    self._touch(photo.batch_id)
                    started += 1
        return started

    def release_held(self, event_id: str | None = None) -> int:
        """FR-34: send every held photo (of one event, or all) on to upload."""
        released = 0
        for eid, state in self._event_states(event_id):
            for photo in list(state.photos.values()):
                if photo.status == "processed" and photo.held:
                    photo.held = False
                    state.put_photo(photo)
                    self._enqueue("upload", eid, photo.name)
                    self._touch(photo.batch_id)
                    released += 1
        return released

    def retry_failed(self, event_id: str | None = None) -> int:
        """FR-35: reset attempts and requeue each failed photo where it failed."""
        retried = 0
        for eid, state in self._event_states(event_id):
            for photo in list(state.photos.values()):
                if photo.large == "failed":  # FR-42: the full-size copy only
                    photo.large = ""
                    state.put_photo(photo)
                    self._enqueue("large", eid, photo.name)
                    retried += 1
                if photo.status != "failed":
                    continue
                stage = photo.failed_stage
                if stage != "process" and not photo.remote_id:
                    # Nothing uploaded yet; redo processing if its output is gone.
                    if not state.processed_file(photo).is_file():
                        stage = "process"
                photo.attempts, photo.error, photo.failed_stage = 0, "", ""
                photo.held = False
                photo.status = "copied" if stage == "process" else "processed"
                state.put_photo(photo)
                self._enqueue("process" if stage == "process" else "upload", eid, photo.name)
                self._touch(photo.batch_id)
                retried += 1
        return retried

    def resume(self) -> None:
        """FR-36: pick up every unfinished photo in every event."""
        for event in self._store.events():
            root = self._store.event_root(event)
            if not (root / ".WeFlow").is_dir():
                continue
            state = self._state_for(event)
            for folder in (state.originals, state.processed, state.work, state.large):
                for leftover in folder.glob(".tmp-*"):
                    with contextlib.suppress(OSError):
                        leftover.unlink()
            with self._lock:
                for batch in state.batches.values():
                    batch.previous_session = True
                    batch.rate = 0.0
                    self._batches[batch.id] = batch
                    self._last_id = max(self._last_id, batch.id)
                    if batch.state == "copying":  # killed mid-copy: never safe
                        batch.state, batch.error = "error", INTERRUPTED
                        state.put_batch(batch)
            for photo in list(state.photos.values()):
                owner = state.batches.get(photo.batch_id)
                if photo.large == "failed":  # FR-42: try the full-size copy again
                    photo.large = ""
                    state.put_photo(photo)
                if photo.status == "copied" and not photo.held:
                    self._enqueue("process", event.id, photo.name)
                elif photo.status == "processed" and not photo.held:
                    if owner is not None and owner.destination != "none":
                        self._enqueue("upload", event.id, photo.name)
            for batch in list(state.batches.values()):
                if batch.state != "done":
                    self._touch(batch.id)
                self._queue_large(event, state, batch)

    def make_large(self, event_id: str) -> int:
        """FR-42: queue full-size copies of the event's photos that are already up and
        have none (the switch was just turned on). Returns how many were queued."""
        event = self._store.get(event_id)
        if event is None:
            return 0
        queued = 0
        for _eid, state in self._event_states(event_id):
            for batch in list(state.batches.values()):
                queued += self._queue_large(event, state, batch)
        return queued

    def snapshot(self) -> list[Batch]:
        with self._lock:
            return sorted((replace(b) for b in self._batches.values()), key=lambda b: b.started)

    def has_pending(self, event_id: str | None = None) -> bool:
        """True while any batch (of one event, or all) is still being worked on."""
        with self._lock:
            return any(
                b.state in ACTIVE_STATES
                for b in self._batches.values()
                if event_id is None or b.event_id == event_id
            )

    def stop(self, timeout: float = 5) -> None:
        """Stop all workers and save state. Unfinished work resumes next launch."""
        if self._stopping.is_set():
            return
        self._stopping.set()
        self._copy_q.put(None)
        self._process_slots.close()
        self._upload_slots.close()
        for _ in self._threads:
            self._process_q.put((-1, next(self._order), "", "", ""))
        self._upload_q.close()
        deadline = time.monotonic() + timeout
        for thread in self._threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        with self._lock:
            states = list(self._states.values())
        for state in states:
            state.close()

    # -------------------------------------------------------------- plumbing
    def _emit(self, event: Event) -> None:
        try:
            self._emit_cb(event)
        except Exception:  # a listener must never take a worker down
            log.exception("event listener failed")

    def _state_for(self, event: Profile) -> EventState:
        root = self._store.event_root(event)
        with self._lock:
            state = self._states.get(event.id)
            if state is None or state.root != root:
                if state is not None:
                    state.close()
                state = EventState(root, event.originals_dir)
                self._states[event.id] = state
            return state

    def _event_states(self, event_id: str | None) -> list[tuple[str, EventState]]:
        with self._lock:
            return [(e, s) for e, s in self._states.items() if event_id in (None, e)]

    def _enqueue(self, stage: str, event_id: str, name: str) -> None:
        with self._lock:
            key = (stage, event_id, name)
            if key in self._queued or self._stopping.is_set():
                return
            self._queued.add(key)
        if stage in ("process", "large"):
            priority = 1 if stage == "large" else 0
            self._process_q.put((priority, next(self._order), stage, event_id, name))
        else:
            self._upload_q.put((event_id, name))

    def _dequeued(self, stage: str, task: Task) -> None:
        with self._lock:
            self._queued.discard((stage, task[0], task[1]))

    def _touch(self, batch_id: int, now: bool = False) -> None:
        if now:
            self._flush(batch_id)
        else:
            with self._lock:
                self._dirty.add(batch_id)

    def _lookup(self, task: Task) -> tuple[Profile, EventState, PhotoRecord, Batch] | None:
        event = self._store.get(task[0])
        with self._lock:
            state = self._states.get(task[0])
        if event is None or state is None:
            return None
        photo = state.photos.get(task[1])
        batch = state.batches.get(photo.batch_id) if photo else None
        if photo is None or batch is None:
            return None
        return event, state, photo, batch

    # --------------------------------------------------------------- flusher
    def _flush_loop(self) -> None:
        while not self._stopping.wait(FLUSH_SECONDS):
            self._flush_dirty()
        self._flush_dirty()

    def _flush_dirty(self) -> None:
        with self._lock:
            dirty, self._dirty = self._dirty, set()
        for batch_id in dirty:
            self._flush(batch_id)

    def _flush(self, batch_id: int) -> None:
        notices: list[Notice] = []
        with self._lock:
            self._dirty.discard(batch_id)
            batch = self._batches.get(batch_id)
            state = self._states.get(batch.event_id) if batch else None
            if batch is None or state is None:
                return
            if batch.verified or batch.state == "error":
                self._recount(batch, state, notices)
            snapshot = replace(batch)
        self._emit(BatchUpdated(snapshot))
        for notice in notices:
            self._emit(notice)

    def _recount(self, batch: Batch, state: EventState, notices: list[Notice]) -> None:
        photos = state.batch_photos(batch.id)
        uploads = batch.destination != "none"
        to_process = sum(p.status == "copied" and not p.held for p in photos)
        batch.ready = sum(p.status == "copied" and p.held for p in photos)
        to_upload = sum(p.status == "processed" and not p.held for p in photos) if uploads else 0
        failed = [p for p in photos if p.status == "failed"]
        batch.uploaded = sum(p.status == "uploaded" for p in photos)
        batch.held = sum(p.status == "processed" and p.held for p in photos)
        batch.failed = len(failed)
        failed_processing = sum(p.failed_stage == "process" for p in failed)
        batch.processed = len(photos) - to_process - batch.ready - failed_processing
        if batch.state == "error":
            return
        if to_process:
            new = "processing"
        elif to_upload:
            new = "uploading"
        elif failed:
            new = "attention"
        elif batch.ready:
            new = "ready"
        elif batch.held:
            new = "held"
        else:
            new = "done"
        if new == "processing" and batch.state == "safe" and batch.processed == 0:
            new = "safe"  # stays "safe" until the first photo is through
        waiting = self._waiting.get(batch.id, "") if new == "uploading" else ""
        if new == "attention":
            count = "1 photo" if len(failed) == 1 else f"{len(failed)} photos"
            waiting = f"{count} failed. {failed[0].error}".strip()
        if new == batch.state and waiting == batch.error:
            return
        changed = new != batch.state
        batch.state, batch.error = new, waiting
        if not changed:
            return
        batch.finished = time.time() if new in ("done", "attention", "held", "ready") else 0.0
        state.put_batch(batch)
        if new == "attention":
            notices.append(Notice("error", f"{batch.label}: {batch.error}", batch.id))
        elif new == "held":
            notices.append(
                Notice("info", f"{batch.label}: {plural(batch.held)} ready to upload.", batch.id)
            )
        elif new == "ready" and not batch.is_card:  # a card already said "safe to remove"
            notices.append(
                Notice("info", f"{batch.label}: {plural(batch.ready)} ready to process.", batch.id)
            )
        elif new == "done":
            notices.append(Notice("info", f"{batch.label}: {done_summary(batch)}", batch.id))

    # ---------------------------------------------------------------- copier
    def _copy_loop(self) -> None:
        while True:
            batch_id = self._copy_q.get()
            if batch_id is None:
                return
            self._copying = True
            self._apply_limits()
            try:
                self._copy_batch(batch_id)
            except Exception:
                log.exception("copy worker failed")
            finally:
                self._copying = False
                self._apply_limits()

    def _copy_batch(self, batch_id: int) -> None:
        with self._lock:
            batch = self._batches[batch_id]
            state = self._states[batch.event_id]
        event = self._store.get(batch.event_id)
        folder = event.folder if event else state.root.name
        names: list[str] = []
        try:
            source = Path(batch.source)
            if not source.is_dir():
                raise ingest.CopyError("The card was removed or could not be read.")
            files = ingest.list_jpegs(source)
            batch.total = len(files)
            batch.bytes_total = sum(_size(p) for p in files)
            self._touch(batch.id, now=True)
            known = state.hashes()
            meter = RateMeter(3.0)

            def progress(count: int) -> None:
                batch.bytes_done += count
                batch.rate = meter.add(count)
                self._touch(batch.id)

            for path in files:
                if self._stopping.is_set():
                    return  # stays "copying" on disk; resume() turns it into an error
                copied = ingest.ingest_file(path, state.originals, folder, known, progress)
                if copied is None:
                    batch.skipped += 1
                else:
                    photo = PhotoRecord(
                        copied.name,
                        copied.sha256,
                        copied.captured,
                        batch.id,
                        original=copied.original,
                        seq=state.next_seq(),
                        held=self._paced,  # paced: wait for the go-ahead to process
                    )
                    state.put_photo(photo)
                    names.append(copied.name)
                    batch.copied += 1
                    if self._speed == "high" and not photo.held:
                        # MAX (1.15.0): this photo is copied and verified, so start on it
                        # now. The batch still isn't safe until every file is (FR-15).
                        self._enqueue("process", batch.event_id, copied.name)
                self._touch(batch.id)
            ingest.sync_dir(state.originals)
            state.sync()
        except (ingest.CopyError, OSError) as exc:
            message = str(exc) if isinstance(exc, ingest.CopyError) else "The copy failed."
            log.warning("batch %s copy failed: %s", batch.id, message)
            with self._lock:
                batch.state, batch.error, batch.finished = "error", message, time.time()
            state.put_batch(batch)
            self._emit(Notice("error", f"{batch.label}: don't remove yet. {message}", batch.id))
        else:
            # FR-15: only now, with every file copied, verified and on disk.
            with self._lock:
                batch.verified = True
                batch.state = "safe" if batch.is_card else "processing"
                batch.copy_seconds = time.time() - batch.started
                batch.rate = 0.0
            state.put_batch(batch)
            state.sync()
            if batch.is_card:
                self._emit(Notice("safe", f"{batch.label}: {safe_summary(batch)}", batch.id))
            else:
                self._emit(Notice("info", f"{batch.label}: {copy_summary(batch)}", batch.id))
        nudge = ""
        for name in names:
            waiting = state.photos.get(name)
            if waiting is not None and waiting.status == "uploaded":
                nudge = name
            elif waiting is not None and not waiting.held:
                self._enqueue("process", batch.event_id, name)
        if nudge:  # MAX: photos went up during the copy, and albums aren't sorted until now
            self._enqueue("upload", batch.event_id, nudge)
        self._touch(batch.id, now=True)

    # ------------------------------------------------------------ processing
    def _process_loop(self) -> None:
        while self._process_slots.acquire():
            try:
                priority, _, stage, event_id, name = self._process_q.get()
                if priority < 0 or self._stopping.is_set():
                    return
                task = (event_id, name)
                try:
                    if stage == "large":
                        self._large_one(task)
                    else:
                        self._process_one(task)
                except Exception:
                    log.exception("process worker failed on %s", name)
                finally:
                    self._dequeued(stage, task)
            finally:
                self._process_slots.release()

    def _process_one(self, task: Task) -> None:
        found = self._lookup(task)
        if found is None:
            return
        event, state, photo, batch = found
        if photo.status != "copied":
            return
        self._name_output(event, state, photo)
        # Copies that will be uploaded wait in the hidden work folder; local-only ones
        # are the finished result and go straight to processed/.
        folder = state.processed if batch.destination == "none" else state.work
        try:
            imaging.process_file(
                state.originals / photo.name,
                folder / photo.processed_name,
                event,
                self._budget.reserve,
            )
        except Exception as exc:
            message = str(exc)
            if not isinstance(exc, imaging.ProcessError):
                log.exception("processing %s failed unexpectedly", photo.name)
                message = "This photo could not be processed. Press Retry."
            photo.status, photo.failed_stage, photo.error = "failed", "process", message
            state.put_photo(photo)
            log.warning("processing %s failed: %s", photo.name, message)
        else:
            photo.status, photo.error = "processed", ""
            upload = batch.destination != "none"
            photo.held = upload and (event.hold_uploads or self._paced)
            state.put_photo(photo)
            if upload and not photo.held:
                self._enqueue("upload", task[0], photo.name)
        self._touch(batch.id)

    @staticmethod
    def _name_output(event: Profile, state: EventState, photo: PhotoRecord) -> None:
        """Fix the processed copy's name the first time the photo is processed."""
        if photo.output:
            return
        with state.lock:
            if not photo.seq:  # imported before 1.2.0
                photo.seq = state.next_seq()
            stem = naming.output_stem(event, photo.name, photo.original, photo.captured, photo.seq)
            if event.file_naming == "import":
                photo.output = photo.name
            else:
                photo.output = naming.unique_name(stem, state.output_taken)
            state.put_photo(photo)

    # ------------------------------------------------------------- uploading
    def _upload_loop(self) -> None:
        while self._upload_slots.acquire():
            try:
                task = self._upload_q.get()
                if task is None:
                    return
                delay: float | None = None
                try:
                    delay = self._upload_one(task)
                except Exception:
                    log.exception("upload worker failed on %s", task[1])
                if delay is None:
                    self._dequeued("upload", task)
                else:
                    self._upload_q.put(task, delay)
            finally:
                self._upload_slots.release()

    def _uploader(self, destination: str) -> Uploader:
        with self._lock:
            if destination not in self._uploaders:
                self._uploaders[destination] = self._uploader_factory(destination)
            return self._uploaders[destination]

    def _upload_one(self, task: Task) -> float | None:
        """Upload, then add to the album. Returns a retry delay, or ``None`` when settled."""
        found = self._lookup(task)
        if found is None:
            return None
        event, state, photo, batch = found
        if photo.status == "uploaded" and batch.destination != "none":
            # The copier's nudge: everything may have gone up before the copy ended.
            self._sort_when_settled(self._uploader(batch.destination), event, state, batch)
            self._queue_large(event, state, batch)
            return None
        if photo.status != "processed" or photo.held:
            return None
        stage = "album" if photo.remote_id else "upload"
        source = state.processed_file(photo)
        if not photo.remote_id and not source.is_file():
            # The processed copy went missing before upload: make it again from the original.
            photo.status = "copied"
            state.put_photo(photo)
            self._enqueue("process", task[0], photo.name)
            self._touch(batch.id)
            return None
        gate = self._gates.get(batch.destination)
        tuner = None if gate is not None else self._tuner
        try:
            uploader = self._uploader(batch.destination)
            if not photo.remote_id:  # FR-32: a stored remote_id is never uploaded again
                if gate is not None and not gate.try_acquire():
                    return GATE_WAIT  # the service's limit is full: not an attempt
                with self._lock:
                    meter = self._upload_meters.setdefault(batch.id, RateMeter(20.0))
                size = _size(source)
                photo.remote_id = self._send(uploader, source, event, size, gate, tuner)
                state.put_photo(photo)
                stage = "album"
                with self._lock:
                    batch.upload_bytes += size
                    rate = meter.add(size)
                    if batch.state != "copying":  # while copying, the rate shown is the copy's
                        batch.rate = rate
            uploader.add_to_album(photo.remote_id, event)
        except Exception as caught:
            if isinstance(caught, UploadError):
                exc = caught
            else:  # a bug or an unplanned failure: keep the photo moving, never drop it
                log.exception("upload of %s failed unexpectedly", photo.name)
                exc = UploadError("The upload hit a problem. WeFlow will keep trying.")
            if exc.busy and tuner is not None:
                self._tune(tuner, tuner.busy())
            photo.attempts += 1
            photo.error = exc.message
            if exc.permanent or photo.attempts >= MAX_ATTEMPTS:
                photo.status, photo.failed_stage = "failed", stage
                state.put_photo(photo)
                log.warning("upload of %s failed for good: %s", photo.name, exc.message)
                self._touch(batch.id)
                return None
            state.put_photo(photo)
            with self._lock:
                first = self._waiting.get(batch.id) != exc.message
                self._waiting[batch.id] = exc.message
            if first:
                self._emit(Notice("info", f"{batch.label}: {exc.message}", batch.id))
            self._touch(batch.id)
            return self._retry_delay(photo.attempts)
        # Before marking it uploaded: a kill in between only repeats the album step,
        # which needs no file.
        self._settle_output(event, state, photo)
        photo.status, photo.attempts, photo.error = "uploaded", 0, ""
        state.put_photo(photo)
        with self._lock:
            self._waiting.pop(batch.id, None)
        self._touch(batch.id)
        self._sort_when_settled(uploader, event, state, batch)
        self._queue_large(event, state, batch)
        return None

    def _send(
        self,
        uploader: Uploader,
        source: Path,
        event: Profile,
        size: int,
        gate: Slots | None,
        tuner: UploadTuner | None,
    ) -> str:
        """Send one file, holding the service's slot and telling the tuner how it went."""
        if tuner is not None:
            tuner.begin()
        sent = 0
        try:
            remote_id = uploader.upload(source, event)
            sent = size
            return remote_id
        finally:
            if gate is not None:
                gate.release()
            if tuner is not None:
                self._tune(tuner, tuner.end(sent))

    def _tune(self, tuner: UploadTuner, limit: int | None) -> None:
        if limit is None:
            return
        with self._lock:
            if self._tuner is not tuner:
                return  # the speed changed meanwhile
            self._upload_slots.set_limit(limit)
        log.info("MAX speed: %d uploads at once", limit)

    def _sort_when_settled(
        self, uploader: Uploader, event: Profile, state: EventState, batch: Batch
    ) -> None:
        """Once nothing in the batch is left to upload, sort its album once (FR-31a).
        A failed sort never fails a photo: they are already up."""
        if batch.state == "copying":
            return  # MAX uploads during the copy; more photos are coming
        photos = state.batch_photos(batch.id)
        if any(p.status in ("copied", "processed") and not p.held for p in photos):
            return
        uploaded = sum(p.status == "uploaded" for p in photos)
        with self._lock:  # two upload workers can finish together: sort only once
            if self._sorted.get(batch.id) == uploaded:
                return
            self._sorted[batch.id] = uploaded
        try:
            uploader.sort_album(event)
        except Exception as exc:
            message = exc.message if isinstance(exc, UploadError) else type(exc).__name__
            log.warning("sorting the album for batch %s failed: %s", batch.id, message)
            self._emit(
                Notice(
                    "info",
                    f"{batch.label}: photos are up, but the album could not be sorted. "
                    "It will be sorted after the next card.",
                    batch.id,
                )
            )

    # ------------------------------------------------------ full size (FR-42)
    def _queue_large(self, event: Profile, state: EventState, batch: Batch) -> int:
        """Once nothing in the batch is left to upload, queue a full-size copy of each
        photo that is up. Returns how many were queued."""
        if not event.makes_large or batch.destination != "smugmug" or batch.state == "copying":
            return 0
        photos = state.batch_photos(batch.id)
        if any(p.status in ("copied", "processed") and not p.held for p in photos):
            return 0
        queued = 0
        for photo in photos:
            if photo.status == "uploaded" and not photo.large:
                self._enqueue("large", event.id, photo.name)
                queued += 1
        return queued

    def _large_one(self, task: Task) -> None:
        """Process the original again at full size and without a size limit."""
        found = self._lookup(task)
        if found is None:
            return
        event, state, photo, batch = found
        if photo.status != "uploaded" or photo.large or not event.makes_large:
            return
        full = replace(event, max_long_edge=0, limit_file_size=False, jpeg_quality=100)
        try:
            imaging.process_file(
                state.originals / photo.name,
                state.large / photo.processed_name,
                full,
                self._budget.reserve,
            )
        except Exception as exc:
            if not isinstance(exc, imaging.ProcessError):
                log.exception("full-size copy of %s failed unexpectedly", photo.name)
            log.warning("full-size copy of %s failed", photo.name)
            photo.large = "failed"
        else:
            photo.large = "done"
        state.put_photo(photo)
        self._large_finished(state, batch)

    def _large_finished(self, state: EventState, batch: Batch) -> None:
        """Say once when every full-size copy of the batch is made (or has failed)."""
        photos = [p for p in state.batch_photos(batch.id) if p.status == "uploaded"]
        if any(not p.large for p in photos):
            return
        done = sum(p.large == "done" for p in photos)
        failed = sum(p.large == "failed" for p in photos)
        with self._lock:  # two workers can finish together: say it once
            if self._large_told.get(batch.id) == (done, failed):
                return
            self._large_told[batch.id] = (done, failed)
        if failed:
            self._emit(
                Notice(
                    "error",
                    f"{batch.label}: {plural(failed)} could not be saved at full size. "
                    "WeFlow tries again next time it starts.",
                    batch.id,
                )
            )
        elif done:
            self._emit(
                Notice(
                    "info",
                    f"{batch.label}: {plural(done)} saved at full size in {LARGE_FOLDER}.",
                    batch.id,
                )
            )

    @staticmethod
    def _settle_output(event: Profile, state: EventState, photo: PhotoRecord) -> None:
        """Once a photo is up and in its album, move its processed copy into processed/
        if the event keeps them, otherwise delete it. Only the work folder is touched:
        files already in processed/ (from before 1.5.0) stay where they are."""
        work = state.work / photo.processed_name
        try:
            if not work.exists():
                return
            if event.keep_processed:
                state.processed.mkdir(parents=True, exist_ok=True)
                os.replace(work, state.processed / photo.processed_name)
            else:
                work.unlink()
        except OSError:
            log.warning("could not tidy the processed copy of %s", photo.name, exc_info=True)


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def plural(count: int, word: str = "photo") -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def safe_summary(batch: Batch) -> str:
    took = f" in {duration(batch.copy_seconds)}" if batch.copy_seconds >= 1 else ""
    text = f"safe to remove. {plural(batch.copied)} copied and verified{took}."
    if batch.skipped and not batch.copied:
        text = "safe to remove. Nothing new on this card."
    return text


def copy_summary(batch: Batch) -> str:
    """Said once every file is copied and verified."""
    if not batch.copied:
        return "copy done. Nothing new to copy."
    took = f" in {duration(batch.copy_seconds)}" if batch.copy_seconds >= 1 else ""
    return f"copy done. {plural(batch.copied)} copied and verified{took}."


def duration(seconds: float) -> str:
    """``8 s``, ``2 min 5 s``, ``1 h 3 min``."""
    whole = max(0, round(seconds))
    if whole < 60:
        return f"{whole} s"
    minutes, secs = divmod(whole, 60)
    if minutes < 60:
        return f"{minutes} min {secs} s" if secs else f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


def speed(rate: float) -> str:
    """Bytes per second as ``38.2 MB/s``."""
    mb = rate / 1_000_000
    return f"{mb:.1f} MB/s" if mb < 100 else f"{mb:.0f} MB/s"


def done_summary(batch: Batch) -> str:
    if batch.destination == "none":
        return f"all done. {plural(batch.processed)} ready in the library."
    if not batch.copied:
        return "all done. Nothing new to upload."
    return f"all done. {plural(batch.uploaded)} uploaded to {service_name(batch.destination)}."
