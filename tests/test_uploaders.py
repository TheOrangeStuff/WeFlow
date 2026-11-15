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


class SortingFlickr(FakeFlickr):
    """An existing album, two pages of photos out of date order."""

    def __init__(self) -> None:
        super().__init__()
        self.sets = [{"id": "set-9", "title": {"_content": "Gala Night"}}]
        self.pages = [
            [
                {"id": "3", "datetaken": "2026-11-15 20:00:00"},
                {"id": "1", "datetaken": "2026-11-15 18:00:00"},
            ],
            [
                {"id": "2", "datetaken": "2026-11-15 19:00:00"},
                {"id": "4", "datetaken": "2026-11-15 18:00:00"},
            ],
        ]

    def getPhotos(self, **kwargs: Any) -> dict[str, Any]:  # noqa: N802
        self.calls.append(("getPhotos", kwargs))
        page = kwargs["page"]
        return {"photoset": {"pages": len(self.pages), "photo": self.pages[page - 1]}}

    def reorderPhotos(self, **kwargs: Any) -> dict[str, Any]:  # noqa: N802
        self.calls.append(("reorderPhotos", kwargs))
        return {"stat": "ok"}


def test_fr31a_flickr_sorts_whole_album_date_taken_oldest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SortingFlickr()
    monkeypatch.setattr(uploaders, "_flickr_client", lambda with_token: client)
    uploaders.FlickrUploader().sort_album(Profile(name="G", album="Gala Night"))
    assert client.calls[1][1]["extras"] == "date_taken"
    name, args = client.calls[-1]
    # Ties keep the album's order (1 before 4).
    assert name == "reorderPhotos" and args == {"photoset_id": "set-9", "photo_ids": "1,4,2,3"}


def test_fr31a_flickr_sort_skips_album_already_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SortingFlickr()
    client.pages = [[{"id": "1", "datetaken": "2026-01-01 00:00:00"}]]
    monkeypatch.setattr(uploaders, "_flickr_client", lambda with_token: client)
    uploaders.FlickrUploader().sort_album(Profile(name="G", album="Gala Night"))
    assert "reorderPhotos" not in [name for name, _ in client.calls]


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


# ----------------------------------------------------------- FR-39: pick a gallery
class ListingSession:
    """Answers SmugMug's authuser and paged album-list calls."""

    def __init__(
        self, pages: list[list[dict[str, Any]]], status: int = 200, total: bool = True
    ) -> None:
        self.pages, self.status, self.total = pages, status, total
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def get(
        self, url: str, params: dict[str, Any] | None, headers: dict[str, str], timeout: Any
    ) -> FakeResponse:
        self.calls.append((url, params))
        if url.endswith("!authuser"):
            uris = {"UserAlbums": {"Uri": "/api/v2/user/me!albums"}}
            return FakeResponse(self.status, {"Response": {"User": {"Uris": uris}}})
        index = (int(params["start"]) - 1) // uploaders.SMUGMUG_PAGE if params else 0
        more = index + 1 < len(self.pages)
        pages: dict[str, Any] = {
            "Count": uploaders.SMUGMUG_PAGE,
            "NextPage": "next" if more else None,
        }
        if self.total:
            pages["Total"] = (len(self.pages) - 1) * uploaders.SMUGMUG_PAGE + 1
        body = {"Response": {"Album": self.pages[index], "Pages": pages}}
        return FakeResponse(self.status, body)


def gallery(key: str, name: str, date: str, folder: str = "Events") -> dict[str, Any]:
    return {
        "Uri": f"/api/v2/album/{key}",
        "Name": name,
        "NodeID": f"N{key}",
        "UrlPath": f"/{folder}/{name.replace(' ', '-')}",
        "Date": date,
    }


@pytest.mark.parametrize("total", [True, False], ids=["all-at-once", "page-by-page"])
def test_fr39_smugmug_lists_every_page_newest_first(
    monkeypatch: pytest.MonkeyPatch, total: bool
) -> None:
    session = ListingSession(
        [
            [gallery("A1", "Old Gala", "2025-01-01T00:00:00+00:00")],
            [gallery("B2", "Smith Wedding", "2026-10-01T00:00:00+00:00", "Weddings")],
        ],
        total=total,
    )
    uploader = SmugMugUploader()
    monkeypatch.setattr(uploader, "_session", lambda: session)
    choices = uploader.list_albums()
    assert [c.name for c in choices] == ["Smith Wedding", "Old Gala"]
    assert choices[0] == uploaders.AlbumChoice(
        "/api/v2/album/B2", "Smith Wedding", "NB2", "Weddings"
    )
    starts = [params["start"] for _, params in session.calls[1:] if params]
    assert starts == [1, 1 + uploaders.SMUGMUG_PAGE]


def test_fr39_smugmug_lists_past_2000_galleries(monkeypatch: pytest.MonkeyPatch) -> None:
    """SmugMug lists oldest first: the newest gallery sits on the last page."""
    pages = [[gallery(f"A{i}", f"Old {i}", "2020-01-01T00:00:00+00:00")] for i in range(25)]
    pages.append([gallery("NEW", "Fun Run", "2026-11-13T00:00:00+00:00")])
    uploader = SmugMugUploader()
    monkeypatch.setattr(uploader, "_session", lambda: ListingSession(pages))
    choices = uploader.list_albums()
    assert choices[0].name == "Fun Run"
    assert len(choices) == 26


