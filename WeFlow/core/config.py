"""Settings, events (profiles) and their on-disk store."""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import date
from pathlib import Path
from typing import Any

from . import secrets

APP_NAME = "WeFlow"
DESTINATIONS = ("none", "smugmug", "flickr")
FLICKR_PRIVACY = ("private", "friends", "family", "friends+family", "public")
WATERMARK_POSITIONS = ("tl", "tr", "center", "bl", "br")
# How processed copies are named (Lightroom-style). "import" keeps the import name (FR-13).
FILE_NAMING = (
    "import",
    "custom_seq",
    "custom",
    "date_filename",
    "filename",
    "filename_seq",
)
FILE_SIZE_KB_MIN, FILE_SIZE_KB_MAX = 10, 100_000
# The event settings an export preset holds.
EXPORT_FIELDS = (
    "file_naming",
    "custom_name",
    "start_number",
    "jpeg_quality",
    "limit_file_size",
    "file_size_kb",
)

# Secret-bearing keys a v0.1 settings.json may contain -> keyring names.
LEGACY_SECRET_KEYS = {
    "smugmug_api_key": secrets.SMUGMUG_API_KEY,
    "smugmug_api_secret": secrets.SMUGMUG_API_SECRET,
    "smugmug_token": secrets.SMUGMUG_TOKEN,
    "smugmug_token_secret": secrets.SMUGMUG_TOKEN_SECRET,
    "flickr_api_key": secrets.FLICKR_API_KEY,
    "flickr_api_secret": secrets.FLICKR_API_SECRET,
    "flickr_token": secrets.FLICKR_TOKEN,
    "flickr_token_secret": secrets.FLICKR_TOKEN_SECRET,
}


