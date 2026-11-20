from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from WeFlow.core import secrets
from WeFlow.core.config import Profile, Store, default_event_name, slugify


def test_fr01_create_edit_duplicate_delete(store: Store, tmp_path: Path) -> None:
    first = store.create_event("Smith Wedding")
    first.album = "Reception"
    store.update_event(first)
    assert store.get(first.id).album == "Reception"  # type: ignore[union-attr]

    copy = store.duplicate_event(first.id)
    assert copy.id != first.id and copy.album == "Reception"
    assert copy.name == "Smith Wedding copy" and copy.folder != first.folder

    photo = store.event_root(first) / "originals" / "keep.jpg"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"jpeg")
    store.delete_event(first.id)
    assert store.get(first.id) is None
    assert photo.read_bytes() == b"jpeg"  # deleting an event never deletes photos


def test_fr02_exactly_one_active_event(store: Store) -> None:
    a = store.create_event("A")
    b = store.create_event("B")
    assert store.active().id == b.id  # type: ignore[union-attr]
    store.set_active(a.id)
    assert store.active().id == a.id  # type: ignore[union-attr]
    store.delete_event(a.id)
    assert store.active() is None


def test_fr03_new_event_copies_active_settings_and_dates_name(store: Store) -> None:
    base = store.create_event("Base")
    base.destination, base.album, base.watermark_opacity = "flickr", "Gala", 0.4
    store.update_event(base)
    draft = store.draft_event(today=date(2026, 5, 16))
    assert draft.name == "2026-05-16 New Event" == default_event_name(date(2026, 5, 16))
    assert (draft.destination, draft.album, draft.watermark_opacity) == ("flickr", "Gala", 0.4)
    assert draft.id != base.id
    saved = store.add_event(draft)
    assert store.draft_event(today=date(2026, 5, 16)).name == "2026-05-16 New Event 2"
    assert saved.folder == "2026-05-16-New-Event"


def test_event_folder_is_frozen_after_rename(store: Store) -> None:
    event = store.create_event("Spring Gala")
    assert event.folder == slugify("Spring Gala") == "Spring-Gala"
    event.name, event.folder = "Renamed Gala", "something-else"
    saved = store.update_event(event)
    assert saved.name == "Renamed Gala" and saved.folder == "Spring-Gala"


def test_event_names_are_unique(store: Store) -> None:
    store.create_event("Same")
    assert store.create_event("Same").name == "Same 2"
    assert "name" in Profile(name="same").validate(store.names())


def test_validate_plain_language() -> None:
    assert Profile(name="ok").validate() == {}
    errors = Profile(name="", destination="smugmug", watermark=True, jpeg_quality=101).validate()
    assert set(errors) == {"name", "album", "watermark_path", "jpeg_quality"}
    assert "watermark_path" in Profile(name="x", watermark=True, watermark_path="a.jpg").validate()


def test_store_round_trip_and_atomic_files(store: Store) -> None:
    event = store.create_event("Persisted")
    again = Store(store.data_dir)
    again.load()
    assert again.active() == event
    assert not list(store.data_dir.glob(".tmp-*"))


def test_nfr06_legacy_secrets_move_to_keyring(
    tmp_path: Path, memory_secrets: secrets.MemoryBackend
) -> None:
    data = tmp_path / "old"
    data.mkdir()
    (data / "settings.json").write_text(
        json.dumps({"library": "/photos", "smugmug_api_key": "KEY", "flickr_token": "TOK"})
    )
    (data / "events.json").write_text(json.dumps([{"name": "Old Event", "destination": "flickr"}]))
    store = Store(data)
    store.load()
    assert secrets.get_secret(secrets.SMUGMUG_API_KEY) == "KEY"
    assert secrets.get_secret(secrets.FLICKR_TOKEN) == "TOK"
    on_disk = (data / "settings.json").read_text()
    assert "KEY" not in on_disk and "TOK" not in on_disk
    assert store.settings.library == "/photos"
    migrated = store.events()[0]
    assert migrated.id and migrated.folder == "Old-Event"
    assert json.loads((data / "events.json").read_text())[0]["id"] == migrated.id