def test_fr39_smugmug_list_expired_sign_in(monkeypatch: pytest.MonkeyPatch) -> None:
    uploader = SmugMugUploader()
    monkeypatch.setattr(uploader, "_session", lambda: ListingSession([], status=401))
    with pytest.raises(UploadError, match="Reconnect it in Settings"):
        uploader.list_albums()


def test_fr39_flickr_lists_album_titles_newest_first(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeFlickr()
    client.sets = [
        {"id": "1", "title": {"_content": "Old Party"}, "date_create": "1600000000"},
        {"id": "2", "title": {"_content": "Gala Night"}, "date_create": "1790000000"},
        {"id": "3", "title": {"_content": "No Date"}},
    ]
    monkeypatch.setattr(uploaders, "_flickr_client", lambda with_token: client)
    choices = uploaders.FlickrUploader().list_albums()
    assert [c.name for c in choices] == ["Gala Night", "Old Party", "No Date"]
    assert choices[0] == uploaders.AlbumChoice("Gala Night", "Gala Night")


@pytest.mark.parametrize(
    "album",
    [
        "/api/v2/album/B2",
        "https://api.smugmug.com/api/v2/album/B2",
        "https://me.smugmug.com/Weddings/Smith-Wedding/n-NB2",
    ],
)
def test_fr39_match_album_forms(album: str) -> None:
    wedding = uploaders.AlbumChoice("/api/v2/album/B2", "Smith Wedding", "NB2")
    other = uploaders.AlbumChoice("/api/v2/album/A1", "Old Gala", "NA1")
    assert uploaders.match_album([other, wedding], album) == wedding
    assert uploaders.match_album([other], album) is None


class CreatingSession:
    """Answers authuser and the new-gallery POST; the first ``taken`` POSTs get 409."""

    def __init__(self, taken: int = 0, status: int = 201) -> None:
        self.taken, self.status = taken, status
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def get(
        self, url: str, params: dict[str, Any] | None, headers: dict[str, str], timeout: Any
    ) -> FakeResponse:
        uris = {"Node": {"Uri": "/api/v2/node/ROOT"}}
        return FakeResponse(200, {"Response": {"User": {"Uris": uris}}})

    def post(
        self, url: str, json: dict[str, Any], headers: dict[str, str], timeout: Any
    ) -> FakeResponse:
        self.posts.append((url, json))
        if len(self.posts) <= self.taken:
            return FakeResponse(409)
        node = {
            "Name": json["Name"],
            "NodeID": "NEW1",
            "UrlPath": f"/{json['UrlName']}",
            "Uris": {"Album": {"Uri": "/api/v2/album/NEW1"}},
        }
        return FakeResponse(self.status, {"Response": {"Node": node}})


@pytest.mark.parametrize(
    ("name", "url_name"),
    [
        ("smith wedding 2026", "Smith-wedding-2026"),
        ("2026 Gala!", "Gallery-2026-Gala"),
        ("é", "Gallery"),
    ],
)
def test_fr41_smugmug_url_name(name: str, url_name: str) -> None:
    assert uploaders.smugmug_url_name(name) == url_name


def test_fr41_smugmug_creates_unlisted_gallery(monkeypatch: pytest.MonkeyPatch) -> None:
    session = CreatingSession(taken=1)  # the first address is in use
    uploader = SmugMugUploader()
    monkeypatch.setattr(uploader, "_session", lambda: session)
    choice = uploader.create_album("  Smith Wedding ")
    assert choice == uploaders.AlbumChoice("/api/v2/album/NEW1", "Smith Wedding", "NEW1", "")
    assert [url for url, _ in session.posts] == [
        "https://api.smugmug.com/api/v2/node/ROOT!children"
    ] * 2
    assert [body["UrlName"] for _, body in session.posts] == ["Smith-Wedding", "Smith-Wedding-2"]
    assert session.posts[0][1]["Type"] == "Album"
    assert session.posts[0][1]["Privacy"] == "Unlisted"


def test_fr41_smugmug_create_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    uploader = SmugMugUploader()
    monkeypatch.setattr(uploader, "_session", lambda: CreatingSession(status=403))
    with pytest.raises(UploadError, match="Reconnect it in Settings"):
        uploader.create_album("Gala")


def test_fr41_flickr_new_album_needs_no_call(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_client(with_token: bool) -> Any:
        raise AssertionError("Flickr was called")

    monkeypatch.setattr(uploaders, "_flickr_client", no_client)
    assert uploaders.FlickrUploader().create_album(" Gala ") == uploaders.AlbumChoice(
        "Gala", "Gala"
    )


def test_nfr10_smugmug_failures_are_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import requests

    class Unreachable:
        def get(self, url: str, **kwargs: Any) -> FakeResponse:
            raise requests.ReadTimeout("read timed out")

    uploader = SmugMugUploader()
    monkeypatch.setattr(uploader, "_session", lambda: Unreachable())
    with caplog.at_level("INFO", logger="WeFlow"), pytest.raises(UploadError, match="Can't reach"):
        uploader.list_albums()
    assert "SmugMug GET /api/v2!authuser failed" in caplog.text
    assert "ReadTimeout: read timed out" in caplog.text


def test_nfr10_recent_log_is_capped_at_whole_lines(tmp_path: Path) -> None:
    from WeFlow.core import logs

    folder = logs.log_folder(tmp_path)
    folder.mkdir()
    (folder / "weflow.log.1").write_text("old line\n")
    (folder / "weflow.log").write_text("".join(f"line {i}\n" for i in range(1000)))
    text = logs.recent_log(tmp_path, limit=100)
    assert len(text) <= 100 and text.startswith("line ") and text.endswith("line 999\n")
    assert "old line" in logs.recent_log(tmp_path)