def app_data_dir() -> Path:
    """Per-user data folder. ``WeFlow_DATA_DIR`` overrides it (tests, portable use)."""
    override = os.environ.get("WeFlow_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / APP_NAME


def default_library() -> Path:
    return Path.home() / "Pictures" / APP_NAME


def slugify(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")
    return slug or "event"


def default_event_name(today: date | None = None) -> str:
    return f"{(today or date.today()).isoformat()} New Event"


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".tmp-{path.name}")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


@dataclass
class Settings:
    library: str = ""
    active_event_id: str = ""
    paused: bool = False
    card_sound: bool = True
    start_at_login: bool = False
    first_run_done: bool = False
    sample_photo: str = ""
    check_updates: bool = True
    skipped_update: str = ""
    seen_version: str = ""  # the version whose "What's new" was last shown


@dataclass
class Profile:
    """One event: where photos go and how they should look."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    name: str = ""
    folder: str = ""
    destination: str = "none"
    album: str = ""
    flickr_privacy: str = "private"
    keywords: str = ""
    auto_wb: bool = True
    wb_strength: float = 0.6
    auto_tone: bool = True
    watermark: bool = False
    watermark_path: str = ""
    watermark_position: str = "br"
    watermark_scale: float = 0.18
    watermark_opacity: float = 0.7
    watermark_margin: float = 0.03
    max_long_edge: int = 0
    jpeg_quality: int = 92
    hold_uploads: bool = False
    file_naming: str = "import"
    custom_name: str = ""  # empty: use the event name
    start_number: int = 1
    limit_file_size: bool = False
    file_size_kb: int = 500  # K = 1000 bytes

    def keyword_list(self) -> list[str]:
        return [k.strip() for k in self.keywords.split(",") if k.strip()]

    def validate(self, other_names: set[str] | None = None) -> dict[str, str]:
        """Return ``{field: plain-language problem}``; empty when the event is valid."""
        errors: dict[str, str] = {}
        name = self.name.strip()
        if not name:
            errors["name"] = "Give the event a name."
        elif other_names and name.casefold() in {n.casefold() for n in other_names}:
            errors["name"] = "Another event already uses this name."
        if self.destination not in DESTINATIONS:
            errors["destination"] = "Choose where photos should go."
        elif self.destination == "smugmug" and not self.album.strip():
            errors["album"] = "Paste the SmugMug gallery link."
        elif self.destination == "flickr" and not self.album.strip():
            errors["album"] = "Type the Flickr album title."
        if self.flickr_privacy not in FLICKR_PRIVACY:
            errors["flickr_privacy"] = "Choose who can see the photos."
        if self.watermark:
            if not self.watermark_path.strip():
                errors["watermark_path"] = "Choose a PNG file for the watermark."
            elif not self.watermark_path.lower().endswith(".png"):
                errors["watermark_path"] = "The watermark must be a PNG file."
        if self.watermark_position not in WATERMARK_POSITIONS:
            errors["watermark_position"] = "Choose a corner or the center."
        if not 0 <= self.jpeg_quality <= 100:
            errors["jpeg_quality"] = "Quality must be between 0 and 100."
        if self.file_naming not in FILE_NAMING:
            errors["file_naming"] = "Choose how files should be named."
        if self.start_number < 0:
            errors["start_number"] = "Start at 0 or higher."
        if self.limit_file_size and not (FILE_SIZE_KB_MIN <= self.file_size_kb <= FILE_SIZE_KB_MAX):
            errors["file_size_kb"] = (
                f"Use a size between {FILE_SIZE_KB_MIN} K and {FILE_SIZE_KB_MAX:,} K."
            )
        if self.max_long_edge < 0:
            errors["max_long_edge"] = "Use 0 for full size, or a size in pixels."
        return errors


@dataclass
class ExportPreset:
    """Saved export settings (naming, quality, size limit) to reuse across events."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    name: str = ""
    file_naming: str = "import"
    custom_name: str = ""
    start_number: int = 1
    jpeg_quality: int = 92
    limit_file_size: bool = False
    file_size_kb: int = 500

    @classmethod
    def from_event(cls, name: str, event: Profile) -> ExportPreset:
        return cls(name=name, **{f: getattr(event, f) for f in EXPORT_FIELDS})

    def apply(self, event: Profile) -> Profile:
        """A copy of ``event`` with this preset's export settings."""
        return replace(event, **{f: getattr(self, f) for f in EXPORT_FIELDS})

    def matches(self, event: Profile) -> bool:
        return all(getattr(self, f) == getattr(event, f) for f in EXPORT_FIELDS)


def _from_dict(cls: type[Any], data: dict[str, Any]) -> Any:
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


class Store:
    """Settings and events, kept in memory and written atomically to app-data.

    Mutations update memory at once and hand the file write to ``saver`` so a UI
    can keep disk I/O off its thread. The default saver writes inline.
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or app_data_dir()
        self.settings = Settings()
        self._events: dict[str, Profile] = {}
        self._presets: dict[str, ExportPreset] = {}
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self.saver: Callable[[Callable[[], None]], object] = lambda job: job()

    # ---------------------------------------------------------------- files
    @property
    def settings_path(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def events_path(self) -> Path:
        return self.data_dir / "events.json"

    @property
    def presets_path(self) -> Path:
        return self.data_dir / "presets.json"

    def load(self) -> None:
        """Read the files. Missing or damaged files fall back to defaults."""
        with self._lock:
            raw = _read_json(self.settings_path)
            migrated = False
            if isinstance(raw, dict):
                migrated = _migrate_legacy_secrets(raw)
                self.settings = _from_dict(Settings, raw)
            events = _read_json(self.events_path)
            self._events = {}
            fixed = False
            if isinstance(events, list):
                for item in events:
                    if not isinstance(item, dict):
                        continue
                    profile: Profile = _from_dict(Profile, item)
                    if "id" not in item or not profile.folder:
                        fixed = True  # v0.1 event: give it an id and freeze its folder
                        profile.folder = profile.folder or slugify(profile.name)
                    self._events[profile.id] = profile
            if self.settings.active_event_id not in self._events:
                self.settings.active_event_id = ""
            self._presets = {}
            presets = _read_json(self.presets_path)
            if isinstance(presets, list):
                for item in presets:
                    if isinstance(item, dict):
                        with contextlib.suppress(TypeError):
                            preset: ExportPreset = _from_dict(ExportPreset, item)
                            self._presets[preset.id] = preset
        if migrated:
            self.save_settings()
        if fixed:
            self.save_events()

    def save_settings(self) -> None:
        with self._lock:
            text = json.dumps(asdict(self.settings), indent=2)
        self.saver(lambda: self._write(self.settings_path, text))

    def save_events(self) -> None:
        with self._lock:
            text = json.dumps([asdict(e) for e in self._events.values()], indent=2)
        self.saver(lambda: self._write(self.events_path, text))

    def save_presets(self) -> None:
        with self._lock:
            text = json.dumps([asdict(p) for p in self._presets.values()], indent=2)
        self.saver(lambda: self._write(self.presets_path, text))

    def _write(self, path: Path, text: str) -> None:
        with self._write_lock:
            atomic_write_text(path, text)

    # -------------------------------------------------------------- library
    @property
    def library(self) -> Path:
        with self._lock:
            return Path(self.settings.library) if self.settings.library else default_library()

    def event_root(self, event: Profile) -> Path:
        return self.library / event.folder

    def update_settings(self, **changes: Any) -> None:
        with self._lock:
            self.settings = replace(self.settings, **changes)
        self.save_settings()

    # --------------------------------------------------------------- events
    def events(self) -> list[Profile]:
        with self._lock:
            return [replace(e) for e in self._events.values()]

    def get(self, event_id: str) -> Profile | None:
        with self._lock:
            found = self._events.get(event_id)
            return replace(found) if found else None

    def active(self) -> Profile | None:
        with self._lock:
            return self.get(self.settings.active_event_id)

    def set_active(self, event_id: str) -> None:
        with self._lock:
            if event_id and event_id not in self._events:
                raise KeyError(event_id)
            self.settings.active_event_id = event_id
        self.save_settings()

    def names(self, exclude_id: str = "") -> set[str]:
        with self._lock:
            return {e.name for e in self._events.values() if e.id != exclude_id}

    def unique_name(self, wanted: str, exclude_id: str = "") -> str:
        taken = {n.casefold() for n in self.names(exclude_id)}
        if wanted.casefold() not in taken:
            return wanted
        n = 2
        while f"{wanted} {n}".casefold() in taken:
            n += 1
        return f"{wanted} {n}"

    def _unique_folder(self, wanted: str) -> str:
        taken = {e.folder.casefold() for e in self._events.values()}
        folder, n = wanted, 2
        while folder.casefold() in taken:
            folder = f"{wanted}-{n}"
            n += 1
        return folder

    def draft_event(self, today: date | None = None) -> Profile:
        """FR-03: an unsaved event copying the active event's settings."""
        with self._lock:
            base = self.active() or Profile()
            return replace(
                base,
                id=uuid.uuid4().hex,
                name=self.unique_name(default_event_name(today)),
                folder="",
            )

    def add_event(self, event: Profile, activate: bool = True) -> Profile:
        """Save a new event. Its folder is derived from the name once, here."""
        with self._lock:
            if event.id in self._events:
                raise ValueError("event already exists")
            saved = replace(event, name=self.unique_name(event.name.strip()))
            saved.folder = self._unique_folder(slugify(saved.name))
            self._events[saved.id] = saved
            if activate:
                self.settings.active_event_id = saved.id
        self.save_events()
        if activate:
            self.save_settings()
        return replace(saved)

    def create_event(self, name: str | None = None, activate: bool = True) -> Profile:
        draft = self.draft_event()
        if name:
            draft.name = name
        return self.add_event(draft, activate=activate)

    def update_event(self, event: Profile) -> Profile:
        """Save edits. ``id`` and ``folder`` never change after creation."""
        with self._lock:
            current = self._events[event.id]
            saved = replace(event, folder=current.folder)
            saved.name = self.unique_name(saved.name.strip(), exclude_id=event.id)
            self._events[event.id] = saved
        self.save_events()
        return replace(saved)

    def duplicate_event(self, event_id: str) -> Profile:
        with self._lock:
            source = self._events[event_id]
            copy = replace(source, id=uuid.uuid4().hex, name=f"{source.name} copy", folder="")
        return self.add_event(copy, activate=False)

    def delete_event(self, event_id: str) -> None:
        """FR-01: forget the event. Its photos stay in the library untouched."""
        with self._lock:
            self._events.pop(event_id, None)
            was_active = self.settings.active_event_id == event_id
            if was_active:
                self.settings.active_event_id = ""
        self.save_events()
        if was_active:
            self.save_settings()

    # -------------------------------------------------------------- presets
    def presets(self) -> list[ExportPreset]:
        """Export presets, sorted by name."""
        with self._lock:
            found = [replace(p) for p in self._presets.values()]
        return sorted(found, key=lambda p: p.name.casefold())

    def save_preset(self, name: str, event: Profile) -> ExportPreset:
        """Save ``event``'s export settings as a preset; a preset of the same name is replaced."""
        name = name.strip()
        if not name:
            raise ValueError("a preset needs a name")
        with self._lock:
            same = next(
                (p for p in self._presets.values() if p.name.casefold() == name.casefold()), None
            )
            preset = ExportPreset.from_event(name, event)
            if same is not None:
                preset.id = same.id
            self._presets[preset.id] = preset
        self.save_presets()
        return replace(preset)

    def delete_preset(self, preset_id: str) -> None:
        with self._lock:
            self._presets.pop(preset_id, None)
        self.save_presets()


def _read_json(path: Path) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _migrate_legacy_secrets(raw: dict[str, Any]) -> bool:
    """Move v0.1 plain-text secrets into the keyring and blank them (§4.4)."""
    moved = False
    for key, secret_name in LEGACY_SECRET_KEYS.items():
        value = raw.get(key)
        if isinstance(value, str) and value:
            with contextlib.suppress(secrets.SecretsError):
                secrets.set_secret(secret_name, value)
                moved = True
        if key in raw:
            raw.pop(key)
            moved = True
    return moved
