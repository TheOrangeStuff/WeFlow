"""SmugMug and Flickr uploaders, their connect flows, and a fake for tests.

Uploading and adding to an album are separate calls (FR-32) so a photo that is
already up is never sent again.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlparse

from . import secrets
from .config import Profile

SMUGMUG_API = "https://api.smugmug.com"
SMUGMUG_UPLOAD = "https://upload.smugmug.com/"
SMUGMUG_REQUEST_TOKEN = f"{SMUGMUG_API}/services/oauth/1.0a/getRequestToken"
SMUGMUG_AUTHORIZE = f"{SMUGMUG_API}/services/oauth/1.0a/authorize"
SMUGMUG_ACCESS_TOKEN = f"{SMUGMUG_API}/services/oauth/1.0a/getAccessToken"
TIMEOUT = (15, 300)
SERVICE_NAMES = {"smugmug": "SmugMug", "flickr": "Flickr", "none": "this computer"}


class UploadError(Exception):
    """An upload step failed. ``permanent`` errors are not retried (FR-33)."""

    def __init__(self, message: str, permanent: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.permanent = permanent


class Uploader(Protocol):
    def upload(self, path: Path, event: Profile) -> str:
        """Send the file; return the service's id for it."""
        ...

    def add_to_album(self, remote_id: str, event: Profile) -> None:
        """Put an uploaded photo in the event's album. Safe to repeat."""
        ...


def service_name(destination: str) -> str:
    return SERVICE_NAMES.get(destination, destination)


def _not_connected(service: str) -> UploadError:
    return UploadError(f"{service} is not connected. Connect it in Settings.", permanent=True)


def _expired(service: str) -> UploadError:
    return UploadError(f"{service} sign-in has expired. Reconnect it in Settings.", permanent=True)


def _keychain_locked() -> UploadError:
    return UploadError("The system keychain is locked. Unlock it, then press Retry.", True)


def _offline(service: str) -> UploadError:
    return UploadError(f"Can't reach {service}. WeFlow will keep trying.")


# Where each user applies for their own API key. WeFlow ships with none.
API_KEY_PAGES = {
    "smugmug": "https://api.smugmug.com/api/developer/apply",
    "flickr": "https://www.flickr.com/services/apps/create/apply",
}


def api_credentials(service: str) -> tuple[str, str]:
    """The user's own API key and secret, from the keychain. Nothing is built in."""
    return secrets.get_secret(f"{service}_api_key"), secrets.get_secret(f"{service}_api_secret")


def has_api_keys(service: str) -> bool:
    try:
        return all(api_credentials(service))
    except secrets.SecretsError:
        return False


def is_connected(service: str) -> bool:
    try:
        key, secret = api_credentials(service)
        return bool(key and secret and secrets.get_secret(f"{service}_token"))
    except secrets.SecretsError:
        return False


def disconnect(service: str) -> None:
    secrets.delete_secret(f"{service}_token")
    secrets.delete_secret(f"{service}_token_secret")


# ------------------------------------------------------------------ SmugMug
@dataclass(frozen=True)
class PendingAuth:
    """A started out-of-band authorisation, waiting for the user's code."""

    url: str
    token: str
    token_secret: str
    handle: Any = None


def _smugmug_http_error(status: int) -> UploadError:
    if status in (401, 403):
        return _expired("SmugMug")
    if status == 404:
        return UploadError(
            "SmugMug can't find that gallery. Check the gallery link in the event.", permanent=True
        )
    if status == 429 or status >= 500:
        return UploadError("SmugMug is busy. WeFlow will keep trying.")
    return UploadError(
        "SmugMug refused the upload. Check the gallery in the event.", permanent=True
    )


def smugmug_begin() -> PendingAuth:
    """Step 1 of the OAuth 1.0a out-of-band flow (FR-30)."""
    import requests
    from requests_oauthlib import OAuth1Session

    key, secret = api_credentials("smugmug")
    if not key or not secret:
        raise UploadError("Enter a SmugMug API key first.", permanent=True)
    try:
        session = OAuth1Session(key, client_secret=secret, callback_uri="oob")
        token = session.fetch_request_token(SMUGMUG_REQUEST_TOKEN, timeout=TIMEOUT[0])
        url = session.authorization_url(SMUGMUG_AUTHORIZE, Access="Full", Permissions="Add")
    except requests.RequestException as exc:
        raise _offline("SmugMug") from exc
    except ValueError as exc:  # oauthlib TokenRequestDenied and friends
        raise UploadError(
            "SmugMug did not accept the API key. Check the key and secret.", permanent=True
        ) from exc
    return PendingAuth(url, str(token["oauth_token"]), str(token["oauth_token_secret"]))


