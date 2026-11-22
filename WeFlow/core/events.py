"""Typed events the core emits through a callback (§8). Emitted on worker threads."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from .state import Batch

NoticeLevel = Literal["info", "safe", "error"]


@dataclass(frozen=True)
class BatchUpdated:
    """Any counter or state change of a batch (at most 10 per second per batch)."""

    batch: Batch


@dataclass(frozen=True)
class CardDetected:
    path: str
    label: str


@dataclass(frozen=True)
class CardRemoved:
    path: str


@dataclass(frozen=True)
class FolderPhotos:
    """New photos have finished arriving in the watched folder (FR-48)."""

    folder: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class Notice:
    level: NoticeLevel
    message: str
    batch_id: int | None = None


Event = BatchUpdated | CardDetected | CardRemoved | FolderPhotos | Notice
Emit = Callable[[Event], None]
