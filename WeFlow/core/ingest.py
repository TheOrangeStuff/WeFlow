"""Verified copy from a card: hash, re-hash, rename, de-duplicate (FR-12 – FR-17).

Card files are only ever opened with mode ``"rb"``. Nothing on the source is
renamed, moved, deleted or written.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image

CHUNK = 4 * 1024 * 1024
JPEG_SUFFIXES = {".jpg", ".jpeg"}
EXIF_IFD = 0x8769
DATETIME_ORIGINAL = 0x9003
DATETIME = 0x0132
_hasher = ThreadPoolExecutor(max_workers=1, thread_name_prefix="weflow-hash")


class CopyError(Exception):
    """A file could not be copied and verified. The message is shown to the user."""


class ChecksumMismatch(CopyError):
    pass


@dataclass(frozen=True)
class Copied:
    name: str
    sha256: str
    captured: str  # ISO date-time
    original: str = ""  # the card's file name, without extension


def list_jpegs(source: Path) -> list[Path]:
    """All JPEGs under ``source/DCIM`` (or ``source`` itself), skipping dotfiles."""
    root = source / "DCIM" if (source / "DCIM").is_dir() else source
    found: list[Path] = []
    for folder, dirnames, filenames in os.walk(root, onerror=_raise):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for filename in sorted(filenames):
            if filename.startswith("."):  # includes AppleDouble "._*"
                continue
            if os.path.splitext(filename)[1].lower() in JPEG_SUFFIXES:
                found.append(Path(folder) / filename)
    return found


def _raise(error: OSError) -> None:
    raise error


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


Progress = Callable[[int], None]  # called with each chunk's size in bytes


def copy_verified(source: Path, temp: Path, progress: Progress | None = None) -> str:
    """Copy ``source`` to ``temp``, hashing as it goes; fsync; re-hash the copy.

    Returns the SHA-256. On a mismatch the temp file is deleted and
    ``ChecksumMismatch`` is raised (FR-12).
    """
    digest = hashlib.sha256()
    try:
        with open(source, "rb") as src, open(temp, "wb") as dst:
            while chunk := src.read(CHUNK):
                hashing = _hasher.submit(digest.update, chunk)  # hash while writing
                dst.write(chunk)
                hashing.result()
                if progress is not None:
                    progress(len(chunk))
            dst.flush()
            os.fsync(dst.fileno())
        if hash_file(temp) != digest.hexdigest():
            raise ChecksumMismatch(f"{source.name} did not copy correctly.")
    except BaseException:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise
    return digest.hexdigest()


def captured_at(photo: Path, fallback_mtime: float) -> datetime:
    """EXIF DateTimeOriginal, else the file's modified time (FR-13)."""
    try:
        with Image.open(photo) as img:
            exif = img.getexif()
            raw = exif.get_ifd(EXIF_IFD).get(DATETIME_ORIGINAL) or exif.get(DATETIME)
        if raw:
            return datetime.strptime(str(raw).strip().rstrip("\x00"), "%Y:%m:%d %H:%M:%S")
    except (OSError, ValueError, SyntaxError):
        pass
    return datetime.fromtimestamp(fallback_mtime)


def target_name(name: str, taken: Callable[[str], bool]) -> str:
    """The camera's own file name, unchanged; ``_2``, ``_3``… before the extension
    only when that name is already in the event (FR-13, 1.10.0)."""
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    candidate, n = name, 2
    while taken(candidate):
        candidate = f"{stem}_{n}{dot}{ext}"
        n += 1
    return candidate


def ingest_file(
    source: Path,
    originals: Path,
    folder: str,
    known_hashes: set[str],
    progress: Progress | None = None,
) -> Copied | None:
    """Copy and verify one photo into ``originals``.

    Returns ``None`` when its checksum is already known (FR-14); otherwise the new
    record, and adds the checksum to ``known_hashes``.
    """
    originals.mkdir(parents=True, exist_ok=True)
    temp = originals / f".tmp-{os.getpid()}-{source.name}"
    try:
        mtime = source.stat().st_mtime
        sha = copy_verified(source, temp, progress)
    except CopyError:
        raise
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise CopyError("This computer is out of space. Free some space first.") from exc
        raise CopyError("The card was removed or could not be read.") from exc
    try:
        if sha in known_hashes:
            temp.unlink()
            return None
        captured = captured_at(temp, mtime)
        name = target_name(source.name, lambda n: (originals / n).exists())
        os.utime(temp, (mtime, mtime))
        os.replace(temp, originals / name)
    except OSError as exc:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise CopyError("A photo could not be saved on this computer.") from exc
    known_hashes.add(sha)
    return Copied(
        name=name,
        sha256=sha,
        captured=captured.isoformat(timespec="seconds"),
        original=source.stem,
    )


def sync_dir(folder: Path) -> None:
    """Flush directory entries (renames) to disk where the OS supports it."""
    if os.name == "nt":
        return
    with contextlib.suppress(OSError):
        fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