def smugmug_finish(pending: PendingAuth, code: str) -> None:
    """Step 2: trade the 6-digit code for an access token and store it."""
    import requests
    from requests_oauthlib import OAuth1Session

    key, secret = api_credentials("smugmug")
    try:
        session = OAuth1Session(
            key,
            client_secret=secret,
            resource_owner_key=pending.token,
            resource_owner_secret=pending.token_secret,
            verifier=code.strip(),
        )
        token = session.fetch_access_token(SMUGMUG_ACCESS_TOKEN, timeout=TIMEOUT[0])
    except requests.RequestException as exc:
        raise _offline("SmugMug") from exc
    except ValueError as exc:
        raise UploadError(
            "SmugMug did not accept that code. Press Connect and try again.", permanent=True
        ) from exc
    secrets.set_secret(secrets.SMUGMUG_TOKEN, str(token["oauth_token"]))
    secrets.set_secret(secrets.SMUGMUG_TOKEN_SECRET, str(token["oauth_token_secret"]))


def smugmug_album_ref(album: str) -> tuple[str, str]:
    """Classify the album field: ``("uri", AlbumUri)`` or ``("node", node id)``."""
    text = album.strip()
    node = re.search(r"/n-([A-Za-z0-9]+)", text)
    if node:
        return "node", node.group(1)
    if "/api/v2/album/" in text:
        path = urlparse(text).path if "://" in text else text
        return "uri", path.rstrip("/")
    key = text.strip("/").rsplit("/", 1)[-1]
    return "uri", f"/api/v2/album/{key}"


