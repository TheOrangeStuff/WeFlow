from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from WeFlow.core import secrets, uploaders
from WeFlow.core.config import Profile
from WeFlow.core.uploaders import FakeUploader, SmugMugUploader, UploadError


class FakeResponse:
    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status_code = status
        self._body = body or {}

    def json(self) -> dict[str, Any]:
        return self._body


class FakeSession:
    """Stands in for the OAuth session, so no test reaches SmugMug."""

    def __init__(self, post: FakeResponse, get: FakeResponse | None = None) -> None:
        self.post_response, self.get_response = post, get
        self.posts: list[dict[str, Any]] = []
        self.gets: list[str] = []

    def post(self, url: str, data: Any, headers: dict[str, str], timeout: Any) -> FakeResponse:
        self.posts.append({"url": url, "headers": headers, "body": data.read()})
        return self.post_response

    def get(self, url: str, headers: dict[str, str], timeout: Any) -> FakeResponse:
        self.gets.append(url)
        assert self.get_response is not None
        return self.get_response


def smugmug(monkeypatch: pytest.MonkeyPatch, session: FakeSession) -> SmugMugUploader:
    uploader = SmugMugUploader()
    monkeypatch.setattr(uploader, "_session", lambda: session)
    return uploader


@pytest.mark.parametrize(
    ("album", "expected"),
    [
        ("https://me.smugmug.com/Events/Gala/n-Ab12Cd", ("node", "Ab12Cd")),
        ("https://me.smugmug.com/Events/Gala/n-Ab12Cd/", ("node", "Ab12Cd")),
        ("/api/v2/album/XyZ987", ("uri", "/api/v2/album/XyZ987")),
        ("https://api.smugmug.com/api/v2/album/XyZ987", ("uri", "/api/v2/album/XyZ987")),
        ("XyZ987", ("uri", "/api/v2/album/XyZ987")),
    ],
)
def test_fr30_album_field_forms(album: str, expected: tuple[str, str]) -> None:
    assert uploaders.smugmug_album_ref(album) == expected


def test_fr30_upload_headers_and_node_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    photo = tmp_path / "Gala_20260516-183007_IMG_0001.jpg"
    photo.write_bytes(b"jpeg-bytes")
    node = {"Response": {"Node": {"Uris": {"Album": {"Uri": "/api/v2/album/RESOLVED"}}}}}
    session = FakeSession(
        FakeResponse(200, {"stat": "ok", "Image": {"ImageUri": "/api/v2/image/abc-0"}}),
        FakeResponse(200, node),
    )
    uploader = smugmug(monkeypatch, session)
    event = Profile(
        name="Gala",
        destination="smugmug",
        album="https://x.smugmug.com/Gala/n-Ab12Cd",
        keywords="gala, 2026",
    )
    assert uploader.upload(photo, event) == "/api/v2/image/abc-0"
    assert uploader.upload(photo, event) == "/api/v2/image/abc-0"
    assert session.gets == ["https://api.smugmug.com/api/v2/node/Ab12Cd"]  # resolved once
    sent = session.posts[0]
    assert sent["url"] == "https://upload.smugmug.com/" and sent["body"] == b"jpeg-bytes"
    headers = sent["headers"]
    assert headers["X-Smug-AlbumUri"] == "/api/v2/album/RESOLVED"
    assert headers["X-Smug-Version"] == "v2" and headers["X-Smug-ResponseType"] == "JSON"
    assert headers["X-Smug-FileName"] == photo.name and headers["X-Smug-Title"] == photo.stem
    assert headers["X-Smug-Keywords"] == "gala; 2026"
    uploader.add_to_album("/api/v2/image/abc-0", event)  # nothing to do, never raises


def test_fr30_keywords_header_only_when_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"x")
    session = FakeSession(FakeResponse(200, {"stat": "ok", "Image": {"ImageUri": "/i/1"}}))
    smugmug(monkeypatch, session).upload(photo, Profile(name="G", album="KEY"))
    assert "X-Smug-Keywords" not in session.posts[0]["headers"]


@pytest.mark.parametrize(
    ("status", "permanent"), [(401, True), (403, True), (404, True), (429, False), (503, False)]
)
def test_fr33_smugmug_error_kinds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int, permanent: bool
) -> None:
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"x")
    uploader = smugmug(monkeypatch, FakeSession(FakeResponse(status)))
    with pytest.raises(UploadError) as caught:
        uploader.upload(photo, Profile(name="G", album="KEY"))
    assert caught.value.permanent is permanent
    assert caught.value.message.count(".") <= 2 and "Traceback" not in caught.value.message


