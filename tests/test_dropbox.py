"""FR-46 (1.18.0): Dropbox as a destination. No test reaches Dropbox."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from WeFlow.core import secrets, uploaders
from WeFlow.core.config import Profile
from WeFlow.core.uploaders import DropboxUploader, UploadError

Reply = tuple[int, dict[str, Any]]


class Response:
    def __init__(self, status: int, body: dict[str, Any] | None = None) -> None:
        self.status_code = status
        self._body = body or {}
        self.headers: dict[str, str] = {"Retry-After": "0"}

    def json(self) -> dict[str, Any]:
        return self._body


class Session:
    """Answers each POST by endpoint. ``routes`` maps a path ending to a reply, or to
    a list of replies used in turn."""

    def __init__(self, routes: dict[str, Reply | list[Reply] | Callable[[dict], Reply]]) -> None:
        self.routes = routes
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> Response:
        data = kwargs.get("data")
        call = {
            "url": url,
            "headers": kwargs.get("headers") or {},
            "json": kwargs.get("json"),
            "body": data.read() if hasattr(data, "read") else data,
        }
        self.calls.append(call)
        for end, reply in self.routes.items():
            if url.endswith(end):
                if callable(reply):
                    return Response(*reply(call))
                if isinstance(reply, list):
                    return Response(*(reply.pop(0) if len(reply) > 1 else reply[0]))
                return Response(*reply)
        raise AssertionError(f"unexpected call {url}")

    def to(self, end: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["url"].endswith(end)]


TOKEN: Reply = (200, {"access_token": "access-1", "expires_in": 14400})


@pytest.fixture
def connected(monkeypatch: pytest.MonkeyPatch) -> None:
    secrets.set_secret(secrets.DROPBOX_API_KEY, "key")
    secrets.set_secret(secrets.DROPBOX_API_SECRET, "secret")
    secrets.set_secret(secrets.DROPBOX_TOKEN, "refresh-1")
    monkeypatch.setattr(DropboxUploader, "_tokens", {})
    monkeypatch.setattr(uploaders.time, "sleep", lambda _s: None)


def dropbox(monkeypatch: pytest.MonkeyPatch, session: Session) -> DropboxUploader:
    uploader = DropboxUploader()
    monkeypatch.setattr(uploader, "_session", lambda: session)
    return uploader


@pytest.fixture
def photo(tmp_path: Path) -> Path:
    path = tmp_path / "Gala-001.jpg"
    path.write_bytes(b"jpeg-bytes")
    return path


EVENT = Profile(name="Gala", destination="dropbox", album="Clients/Gala 2026/")


@pytest.mark.parametrize(
    ("album", "folder"),
    [
        ("Clients/Gala", "/Clients/Gala"),
        ("/Clients//Gala/", "/Clients/Gala"),
        ("Clients\\Gala", "/Clients/Gala"),
        ("  ", ""),
    ],
)
def test_fr46_folder_field_becomes_dropbox_path(album: str, folder: str) -> None:
    assert uploaders.dropbox_folder(album) == folder


def test_fr46_content_hash_is_hash_of_block_hashes(tmp_path: Path) -> None:
    path = tmp_path / "big.jpg"
    data = b"a" * uploaders.DROPBOX_HASH_BLOCK + b"b" * 10
    path.write_bytes(data)
    block = uploaders.DROPBOX_HASH_BLOCK
    expected = hashlib.sha256(
        hashlib.sha256(data[:block]).digest() + hashlib.sha256(data[block:]).digest()
    ).hexdigest()
    assert uploaders.dropbox_content_hash(path) == expected


def test_fr46_upload_goes_into_event_folder(
    monkeypatch: pytest.MonkeyPatch, connected: None, photo: Path, caplog: pytest.LogCaptureFixture
) -> None:
    session = Session({"/oauth2/token": TOKEN, "/files/upload": (200, {"id": "id:abc"})})
    uploader = dropbox(monkeypatch, session)
    with caplog.at_level("INFO", logger="WeFlow"):
        assert uploader.upload(photo, EVENT) == "id:abc"
        assert uploader.upload(photo, EVENT) == "id:abc"
    sent = session.to("/files/upload")[0]
    arg = json.loads(sent["headers"]["Dropbox-API-Arg"])
    assert arg["path"] == "/Clients/Gala 2026/Gala-001.jpg"
    assert arg["mode"] == "add" and arg["autorename"] is False  # never overwrites
    assert sent["headers"]["Authorization"] == "Bearer access-1" and sent["body"] == b"jpeg-bytes"
    assert len(session.to("/oauth2/token")) == 1  # the access token is reused
    assert "access-1" not in caplog.text and "refresh-1" not in caplog.text  # NFR-10
    assert "Dropbox POST /2/files/upload -> 200" in caplog.text
    assert "access-1" not in json.dumps(secrets._backend.values)  # type: ignore[attr-defined]


def test_fr46_stale_access_token_is_renewed_once(
    monkeypatch: pytest.MonkeyPatch, connected: None, photo: Path
) -> None:
    session = Session(
        {
            "/oauth2/token": [TOKEN, (200, {"access_token": "access-2", "expires_in": 14400})],
            "/files/upload": [(401, {}), (200, {"id": "id:abc"})],
        }
    )
    assert dropbox(monkeypatch, session).upload(photo, EVENT) == "id:abc"
    assert session.to("/files/upload")[1]["headers"]["Authorization"] == "Bearer access-2"


def test_fr32_dropbox_lost_answer_is_not_uploaded_twice(
    monkeypatch: pytest.MonkeyPatch, connected: None, photo: Path
) -> None:
    same = uploaders.dropbox_content_hash(photo)
    session = Session(
        {
            "/oauth2/token": TOKEN,
            "/files/upload": (409, {"error_summary": "path/conflict/file/.."}),
            "/files/get_metadata": (200, {".tag": "file", "id": "id:up", "content_hash": same}),
        }
    )
    assert dropbox(monkeypatch, session).upload(photo, EVENT) == "id:up"
    assert len(session.to("/files/upload")) == 1


def test_fr46_other_file_with_same_name_is_kept(
    monkeypatch: pytest.MonkeyPatch, connected: None, photo: Path
) -> None:
    session = Session(
        {
            "/oauth2/token": TOKEN,
            "/files/upload": [
                (409, {"error_summary": "path/conflict/file/.."}),
                (200, {"id": "id:new"}),
            ],
            "/files/get_metadata": (200, {".tag": "file", "id": "id:x", "content_hash": "other"}),
        }
    )
    assert dropbox(monkeypatch, session).upload(photo, EVENT) == "id:new"
    second = json.loads(session.to("/files/upload")[1]["headers"]["Dropbox-API-Arg"])
    assert second["mode"] == "add" and second["autorename"] is True


@pytest.mark.parametrize(
    ("reply", "permanent", "busy", "words"),
    [
        ((429, {"error_summary": "too_many_write_operations/"}), False, True, "busy"),
        ((503, {}), False, True, "busy"),
        ((409, {"error_summary": "path/insufficient_space/."}), True, False, "full"),
        ((409, {"error_summary": "path/no_write_permission/."}), True, False, "can't write"),
        ((401, {}), True, False, "Reconnect it in Settings"),
    ],
)
def test_fr33_dropbox_error_kinds(
    monkeypatch: pytest.MonkeyPatch,
    connected: None,
    photo: Path,
    reply: Reply,
    permanent: bool,
    busy: bool,
    words: str,
) -> None:
    session = Session({"/oauth2/token": TOKEN, "/files/upload": reply})
    with pytest.raises(UploadError) as caught:
        dropbox(monkeypatch, session).upload(photo, EVENT)
    assert caught.value.permanent is permanent and caught.value.busy is busy
    assert words in caught.value.message


def test_fr46_write_contention_is_retried_in_place(
    monkeypatch: pytest.MonkeyPatch, connected: None, photo: Path
) -> None:
    busy: Reply = (429, {"error_summary": "too_many_write_operations/"})
    session = Session({"/oauth2/token": TOKEN, "/files/upload": [busy, busy, (200, {"id": "i"})]})
    assert dropbox(monkeypatch, session).upload(photo, EVENT) == "i"


def test_fr37_dropbox_not_connected_or_revoked(
    monkeypatch: pytest.MonkeyPatch, connected: None, photo: Path
) -> None:
    session = Session({"/oauth2/token": (400, {"error": "invalid_grant"})})
    with pytest.raises(UploadError, match="Reconnect it in Settings") as caught:
        dropbox(monkeypatch, session).upload(photo, EVENT)
    assert caught.value.permanent
    uploaders.disconnect("dropbox")
    assert not uploaders.is_connected("dropbox") and uploaders.has_api_keys("dropbox")
    with pytest.raises(UploadError, match="Connect it in Settings"):
        DropboxUploader().upload(photo, EVENT)


def test_fr46_connect_stores_only_refresh_token(
    monkeypatch: pytest.MonkeyPatch, memory_secrets: secrets.MemoryBackend
) -> None:
    import requests

    secrets.set_secret(secrets.DROPBOX_API_KEY, "key")
    secrets.set_secret(secrets.DROPBOX_API_SECRET, "secret")
    pending = uploaders.dropbox_begin()
    assert "client_id=key" in pending.url and "token_access_type=offline" in pending.url
    sent: dict[str, Any] = {}

    def post(url: str, **kwargs: Any) -> Response:
        sent.update(kwargs, url=url)
        return Response(200, {"access_token": "access", "refresh_token": "refresh-9"})

    monkeypatch.setattr(requests, "post", post)
    uploaders.dropbox_finish(pending, " the-code ")
    assert sent["data"]["code"] == "the-code" and sent["auth"] == ("key", "secret")
    assert memory_secrets.values[secrets.DROPBOX_TOKEN] == "refresh-9"
    assert "access" not in memory_secrets.values.values()
    assert uploaders.is_connected("dropbox")


def test_fr39_dropbox_lists_two_levels_a_to_z(
    monkeypatch: pytest.MonkeyPatch, connected: None
) -> None:
    def folders(*paths: str) -> list[dict[str, Any]]:
        return [{".tag": "folder", "path_display": p} for p in paths]

    def listing(call: dict[str, Any]) -> Reply:
        path = call["json"]["path"]
        if path == "":
            entries = folders("/Weddings", "/clients") + [{".tag": "file", "path_display": "/a"}]
            return 200, {"entries": entries, "has_more": False}
        if path == "/clients":
            return 200, {"entries": folders("/clients/Smith"), "has_more": True, "cursor": "c"}
        return 200, {"entries": [], "has_more": False}

    session = Session(
        {
            "/oauth2/token": TOKEN,
            "/list_folder/continue": (200, {"entries": folders("/clients/Adams")}),
            "/files/list_folder": listing,
        }
    )
    names = [c.name for c in dropbox(monkeypatch, session).list_albums()]
    assert names == ["clients", "clients/Adams", "clients/Smith", "Weddings"]


def test_fr41_dropbox_creates_nested_folder_or_picks_existing(
    monkeypatch: pytest.MonkeyPatch, connected: None
) -> None:
    session = Session(
        {
            "/oauth2/token": TOKEN,
            "/files/create_folder_v2": [
                (200, {"metadata": {"path_display": "/Clients/Smith"}}),
                (409, {"error_summary": "path/conflict/folder/."}),
            ],
        }
    )
    uploader = dropbox(monkeypatch, session)
    made = uploader.create_album(" Clients/Smith ")
    assert (made.value, made.name) == ("/Clients/Smith", "Clients/Smith")
    assert session.to("/files/create_folder_v2")[0]["json"]["path"] == "/Clients/Smith"
    assert uploader.create_album("Clients/Smith").value == "/Clients/Smith"


def test_fr46_event_needs_a_folder() -> None:
    errors = Profile(name="G", destination="dropbox").validate()
    assert errors["album"] == "Choose a Dropbox folder or create one."