class SmugMugUploader:
    def __init__(self) -> None:
        self._albums: dict[str, str] = {}
        self._lock = threading.Lock()
        self._local = threading.local()

    def _session(self) -> Any:
        from requests_oauthlib import OAuth1Session

        try:
            key, secret = api_credentials("smugmug")
            token = secrets.get_secret(secrets.SMUGMUG_TOKEN)
            token_secret = secrets.get_secret(secrets.SMUGMUG_TOKEN_SECRET)
        except secrets.SecretsError as exc:
            raise _keychain_locked() from exc
        if not (key and secret and token and token_secret):
            raise _not_connected("SmugMug")
        cached = getattr(self._local, "session", None)
        if cached is None or cached[0] != (key, token):
            session = OAuth1Session(
                key,
                client_secret=secret,
                resource_owner_key=token,
                resource_owner_secret=token_secret,
            )
            self._local.session = cached = ((key, token), session)
        return cached[1]

    def album_uri(self, album: str) -> str:
        """Resolve the event's album field to an AlbumUri (cached)."""
        import requests

        if not album.strip():
            raise UploadError("This event has no SmugMug gallery. Add one in the event.", True)
        with self._lock:
            if album in self._albums:
                return self._albums[album]
            kind, value = smugmug_album_ref(album)
            if kind == "node":
                try:
                    response = self._session().get(
                        f"{SMUGMUG_API}/api/v2/node/{value}",
                        headers={"Accept": "application/json"},
                        timeout=TIMEOUT[0],
                    )
                except requests.RequestException as exc:
                    raise _offline("SmugMug") from exc
                if response.status_code != 200:
                    raise _smugmug_http_error(response.status_code)
                try:
                    value = str(response.json()["Response"]["Node"]["Uris"]["Album"]["Uri"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise UploadError(
                        "That SmugMug link is a folder, not a gallery. Use a gallery link.", True
                    ) from exc
            self._albums[album] = value
            return value

    def upload(self, path: Path, event: Profile) -> str:
        import requests

        album_uri = self.album_uri(event.album)
        headers = {
            "X-Smug-AlbumUri": album_uri,
            "X-Smug-Version": "v2",
            "X-Smug-ResponseType": "JSON",
            "X-Smug-FileName": quote(path.name),
            "X-Smug-Title": quote(path.stem),
            "Content-Type": "image/jpeg",
        }
        if event.keyword_list():
            headers["X-Smug-Keywords"] = quote("; ".join(event.keyword_list()), safe=";, ")
        session = self._session()
        try:
            with open(path, "rb") as fh:
                response = session.post(SMUGMUG_UPLOAD, data=fh, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise _offline("SmugMug") from exc
        except OSError as exc:
            raise UploadError("The processed photo is missing. Press Retry.", True) from exc
        if response.status_code != 200:
            raise _smugmug_http_error(response.status_code)
        try:
            body = response.json()
        except ValueError as exc:
            raise UploadError(
                "SmugMug gave an unexpected answer. WeFlow will keep trying."
            ) from exc
        if body.get("stat") != "ok":
            code = int(body.get("code", 0) or 0)
            if code in (401, 403):
                raise _expired("SmugMug")
            if code == 404:
                raise _smugmug_http_error(404)
            raise UploadError("SmugMug refused the upload. Check the gallery in the event.", True)
        image = body.get("Image", {})
        return str(image.get("ImageUri") or image.get("URL") or path.name)

    def add_to_album(self, remote_id: str, event: Profile) -> None:
        """SmugMug uploads go straight into the gallery; nothing more to do."""


# -------------------------------------------------------------------- Flickr
FLICKR_PRIVACY_FLAGS = {
    "private": (0, 0, 0),
    "friends": (0, 1, 0),
    "family": (0, 0, 1),
    "friends+family": (0, 1, 1),
    "public": (1, 0, 0),
}


def flickr_tags(keywords: list[str]) -> str:
    """Flickr wants space-separated tags; quote each so multi-word tags survive."""
    return " ".join('"{}"'.format(k.replace('"', "")) for k in keywords)


def _flickr_client(with_token: bool) -> Any:
    import flickrapi  # type: ignore[import-untyped]

    try:
        key, secret = api_credentials("flickr")
        stored = secrets.get_secret(secrets.FLICKR_TOKEN) if with_token else ""
        stored_secret = secrets.get_secret(secrets.FLICKR_TOKEN_SECRET) if with_token else ""
    except secrets.SecretsError as exc:
        raise _keychain_locked() from exc
    if not key or not secret:
        if with_token:
            raise _not_connected("Flickr")
        raise UploadError("Enter a Flickr API key first.", permanent=True)
    token = None
    if with_token:
        if not stored or not stored_secret:
            raise _not_connected("Flickr")
        token = flickrapi.auth.FlickrAccessToken(stored, stored_secret, "write")
    # store_token=False keeps flickrapi from writing tokens to its own plain file.
    return flickrapi.FlickrAPI(key, secret, token=token, store_token=False, format="parsed-json")


def _flickr_error(exc: Exception) -> UploadError:
    import flickrapi
    import requests

    if isinstance(exc, flickrapi.exceptions.FlickrError):
        code = getattr(exc, "code", None)
        if code in (96, 97, 98, 99, 100):
            return _expired("Flickr")
        if code in (105, 106, 0, None) or (isinstance(code, int) and code >= 500):
            return UploadError("Flickr is busy. WeFlow will keep trying.")
        return UploadError("Flickr refused the photo. Check the album in the event.", True)
    if isinstance(exc, requests.RequestException | OSError):
        return _offline("Flickr")
    return UploadError("Flickr gave an unexpected answer. WeFlow will keep trying.")


def flickr_begin() -> PendingAuth:
    """Step 1 of Flickr's out-of-band authorisation with ``perms=write`` (FR-31)."""
    try:
        client = _flickr_client(with_token=False)
        client.get_request_token(oauth_callback="oob")
        url = client.auth_url(perms="write")
    except UploadError:
        raise
    except Exception as exc:
        raise _flickr_error(exc) from exc
    return PendingAuth(str(url), "", "", handle=client)


def flickr_finish(pending: PendingAuth, code: str) -> None:
    try:
        pending.handle.get_access_token(code.strip())
        token = pending.handle.token_cache.token
    except Exception as exc:
        raise UploadError(
            "Flickr did not accept that code. Press Connect and try again.", permanent=True
        ) from exc
    secrets.set_secret(secrets.FLICKR_TOKEN, str(token.token))
    secrets.set_secret(secrets.FLICKR_TOKEN_SECRET, str(token.token_secret))


class FlickrUploader:
    def __init__(self) -> None:
        self._albums: dict[str, str] = {}
        self._lock = threading.Lock()

    def upload(self, path: Path, event: Profile) -> str:
        public, friend, family = FLICKR_PRIVACY_FLAGS.get(event.flickr_privacy, (0, 0, 0))
        client = _flickr_client(with_token=True)
        try:
            response = client.upload(
                filename=str(path),
                title=path.stem,
                tags=flickr_tags(event.keyword_list()),
                is_public=public,
                is_friend=friend,
                is_family=family,
                format="etree",
            )
            photo_id = response.find("photoid").text
        except FileNotFoundError as exc:
            raise UploadError("The processed photo is missing. Press Retry.", True) from exc
        except Exception as exc:
            raise _flickr_error(exc) from exc
        return str(photo_id)

    def add_to_album(self, remote_id: str, event: Profile) -> None:
        import flickrapi

        title = event.album.strip()
        if not title:
            raise UploadError("This event has no Flickr album. Add one in the event.", True)
        client = _flickr_client(with_token=True)
        with self._lock:  # one at a time, so the album is only created once
            try:
                album_id = self._albums.get(title) or self._find_album(client, title)
                if not album_id:
                    created = client.photosets.create(title=title, primary_photo_id=remote_id)
                    self._albums[title] = str(created["photoset"]["id"])
                    return  # the primary photo is already in the new album
                self._albums[title] = album_id
                client.photosets.addPhoto(photoset_id=album_id, photo_id=remote_id)
            except flickrapi.exceptions.FlickrError as exc:
                if getattr(exc, "code", None) == 3:  # "Photo already in set"
                    return
                if getattr(exc, "code", None) == 1:  # "Photoset not found"
                    self._albums.pop(title, None)
                    raise UploadError("Flickr lost track of the album. Press Retry.", True) from exc
                raise _flickr_error(exc) from exc
            except Exception as exc:
                raise _flickr_error(exc) from exc

    @staticmethod
    def _find_album(client: Any, title: str) -> str:
        page = 1
        while True:
            listing = client.photosets.getList(page=page, per_page=500)["photosets"]
            for album in listing.get("photoset", []):
                if album.get("title", {}).get("_content") == title:
                    return str(album["id"])
            if page >= int(listing.get("pages", 1) or 1):
                return ""
            page += 1


# ---------------------------------------------------------------------- fake
class FakeUploader:
    """Stands in for a real service in tests. Never touches the network.

    The first ``fail_times`` upload calls (and the first ``album_fail_times``
    album calls) raise an ``UploadError``; ``permanent`` chooses which kind.
    """

    def __init__(
        self, fail_times: int = 0, permanent: bool = False, album_fail_times: int = 0
    ) -> None:
        self.fail_times = fail_times
        self.album_fail_times = album_fail_times
        self.permanent = permanent
        self.uploads: list[str] = []
        self.album_adds: list[str] = []
        self.upload_calls = 0
        self.album_calls = 0
        self._lock = threading.Lock()

    def upload(self, path: Path, event: Profile) -> str:
        with self._lock:
            self.upload_calls += 1
            if self.upload_calls <= self.fail_times:
                raise UploadError("Fake upload failure.", self.permanent)
            if not path.is_file():
                raise UploadError("The processed photo is missing. Press Retry.", True)
            self.uploads.append(path.name)
            return f"fake-{path.name}"

    def add_to_album(self, remote_id: str, event: Profile) -> None:
        with self._lock:
            self.album_calls += 1
            if self.album_calls <= self.album_fail_times:
                raise UploadError("Fake album failure.", self.permanent)
            self.album_adds.append(remote_id)


def make_uploader(destination: str) -> Uploader:
    if destination == "smugmug":
        return SmugMugUploader()
    if destination == "flickr":
        return FlickrUploader()
    raise UploadError("This event has nowhere to upload to. Choose a destination.", True)