# What WeFlow 1.0.0 called its keychain entries. An update must find them again.
KEYCHAIN_NAMES_1_0 = {
    "smugmug_api_key",
    "smugmug_api_secret",
    "smugmug_token",
    "smugmug_token_secret",
    "flickr_api_key",
    "flickr_api_secret",
    "flickr_token",
    "flickr_token_secret",
}


def test_update_keychain_names_are_frozen() -> None:
    """Renaming any of these signs every user out on update. Migrate instead."""
    assert secrets.SERVICE == "WeFlow"
    skip = {"SERVICE", "BUNDLE_ENTRY", "SPARE_ENTRY"}
    names = {v for k, v in vars(secrets).items() if k.isupper() and k[0] != "_" and k not in skip}
    assert names == KEYCHAIN_NAMES_1_0


def test_update_keeps_the_user_signed_in(
    tmp_path: Path, memory_secrets: secrets.MemoryBackend
) -> None:
    """Start on the data an older version left behind: nothing has to be entered again."""
    from WeFlow.core import uploaders

    data = tmp_path / "appdata"
    data.mkdir(parents=True)
    old_settings = {  # settings.json as 1.0.0 wrote it: none of the newer fields
        "library": str(tmp_path / "library"),
        "active_event_id": "abc123",
        "paused": False,
        "card_sound": False,
        "start_at_login": True,
        "first_run_done": True,
        "sample_photo": "",
    }
    (data / "settings.json").write_text(json.dumps(old_settings), encoding="utf-8")
    old_event = {"id": "abc123", "name": "Gala", "folder": "Gala", "destination": "smugmug"}
    (data / "events.json").write_text(json.dumps([old_event]), encoding="utf-8")
    before = {name: f"value-of-{name}" for name in KEYCHAIN_NAMES_1_0}
    memory_secrets.values.update(before)

    store = Store(data)
    store.load()
    assert store.settings.first_run_done and store.settings.card_sound is False
    assert store.active() is not None and store.active().name == "Gala"  # type: ignore[union-attr]
    for service in ("smugmug", "flickr"):
        assert uploaders.has_api_keys(service) and uploaders.is_connected(service)
    assert uploaders.api_credentials("smugmug") == (
        "value-of-smugmug_api_key",
        "value-of-smugmug_api_secret",
    )
    store.update_settings(seen_version="9.9.9")  # the new version saves its own settings
    assert memory_secrets.values == before  # and never touches the keychain


def test_fr28_presets_save_apply_replace_delete(store: Store) -> None:
    event = store.create_event("Gala")
    event.file_naming, event.custom_name, event.jpeg_quality = "custom_seq", "", 80
    event.limit_file_size, event.file_size_kb = True, 750
    first = store.save_preset("Web", event)
    assert [p.name for p in store.presets()] == ["Web"]

    fresh = store.create_event("Next")  # a new event can use the preset
    assert not first.matches(fresh)
    applied = first.apply(fresh)
    assert first.matches(applied) and applied.name == "Next" and applied.album == fresh.album
    assert (applied.file_naming, applied.jpeg_quality, applied.file_size_kb) == (
        "custom_seq",
        80,
        750,
    )

    event.jpeg_quality = 70
    again = store.save_preset("web", event)  # same name: replaced, not duplicated
    assert again.id == first.id and [p.jpeg_quality for p in store.presets()] == [70]
    store.save_preset("Archive", Profile(name="x"))
    assert [p.name for p in store.presets()] == ["Archive", "web"]

    reloaded = Store(store.data_dir)
    reloaded.load()
    assert [(p.name, p.jpeg_quality) for p in reloaded.presets()] == [("Archive", 92), ("web", 70)]
    reloaded.delete_preset(again.id)
    assert [p.name for p in reloaded.presets()] == ["Archive"]


