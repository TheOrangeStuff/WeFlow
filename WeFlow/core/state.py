"""Batch and photo records, and their per-event persistence (§4.2–4.4).

Each event keeps ``<Library>/<folder>/.WeFlow/state.json``. To save after every
file transition (NFR-05) without rewriting the whole file each time, changes are
appended to ``journal.jsonl`` beside it and folded into ``state.json`` regularly.
Loading reads ``state.json`` and replays the journal.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import IO, Any

from .config import atomic_write_text

BATCH_STATES = (
    "copying",
    "safe",
    "processing",
    "uploading",
    "held",
    "done",
    "error",
    "attention",
)
COMPACT_EVERY = 500


@dataclass
class PhotoRecord:
    name: str
    sha256: str
    captured: str = ""
    batch_id: int = 0
    status: str = "copied"  # copied | processed | uploaded | failed
    failed_stage: str = ""  # process | upload | album
    remote_id: str = ""
    held: bool = False
    attempts: int = 0
    error: str = ""
    original: str = ""  # the camera's file name, without extension
    seq: int = 0  # 1-based import order within the event; 0 = not given yet
    output: str = ""  # processed file name, fixed when first processed; "" = ``name``

    @property
    def processed_name(self) -> str:
        return self.output or self.name


@dataclass
class Batch:
    """One card insertion or folder import."""

    id: int
    event_id: str
    event_name: str = ""
    label: str = ""
    source: str = ""
    is_card: bool = False
    destination: str = "none"  # the event's destination when the batch started
    state: str = "copying"
    verified: bool = False  # every file copied and checksum-verified
    total: int = 0  # JPEGs found on the card
    copied: int = 0  # new photos copied and verified
    skipped: int = 0  # already imported (FR-14)
    processed: int = 0
    uploaded: int = 0
    failed: int = 0
    held: int = 0
    error: str = ""
    started: float = 0.0
    finished: float = 0.0
    previous_session: bool = False

    @property
    def photos(self) -> int:
        """Photos this batch is responsible for."""
        return self.copied


def _load_into(cls: type[Any], data: dict[str, Any]) -> Any:
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


class EventState:
    """Photo and batch records of one event. Thread-safe."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.dir = root / ".WeFlow"
        self.state_path = self.dir / "state.json"
        self.journal_path = self.dir / "journal.jsonl"
        self.photos: dict[str, PhotoRecord] = {}
        self._outputs: set[str] = set()  # processed names in use, casefolded
        self._last_seq = 0
        self.batches: dict[int, Batch] = {}
        self.lock = threading.RLock()
        self._journal: IO[str] | None = None
        self._pending = 0
        self._load()

    @property
    def originals(self) -> Path:
        return self.root / "originals"

    @property
    def processed(self) -> Path:
        return self.root / "processed"

    # ----------------------------------------------------------------- load
    def _load(self) -> None:
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        for item in data.get("photos", []):
            self._apply({"photo": item})
        for item in data.get("batches", []):
            self._apply({"batch": item})
        try:
            with open(self.journal_path, encoding="utf-8") as fh:
                for line in fh:
                    # A kill can leave a torn last line; ignore it.
                    with contextlib.suppress(ValueError):
                        self._apply(json.loads(line))
                        self._pending += 1
        except OSError:
            pass

    def _apply(self, entry: dict[str, Any]) -> None:
        try:
            if "photo" in entry:
                photo: PhotoRecord = _load_into(PhotoRecord, entry["photo"])
                self._remember(photo)
            elif "batch" in entry:
                batch: Batch = _load_into(Batch, entry["batch"])
                self.batches[batch.id] = batch
        except TypeError:
            pass

    # ---------------------------------------------------------------- write
    def _append(self, entry: dict[str, Any]) -> None:
        if self._journal is None:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._journal = open(self.journal_path, "a", encoding="utf-8")  # noqa: SIM115
        self._journal.write(json.dumps(entry, separators=(",", ":")) + "\n")
        self._journal.flush()
        self._pending += 1
        if self._pending >= COMPACT_EVERY:
            self.compact()

    def _remember(self, photo: PhotoRecord) -> None:
        self.photos[photo.name] = photo
        if photo.output:
            self._outputs.add(photo.output.casefold())
        self._last_seq = max(self._last_seq, photo.seq)

    def next_seq(self) -> int:
        """The next import-order number for this event."""
        with self.lock:
            self._last_seq += 1
            return self._last_seq

    def output_taken(self, name: str) -> bool:
        """True when another photo's processed copy already uses ``name``."""
        with self.lock:
            return name.casefold() in self._outputs or (self.processed / name).exists()

    def put_photo(self, photo: PhotoRecord) -> None:
        with self.lock:
            self._remember(photo)
            self._append({"photo": asdict(photo)})

    def put_batch(self, batch: Batch) -> None:
        with self.lock:
            self.batches[batch.id] = batch
            self._append({"batch": asdict(batch)})

    def sync(self) -> None:
        """Force the journal to disk. Called before a batch may be shown as safe."""
        with self.lock:
            if self._journal is not None:
                self._journal.flush()
                os.fsync(self._journal.fileno())

    def compact(self) -> None:
        """Fold the journal into state.json."""
        with self.lock:
            if not self.photos and not self.batches:
                return
            payload = {
                "version": 1,
                "photos": [asdict(p) for p in self.photos.values()],
                "batches": [asdict(b) for b in self.batches.values()],
            }
            atomic_write_text(self.state_path, json.dumps(payload, indent=1))
            if self._journal is not None:
                self._journal.close()
                self._journal = None
            try:
                self.journal_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                # Windows refuses to delete a file something else has open; empty it instead.
                with contextlib.suppress(OSError), open(self.journal_path, "w"):
                    pass
            self._pending = 0

    def close(self) -> None:
        with self.lock:
            if self._pending:
                self.compact()
            if self._journal is not None:
                self._journal.close()
                self._journal = None

    # ---------------------------------------------------------------- query
    def hashes(self) -> set[str]:
        with self.lock:
            return {p.sha256 for p in self.photos.values()}

    def batch_photos(self, batch_id: int) -> list[PhotoRecord]:
        with self.lock:
            return [p for p in self.photos.values() if p.batch_id == batch_id]
