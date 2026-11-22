"""Erase a card's copied photos, on request only (FR-47, 1.20.0).

This is the one place WeFlow ever changes a card, and only when the user asks for it
twice. A photo is removed from the card only when:

- it is a JPEG WeFlow would import (``ingest.list_jpegs``),
- its checksum, read again from the card now, matches a photo in the event, and
- that photo's copy on this computer is there and still has the same checksum.

Anything else (other files, folders, RAW files, photos not copied) stays on the card.
Nothing is formatted.
"""

from __future__ import annotations

import errno
import os
from dataclasses import dataclass
from pathlib import Path

from . import ingest

LOCKED = "The card is locked. Slide its lock switch up and try again."
FAILED = "The card could not be erased. Check it is still in and try again."


@dataclass(frozen=True)
class Erased:
    ok: bool
    erased: int = 0
    kept: int = 0  # JPEGs left on the card because no verified copy matches them
    message: str = ""


def erase_copied(card: Path, copies: dict[str, Path]) -> Erased:
    """Delete the JPEGs on ``card`` whose checksum is a key of ``copies`` and whose
    copy (the value) is on this computer with that same checksum."""
    erased = kept = 0
    try:
        files = ingest.list_jpegs(card)
        for path in files:
            sha = ingest.hash_file(path)
            copy = copies.get(sha)
            if copy is None or not _same(copy, sha):
                kept += 1
                continue
            os.remove(path)
            erased += 1
        ingest.sync_dir(card)
    except OSError as exc:
        locked = exc.errno in (errno.EROFS, errno.EACCES, errno.EPERM)
        return Erased(False, erased, kept, LOCKED if locked else FAILED)
    return Erased(True, erased, kept, summary(erased, kept))


def _same(copy: Path, sha: str) -> bool:
    try:
        return copy.is_file() and ingest.hash_file(copy) == sha
    except OSError:
        return False


def _plural(count: int) -> str:
    return f"{count} photo" if count == 1 else f"{count} photos"


def summary(erased: int, kept: int) -> str:
    if not erased and not kept:
        return "The card had no photos to erase."
    text = f"Erased {_plural(erased)} from the card."
    if kept:
        text += f" {_plural(kept)} without a verified copy stayed on it."
    return text