def test_fr28_update_reads_older_events_and_presets_files(store: Store) -> None:
    """Events saved by 1.1.0 load with the new export settings at their defaults."""
    store.data_dir.mkdir(parents=True, exist_ok=True)
    old = {"id": "abc", "name": "Old", "folder": "Old", "jpeg_quality": 88}
    (store.data_dir / "events.json").write_text(json.dumps([old]))
    (store.data_dir / "presets.json").write_text('[{"name": "Web", "future_field": 1}, 7]')
    store.load()
    event = store.get("abc")
    assert event is not None and event.jpeg_quality == 88
    assert (event.file_naming, event.limit_file_size, event.start_number) == ("import", False, 1)
    assert [p.name for p in store.presets()] == ["Web"]


def test_nfr06_keychain_one_entry_carries_old_sign_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """1.6.3: the eight old entries move into one, read once per run (fewer macOS prompts)."""
    import keyring

    store: dict[str, str] = {secrets.SMUGMUG_API_KEY: "KEY", secrets.FLICKR_TOKEN: "TOK"}
    reads: list[str] = []

    def get(service: str, name: str) -> str | None:
        reads.append(name)
        return store.get(name)

    def put(service: str, name: str, value: str) -> None:
        store[name] = value

    monkeypatch.setattr(keyring, "get_password", get)
    monkeypatch.setattr(keyring, "set_password", put)
    backend = secrets.KeyringBackend()
    assert backend.get(secrets.SMUGMUG_API_KEY) == "KEY"
    assert backend.get(secrets.FLICKR_TOKEN) == "TOK"
    assert json.loads(store[secrets.BUNDLE_ENTRY])[secrets.FLICKR_TOKEN] == "TOK"

    # A later run reads only the one entry, and only once.
    reads.clear()
    later = secrets.KeyringBackend()
    later.get(secrets.SMUGMUG_API_KEY)
    later.get(secrets.FLICKR_TOKEN)
    assert reads == [secrets.SPARE_ENTRY, secrets.BUNDLE_ENTRY]

    # A new version writes the entry again (so it becomes the entry's creator on macOS).
    store[secrets.BUNDLE_ENTRY] = json.dumps({secrets.FLICKR_TOKEN: "TOK", "_written_by": "0.9"})
    secrets.KeyringBackend().get(secrets.FLICKR_TOKEN)
    assert json.loads(store[secrets.BUNDLE_ENTRY])["_written_by"] != "0.9"


def test_nfr06_refused_keychain_still_connects(monkeypatch: pytest.MonkeyPatch) -> None:
    """macOS refuses the entry an older build made: save to a spare entry instead."""
    import keyring
    from keyring.errors import KeyringError

    store: dict[str, str] = {secrets.BUNDLE_ENTRY: "old", secrets.SMUGMUG_TOKEN: "x"}
    locked = {secrets.BUNDLE_ENTRY, secrets.SMUGMUG_TOKEN}

    def get(service: str, name: str) -> str | None:
        if name in locked:
            raise KeyringError("denied")
        return store.get(name)

    def put(service: str, name: str, value: str) -> None:
        if name in locked:
            raise KeyringError("denied")
        store[name] = value

    monkeypatch.setattr(keyring, "get_password", get)
    monkeypatch.setattr(keyring, "set_password", put)
    secrets.KeyringBackend().set(secrets.SMUGMUG_API_KEY, "KEY")
    assert secrets.KeyringBackend().get(secrets.SMUGMUG_API_KEY) == "KEY"
    assert "KEY" in store[secrets.SPARE_ENTRY]


def test_originals_folder_is_the_event_name_and_never_changes(store: Store) -> None:
    from WeFlow.core.config import originals_folder_name

    event = store.create_event("Smith Wedding: 2026/11")
    assert event.originals_folder == "Smith Wedding 202611"
    event.name = "Renamed"
    assert store.update_event(event).originals_dir == "Smith Wedding 202611"
    assert store.duplicate_event(event.id).originals_dir == "Renamed copy"
    assert originals_folder_name("Processed") == "Processed originals"
    assert originals_folder_name("CON") == "CON originals"
    assert Profile().originals_dir == "originals"  # events made before 1.16.0