def test_fr37_not_connected_is_permanent_and_plain(tmp_path: Path) -> None:
    for uploader in (SmugMugUploader(), uploaders.FlickrUploader()):
        with pytest.raises(UploadError) as caught:
            uploader.upload(tmp_path / "a.jpg", Profile(name="G", album="KEY"))
        assert caught.value.permanent and "Connect it in Settings" in caught.value.message


def test_fr37_connection_state_comes_from_keyring(memory_secrets: secrets.MemoryBackend) -> None:
    assert not uploaders.is_connected("flickr")
    secrets.set_secret(secrets.FLICKR_API_KEY, "k")
    secrets.set_secret(secrets.FLICKR_API_SECRET, "s")
    secrets.set_secret(secrets.FLICKR_TOKEN, "t")
    secrets.set_secret(secrets.FLICKR_TOKEN_SECRET, "ts")
    assert uploaders.is_connected("flickr") and uploaders.api_credentials("flickr") == ("k", "s")
    uploaders.disconnect("flickr")
    assert not uploaders.is_connected("flickr")
    assert secrets.FLICKR_TOKEN not in memory_secrets.values
    assert uploaders.api_credentials("flickr") == ("k", "s")  # the API key is kept


def test_fr31_flickr_tags_and_privacy_flags() -> None:
    assert uploaders.flickr_tags(["gala night", "2026"]) == '"gala night" "2026"'
    assert uploaders.FLICKR_PRIVACY_FLAGS["friends+family"] == (0, 1, 1)
    assert uploaders.FLICKR_PRIVACY_FLAGS["public"] == (1, 0, 0)
    assert uploaders.FLICKR_PRIVACY_FLAGS["private"] == (0, 0, 0)


class FakeFlickr:
    """Records calls like flickrapi.FlickrAPI would receive them."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.sets: list[dict[str, Any]] = []
        self.photosets = self

    def upload(self, **kwargs: Any) -> Any:
        import xml.etree.ElementTree as ET

        self.calls.append(("upload", kwargs))
        return ET.fromstring("<rsp stat='ok'><photoid>555</photoid></rsp>")

    def getList(self, **kwargs: Any) -> dict[str, Any]:  # noqa: N802
        self.calls.append(("getList", kwargs))
        return {"photosets": {"pages": 1, "photoset": self.sets}}

    def create(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("create", kwargs))
        self.sets.append({"id": "set-1", "title": {"_content": kwargs["title"]}})
        return {"photoset": {"id": "set-1"}}

    def addPhoto(self, **kwargs: Any) -> dict[str, Any]:  # noqa: N802
        self.calls.append(("addPhoto", kwargs))
        return {"stat": "ok"}


def test_fr31_flickr_upload_then_album_created_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeFlickr()
    monkeypatch.setattr(uploaders, "_flickr_client", lambda with_token: client)
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"x")
    event = Profile(
        name="Gala",
        destination="flickr",
        album="Gala Night",
        keywords="gala night",
        flickr_privacy="family",
    )
    uploader = uploaders.FlickrUploader()
    assert uploader.upload(photo, event) == "555"
    upload = client.calls[0][1]
    assert (upload["is_public"], upload["is_friend"], upload["is_family"]) == (0, 0, 1)
    assert upload["tags"] == '"gala night"' and upload["title"] == "a"
    uploader.add_to_album("555", event)
    uploader.add_to_album("556", event)
    uploader.add_to_album("557", event)
    names = [name for name, _ in client.calls[1:]]
    assert names == ["getList", "create", "addPhoto", "addPhoto"]
    assert client.calls[2][1] == {"title": "Gala Night", "primary_photo_id": "555"}
    assert client.calls[3][1] == {"photoset_id": "set-1", "photo_id": "556"}


def test_fake_uploader_contract(tmp_path: Path) -> None:
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"x")
    fake = FakeUploader(fail_times=2, permanent=False)
    event = Profile(name="G")
    for _ in range(2):
        with pytest.raises(UploadError) as caught:
            fake.upload(photo, event)
        assert not caught.value.permanent
    assert fake.upload(photo, event) == "fake-a.jpg"
    with pytest.raises(UploadError) as caught:
        FakeUploader(fail_times=1, permanent=True).upload(photo, event)
    assert caught.value.permanent
