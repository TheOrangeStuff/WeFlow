"""File names for processed copies, Lightroom-style (custom name, sequence, date, filename).

Originals keep their import name (FR-13). A processed copy's name is worked out once,
when the photo is first processed, and stored on its record (``PhotoRecord.output``).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime

from .config import Profile, slugify

SEQUENCE_DIGITS = 4
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_SPACES = re.compile(r"\s+")
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{n}" for p in ("COM", "LPT") for n in range(1, 10)}
# The import name is "<folder>_<YYYYMMDD-HHMMSS>_<stem>.jpg"; folders never contain "_".
_IMPORT_NAME = re.compile(r"^[^_]+_\d{8}-\d{6}_(?P<stem>.+)$")


def clean(text: str) -> str:
    """Text made safe for a file name on macOS and Windows; spaces become dashes."""
    text = _FORBIDDEN.sub("", text)
    text = _SPACES.sub("-", text.strip()).strip(". -")
    if text.upper() in _RESERVED:
        text = f"{text}-"
    return text


def original_stem(name: str, original: str = "") -> str:
    """The camera's file name without extension, e.g. ``DSC_0001``."""
    if original:
        return original
    stem = name.rsplit(".", 1)[0]
    match = _IMPORT_NAME.match(stem)
    return match.group("stem") if match else stem


def sequence(event: Profile, seq: int) -> str:
    """The photo's number in the event (1-based ``seq``), counted from the start number."""
    return f"{max(0, event.start_number) + max(1, seq) - 1:0{SEQUENCE_DIGITS}d}"


def output_stem(event: Profile, import_name: str, original: str, captured: str, seq: int) -> str:
    """The processed file name, without extension and before any collision suffix."""
    filename = clean(original_stem(import_name, original)) or "photo"
    custom = clean(event.custom_name) or clean(event.name) or "photo"
    naming = event.file_naming
    if naming == "custom_seq":
        return f"{custom}-{sequence(event, seq)}"
    if naming == "custom":
        return custom
    if naming == "date_filename":
        try:
            day = datetime.fromisoformat(captured).strftime("%Y%m%d")
        except ValueError:
            return filename
        return f"{day}-{filename}"
    if naming == "filename":
        return filename
    if naming == "filename_seq":
        return f"{filename}-{sequence(event, seq)}"
    return import_name.rsplit(".", 1)[0]  # "import": keep the import name


def unique_name(stem: str, taken: Callable[[str], bool]) -> str:
    """``<stem>.jpg``, or ``<stem>-2.jpg``, ``-3``… when the name is taken."""
    name, n = f"{stem}.jpg", 2
    while taken(name):
        name = f"{stem}-{n}.jpg"
        n += 1
    return name


def example(event: Profile) -> str:
    """What the first photo of ``event`` would be called, for the editor."""
    sample = f"{event.folder or slugify(event.name)}_20261111-143000_DSC_0001.jpg"
    return f"{output_stem(event, sample, 'DSC_0001', '2026-11-11T14:30:00', 1)}.jpg"
