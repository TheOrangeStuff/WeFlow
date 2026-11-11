from __future__ import annotations

import json
from datetime import date
from pathlib import Path

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
    errors = Profile(name="", destination="smugmug", watermark=True, jpeg_quality=10).validate()
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
    names = {v for k, v in vars(secrets).items() if k.isupper() and k != "SERVICE"}
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
