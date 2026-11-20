"""SmugMug, Flickr and Dropbox uploaders, their connect flows, and a fake for tests.

Uploading and adding to an album are separate calls (FR-32) so a photo that is
already up is never sent again.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
API_TIMEOUT = (15, 45)  # connect, read: a long gallery page can take a while
log = logging.getLogger("WeFlow.uploaders")  # paths, statuses and timings only: no secrets
SERVICE_NAMES = {
    "smugmug": "SmugMug",
    "flickr": "Flickr",
    "dropbox": "Dropbox",
    "none": "this computer",
}


class UploadError(Exception):
    """An upload step failed. ``permanent`` errors are not retried (FR-33).

    ``busy`` means the service asked us to slow down (1.17.0: fewer uploads at once).
    """

    def __init__(self, message: str, permanent: bool = False, busy: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.permanent = permanent
        self.busy = busy


@dataclass(frozen=True)
class AlbumChoice:
    """One of the user's galleries or albums, offered in the event editor (FR-39).

    ``value`` is what goes in the event's album field; ``node`` is the SmugMug
    node id, so a pasted ``/n-`` link can be matched to its gallery.
    """

    value: str
    name: str
    node: str = ""
    where: str = ""  # SmugMug folder path, to tell same-named galleries apart


MAX_ALBUMS = 50000  # safety bound only: SmugMug lists oldest first, so read every page
SMUGMUG_PAGE = 100
SMUGMUG_PARALLEL = 8  # gallery pages fetched at the same time
SMUGMUG_ALBUM_FIELDS = "Uri,Name,NodeID,UrlPath,Date"  # _filter: skip the rest of each gallery


class Uploader(Protocol):
    def upload(self, path: Path, event: Profile) -> str:
        """Send the file; return the service's id for it."""
        ...

    def add_to_album(self, remote_id: str, event: Profile) -> None:
        """Put an uploaded photo in the event's album. Safe to repeat."""
        ...

    def list_albums(self) -> list[AlbumChoice]:
        """The signed-in user's galleries or albums, newest first (FR-39)."""
        ...

    def create_album(self, name: str) -> AlbumChoice:
        """Make a new gallery or album called ``name`` and return it (FR-41)."""
        ...

    def sort_album(self, event: Profile) -> None:
        """Put the event's album in date-taken order, oldest first (FR-31a). Safe to repeat."""
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
    "dropbox": "https://www.dropbox.com/developers/apps/create",
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
        return UploadError("SmugMug is busy. WeFlow will keep trying.", busy=True)
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


def smugmug_url_name(name: str) -> str:
    """A gallery's web address part: letters and digits joined by ``-``, starting
    with a capital letter, as SmugMug requires."""
    slug = "-".join(re.findall(r"[A-Za-z0-9]+", name))[:60].strip("-")
    if not slug[:1].isalpha():
        slug = f"Gallery-{slug}".strip("-")
    return slug[0].upper() + slug[1:]


SMUGMUG_NEW_PRIVACY = "Unlisted"  # a new gallery is reachable by link, not listed
SMUGMUG_URL_TRIES = 5  # Name-2 … Name-5 when the address is taken


def match_album(choices: list[AlbumChoice], album: str) -> AlbumChoice | None:
    """The listed gallery an album field points at: same value, AlbumUri or node id."""
    text = album.strip()
    if not text:
        return None
    for choice in choices:
        if choice.value == text:
            return choice
    kind, value = smugmug_album_ref(text)
    for choice in choices:
        if (kind == "node" and choice.node == value) or (kind == "uri" and choice.value == value):
            return choice
    return None


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

    def _request(self, method: str, url: str, session: Any = None, **kwargs: Any) -> Any:
        """Send one SmugMug request and log what happened (never headers or bodies).

        ``session`` lets worker threads share one signed-in session, so the keychain
        is read once rather than once per thread."""
        import requests

        where = urlparse(url).path or url
        started = time.monotonic()
        try:
            session = session or self._session()
            call = session.post if method == "POST" else session.get
            response = call(url, **kwargs)
        except requests.RequestException as exc:
            log.warning(
                "SmugMug %s %s failed after %.1fs: %s: %s",
                method,
                where,
                time.monotonic() - started,
                type(exc).__name__,
                exc,
            )
            raise
        log.info(
            "SmugMug %s %s -> %s in %.1fs",
            method,
            where,
            response.status_code,
            time.monotonic() - started,
        )
        return response

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
                    response = self._request(
                        "GET",
                        f"{SMUGMUG_API}/api/v2/node/{value}",
                        headers={"Accept": "application/json"},
                        timeout=API_TIMEOUT,
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
        self._session()  # not signed in: say so before touching the file
        try:
            with open(path, "rb") as fh:
                response = self._request(
                    "POST", SMUGMUG_UPLOAD, data=fh, headers=headers, timeout=TIMEOUT
                )
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

    def _get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        failed: str = "SmugMug did not send the gallery list. Try again.",
        session: Any = None,
    ) -> dict[str, Any]:
        import requests

        try:
            response = self._request(
                "GET",
                f"{SMUGMUG_API}{path}",
                session=session,
                params=params,
                headers={"Accept": "application/json"},
                timeout=API_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise UploadError("Can't reach SmugMug. Check the connection and try again.") from exc
        if response.status_code in (401, 403):
            raise _expired("SmugMug")
        if response.status_code != 200:
            raise UploadError(failed)
        try:
            body = response.json()["Response"]
        except (KeyError, TypeError, ValueError) as exc:
            raise UploadError(failed) from exc
        return dict(body)

    def list_albums(self) -> list[AlbumChoice]:
        """Every gallery of the signed-in user, newest first (FR-39)."""
        try:
            path = str(self._get_json("/api/v2!authuser")["User"]["Uris"]["UserAlbums"]["Uri"])
        except (KeyError, TypeError) as exc:
            raise UploadError("SmugMug did not send the gallery list. Try again.") from exc
        started = time.monotonic()
        found: list[tuple[str, AlbumChoice]] = []
        session = self._session()

        def page(start: int) -> dict[str, Any]:
            params = {"start": start, "count": SMUGMUG_PAGE, "_filter": SMUGMUG_ALBUM_FIELDS}
            return self._get_json(path, params, session=session)

        def take(body: dict[str, Any]) -> None:
            for album in body.get("Album") or []:
                uri, name = album.get("Uri"), str(album.get("Name") or "").strip()
                if not uri or not name:
                    continue
                folder = str(album.get("UrlPath") or "").rsplit("/", 1)[0].strip("/")
                choice = AlbumChoice(str(uri), name, str(album.get("NodeID") or ""), folder)
                found.append((str(album.get("Date") or ""), choice))

        first = page(1)
        take(first)
        pages = first.get("Pages") or {}
        total = int(pages.get("Total") or 0)
        if total:
            # Each page can take many seconds: ask for the rest all at once.
            starts = list(range(1 + SMUGMUG_PAGE, min(total, MAX_ALBUMS) + 1, SMUGMUG_PAGE))
            with ThreadPoolExecutor(max_workers=SMUGMUG_PARALLEL) as pool:
                for body in pool.map(page, starts):
                    take(body)
        else:
            start = 1
            while pages.get("NextPage") and len(found) < MAX_ALBUMS:
                start += int(pages.get("Count") or SMUGMUG_PAGE)
                body = page(start)
                take(body)
                pages = body.get("Pages") or {}
        found.sort(key=lambda item: item[0], reverse=True)
        log.info(
            "SmugMug listed %d of %d galleries in %.1fs",
            len(found),
            total or len(found),
            time.monotonic() - started,
        )
        return [choice for _, choice in found[:MAX_ALBUMS]]

    def create_album(self, name: str) -> AlbumChoice:
        """A new unlisted gallery at the top of the user's site (FR-41)."""
        import requests

        name = name.strip()
        if not name:
            raise UploadError("Enter a name for the new gallery.", True)
        failed = "SmugMug did not make the gallery. Try again."
        try:
            user = self._get_json("/api/v2!authuser", failed=failed)["User"]
            root = str(user["Uris"]["Node"]["Uri"])
        except (KeyError, TypeError) as exc:
            raise UploadError(failed) from exc
        url_name = smugmug_url_name(name)
        for attempt in range(1, SMUGMUG_URL_TRIES + 1):
            body = {
                "Type": "Album",
                "Name": name,
                "UrlName": url_name if attempt == 1 else f"{url_name}-{attempt}",
                "Privacy": SMUGMUG_NEW_PRIVACY,
            }
            try:
                response = self._request(
                    "POST",
                    f"{SMUGMUG_API}{root}!children",
                    json=body,
                    headers={"Accept": "application/json"},
                    timeout=API_TIMEOUT,
                )
            except requests.RequestException as exc:
                raise UploadError(
                    "Can't reach SmugMug. Check the connection and try again."
                ) from exc
            if response.status_code == 409:  # that web address is taken; try the next
                continue
            if response.status_code == 401:
                raise _expired("SmugMug")
            if response.status_code == 403:
                raise UploadError(
                    "SmugMug did not allow a new gallery. Reconnect it in Settings and try again.",
                    True,
                )
            if response.status_code not in (200, 201):
                raise UploadError(failed)
            try:
                node = response.json()["Response"]["Node"]
                uri = str(node["Uris"]["Album"]["Uri"])
            except (KeyError, TypeError, ValueError) as exc:
                raise UploadError(failed) from exc
            folder = str(node.get("UrlPath") or "").rsplit("/", 1)[0].strip("/")
            node_id = str(node.get("NodeID") or "")
            return AlbumChoice(uri, str(node.get("Name") or name), node_id, folder)
        raise UploadError(
            "That name is taken on SmugMug. Choose another name, or pick it from the list.", True
        )

    def sort_album(self, event: Profile) -> None:
        """SmugMug galleries keep their own sort setting; WeFlow leaves it alone."""


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

    log.warning("Flickr call failed: %s: %s", type(exc).__name__, exc)

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

    def list_albums(self) -> list[AlbumChoice]:
        """The signed-in user's Flickr albums, newest first (FR-39)."""
        client = _flickr_client(with_token=True)
        found: list[tuple[int, AlbumChoice]] = []
        page = 1
        try:
            while len(found) < MAX_ALBUMS:
                listing = client.photosets.getList(page=page, per_page=500)["photosets"]
                for album in listing.get("photoset", []):
                    title = str(album.get("title", {}).get("_content") or "").strip()
                    if title:
                        try:
                            created = int(album.get("date_create") or 0)
                        except (TypeError, ValueError):
                            created = 0
                        found.append((created, AlbumChoice(title, title)))
                if page >= int(listing.get("pages", 1) or 1):
                    break
                page += 1
        except Exception as exc:
            raise _flickr_error(exc) from exc
        found.sort(key=lambda item: item[0], reverse=True)  # stable: undated keep their order
        return [choice for _, choice in found[:MAX_ALBUMS]]

    def create_album(self, name: str) -> AlbumChoice:
        """Flickr can't hold an empty album, so the title is kept and the album is
        made with the event's first upload, as a typed title always was (FR-41)."""
        title = name.strip()
        if not title:
            raise UploadError("Enter a name for the new album.", True)
        return AlbumChoice(title, title)

    def sort_album(self, event: Profile) -> None:
        """Flickr's API can't turn on an album's "date taken" sort, so put every photo
        in the album in date-taken order, oldest first, with ``reorderPhotos`` (FR-31a)."""
        title = event.album.strip()
        if not title:
            return
        client = _flickr_client(with_token=True)
        with self._lock:
            try:
                album_id = self._albums.get(title) or self._find_album(client, title)
                if not album_id:
                    return  # nothing reached the album yet
                self._albums[title] = album_id
                found: list[tuple[str, str]] = []
                page = 1
                while True:
                    listing = client.photosets.getPhotos(
                        photoset_id=album_id, extras="date_taken", per_page=500, page=page
                    )["photoset"]
                    for photo in listing.get("photo", []):
                        found.append((str(photo.get("datetaken") or ""), str(photo["id"])))
                    if page >= int(listing.get("pages", 1) or 1):
                        break
                    page += 1
                order = [pid for _, pid in sorted(found, key=lambda item: item[0])]  # stable
                if order == [pid for _, pid in found]:
                    return  # already in order
                client.photosets.reorderPhotos(photoset_id=album_id, photo_ids=",".join(order))
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


# ------------------------------------------------------------------- Dropbox
DROPBOX_AUTHORIZE = "https://www.dropbox.com/oauth2/authorize"
DROPBOX_TOKEN = "https://api.dropboxapi.com/oauth2/token"
DROPBOX_API = "https://api.dropboxapi.com/2"
DROPBOX_CONTENT = "https://content.dropboxapi.com/2"
DROPBOX_TOKEN_MARGIN = 300.0  # seconds: refresh the access token this long before it ends
DROPBOX_WRITE_TRIES = 4  # "too many writes at once": wait briefly and try again in place
DROPBOX_PARALLEL = 8  # folders listed at the same time
DROPBOX_HASH_BLOCK = 4 * 1024 * 1024


def dropbox_folder(album: str) -> str:
    """The event's folder field as a Dropbox path: ``/A/B``, or ``""`` when empty."""
    parts = [p.strip() for p in album.replace("\\", "/").split("/")]
    parts = [p for p in parts if p and p != "."]
    return "/" + "/".join(parts) if parts else ""


def dropbox_content_hash(path: Path) -> str:
    """Dropbox's ``content_hash``: SHA-256 of the SHA-256 of each 4 MiB block."""
    import hashlib

    outer = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(DROPBOX_HASH_BLOCK):
            outer.update(hashlib.sha256(block).digest())
    return outer.hexdigest()


def _dropbox_summary(response: Any) -> str:
    try:
        return str(response.json().get("error_summary") or "")
    except (ValueError, AttributeError):
        return ""


def _dropbox_error(response: Any, what: str = "the upload") -> UploadError:
    """Turn a failed Dropbox answer into a plain-language error (FR-33)."""
    status, summary = response.status_code, _dropbox_summary(response)
    if status == 401:
        return _expired("Dropbox")
    if status == 429 or status >= 500 or "too_many_write_operations" in summary:
        return UploadError("Dropbox is busy. WeFlow will keep trying.", busy=True)
    if "insufficient_space" in summary:
        return UploadError("Your Dropbox is full. Free up space, then press Retry.", True)
    if "no_write_permission" in summary or status == 403:
        return UploadError(
            "WeFlow can't write to that Dropbox folder. Choose another folder in the event.", True
        )
    if "malformed_path" in summary or "disallowed_name" in summary:
        return UploadError("Dropbox can't use that folder name. Choose another in the event.", True)
    return UploadError(f"Dropbox refused {what}. Check the folder in the event.", True)


def dropbox_begin() -> PendingAuth:
    """Step 1: the user approves WeFlow in the browser and copies a code (no redirect).

    ``token_access_type=offline`` asks for a refresh token, so the sign-in lasts."""
    from urllib.parse import urlencode

    key, secret = api_credentials("dropbox")
    if not key or not secret:
        raise UploadError("Enter a Dropbox app key first.", permanent=True)
    query = urlencode({"client_id": key, "response_type": "code", "token_access_type": "offline"})
    return PendingAuth(f"{DROPBOX_AUTHORIZE}?{query}", "", "")


def dropbox_finish(pending: PendingAuth, code: str) -> None:
    """Step 2: trade the code for a refresh token and store it."""
    import requests

    key, secret = api_credentials("dropbox")
    started = time.monotonic()
    try:
        response = requests.post(
            DROPBOX_TOKEN,
            data={"code": code.strip(), "grant_type": "authorization_code"},
            auth=(key, secret),
            timeout=API_TIMEOUT,
        )
    except requests.RequestException as exc:
        log.warning("Dropbox POST /oauth2/token failed: %s", type(exc).__name__)
        raise _offline("Dropbox") from exc
    log.info(
        "Dropbox POST /oauth2/token -> %s in %.1fs",
        response.status_code,
        time.monotonic() - started,
    )
    body: dict[str, Any] = {}
    with contextlib.suppress(ValueError):
        body = dict(response.json())
    if response.status_code == 401 or body.get("error") == "invalid_client":
        raise UploadError(
            "Dropbox did not accept the app key. Check the key and secret.", permanent=True
        )
    refresh = str(body.get("refresh_token") or "")
    if response.status_code != 200 or not refresh:
        raise UploadError(
            "Dropbox did not accept that code. Press Connect and try again.", permanent=True
        )
    secrets.set_secret(secrets.DROPBOX_TOKEN, refresh)


class DropboxUploader:
    """Uploads straight into the event's folder; the folder path is the "album".

    Only the refresh token is stored. The short-lived access token stays in memory.
    """

    _tokens: dict[str, tuple[str, float]] = {}  # refresh token -> (access token, ends at)
    _token_lock = threading.Lock()

    def __init__(self) -> None:
        self._local = threading.local()

    @staticmethod
    def _credentials() -> tuple[str, str, str]:
        try:
            key, secret = api_credentials("dropbox")
            refresh = secrets.get_secret(secrets.DROPBOX_TOKEN)
        except secrets.SecretsError as exc:
            raise _keychain_locked() from exc
        if not (key and secret and refresh):
            raise _not_connected("Dropbox")
        return key, secret, refresh

    def _session(self) -> Any:
        import requests

        session = getattr(self._local, "session", None)
        if session is None:
            session = self._local.session = requests.Session()
        return session

    def _access_token(self, renew: bool = False) -> str:
        import requests

        key, secret, refresh = self._credentials()
        with self._token_lock:
            cached = self._tokens.get(refresh)
            if cached and not renew and cached[1] > time.monotonic():
                return cached[0]
            started = time.monotonic()
            try:
                response = self._session().post(
                    DROPBOX_TOKEN,
                    data={"grant_type": "refresh_token", "refresh_token": refresh},
                    auth=(key, secret),
                    timeout=API_TIMEOUT,
                )
            except requests.RequestException as exc:
                log.warning("Dropbox token refresh failed: %s", type(exc).__name__)
                raise _offline("Dropbox") from exc
            log.info(
                "Dropbox POST /oauth2/token -> %s in %.1fs",
                response.status_code,
                time.monotonic() - started,
            )
            if response.status_code in (400, 401):  # revoked, or the app key changed
                raise _expired("Dropbox")
            if response.status_code != 200:
                raise _dropbox_error(response)
            try:
                body = response.json()
                token = str(body["access_token"])
                lasts = float(body.get("expires_in") or 14400)
            except (KeyError, TypeError, ValueError) as exc:
                raise UploadError(
                    "Dropbox gave an unexpected answer. WeFlow will keep trying."
                ) from exc
            ends = time.monotonic() + max(60.0, lasts - DROPBOX_TOKEN_MARGIN)
            self._tokens[refresh] = (token, ends)
            return token

    def _post(self, url: str, **kwargs: Any) -> Any:
        """One signed POST, logged by path, status and time only. A stale access
        token is renewed once. ``requests`` errors are left to the caller."""
        import requests

        where = urlparse(url).path
        headers = dict(kwargs.pop("headers", {}) or {})
        for renew in (False, True):
            headers["Authorization"] = f"Bearer {self._access_token(renew)}"
            started = time.monotonic()
            try:
                response = self._session().post(url, headers=headers, **kwargs)
            except requests.RequestException as exc:
                log.warning(
                    "Dropbox POST %s failed after %.1fs: %s",
                    where,
                    time.monotonic() - started,
                    type(exc).__name__,
                )
                raise
            log.info(
                "Dropbox POST %s -> %s in %.1fs",
                where,
                response.status_code,
                time.monotonic() - started,
            )
            if response.status_code != 401 or renew:
                return response
        raise AssertionError("unreachable")

    def _api(self, endpoint: str, body: dict[str, Any], failed: str) -> Any:
        import requests

        try:
            return self._post(f"{DROPBOX_API}/{endpoint}", json=body, timeout=API_TIMEOUT)
        except requests.RequestException as exc:
            raise UploadError(failed) from exc

    def _send(self, path: Path, target: str, autorename: bool) -> Any:
        """POST the file, waiting briefly when Dropbox has too many writes at once."""
        import requests

        arg = {"path": target, "mode": "add", "autorename": autorename, "mute": True}
        headers = {
            # ASCII-only JSON, as Dropbox requires in a header
            "Dropbox-API-Arg": json.dumps(arg),
            "Content-Type": "application/octet-stream",
        }
        for attempt in range(1, DROPBOX_WRITE_TRIES + 1):
            try:
                with open(path, "rb") as fh:
                    response = self._post(
                        f"{DROPBOX_CONTENT}/files/upload", data=fh, headers=headers, timeout=TIMEOUT
                    )
            except requests.RequestException as exc:
                raise _offline("Dropbox") from exc
            except OSError as exc:
                raise UploadError("The processed photo is missing. Press Retry.", True) from exc
            contended = response.status_code == 429 or (
                "too_many_write_operations" in _dropbox_summary(response)
            )
            if not contended or attempt == DROPBOX_WRITE_TRIES:
                return response
            try:
                wait = float(response.headers.get("Retry-After") or attempt)
            except ValueError:
                wait = float(attempt)
            time.sleep(min(wait, 10.0))
        raise AssertionError("unreachable")

    def _same_file(self, path: Path, target: str) -> str:
        """The id of the Dropbox file at ``target`` when it holds exactly this photo."""
        response = self._api(
            "files/get_metadata", {"path": target}, "Can't reach Dropbox. WeFlow will keep trying."
        )
        if response.status_code != 200:
            return ""
        try:
            body = response.json()
        except ValueError:
            return ""
        if body.get(".tag") != "file" or body.get("content_hash") != dropbox_content_hash(path):
            return ""
        return str(body.get("id") or "")

    def upload(self, path: Path, event: Profile) -> str:
        folder = dropbox_folder(event.album)
        if not folder:
            raise UploadError("This event has no Dropbox folder. Add one in the event.", True)
        target = f"{folder}/{path.name}"
        response = self._send(path, target, autorename=False)
        if response.status_code == 409 and "/conflict" in _dropbox_summary(response):
            # A file with this name is there. If it is this very photo (an answer lost on
            # the way back), it is already up: never send it twice (FR-32). Otherwise keep
            # the user's file and let Dropbox add " (1)" to this one's name.
            existing = self._same_file(path, target)
            if existing:
                return existing
            response = self._send(path, target, autorename=True)
        if response.status_code != 200:
            raise _dropbox_error(response)
        try:
            return str(response.json()["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise UploadError(
                "Dropbox gave an unexpected answer. WeFlow will keep trying."
            ) from exc

    def add_to_album(self, remote_id: str, event: Profile) -> None:
        """Dropbox uploads go straight into the folder; nothing more to do."""

    def _folders(self, path: str) -> list[str]:
        """The folders directly inside ``path`` ("" is the top of the Dropbox)."""
        failed = "Dropbox did not send the folder list. Try again."
        found: list[str] = []
        response = self._api("files/list_folder", {"path": path, "limit": 2000}, failed)
        while True:
            if response.status_code == 401:
                raise _expired("Dropbox")
            if response.status_code != 200:
                raise UploadError(failed)
            try:
                body = response.json()
            except ValueError as exc:
                raise UploadError(failed) from exc
            for entry in body.get("entries") or []:
                if entry.get(".tag") == "folder" and entry.get("path_display"):
                    found.append(str(entry["path_display"]))
            if not body.get("has_more") or len(found) >= MAX_ALBUMS:
                return found
            response = self._api("files/list_folder/continue", {"cursor": body["cursor"]}, failed)

    def list_albums(self) -> list[AlbumChoice]:
        """Folders at the top of the Dropbox and one level down, A to Z. Dropbox
        folders have no date, so the list can't be newest first (FR-39)."""
        started = time.monotonic()
        top = self._folders("")
        found = list(top)

        def inside(path: str) -> list[str]:
            try:
                return self._folders(path)
            except UploadError as exc:
                if "expired" in exc.message:
                    raise
                return []  # gone or unreadable meanwhile: list the rest

        with ThreadPoolExecutor(max_workers=DROPBOX_PARALLEL) as pool:
            for children in pool.map(inside, top):
                found.extend(children)
        found.sort(key=str.casefold)
        log.info("Dropbox listed %d folders in %.1fs", len(found), time.monotonic() - started)
        return [AlbumChoice(p, p.lstrip("/")) for p in found[:MAX_ALBUMS]]

    def create_album(self, name: str) -> AlbumChoice:
        """A new folder (FR-41). ``Clients/Smith`` makes both levels. A folder that
        is already there is simply chosen."""
        folder = dropbox_folder(name)
        if not folder:
            raise UploadError("Enter a name for the new folder.", True)
        failed = "Dropbox did not make the folder. Try again."
        response = self._api(
            "files/create_folder_v2", {"path": folder, "autorename": False}, failed
        )
        summary = _dropbox_summary(response)
        if response.status_code == 409 and "conflict/folder" in summary:
            return AlbumChoice(folder, folder.lstrip("/"))
        if response.status_code == 409 and "conflict" in summary:
            raise UploadError("A file in Dropbox already has that name. Choose another name.", True)
        if response.status_code != 200:
            raise _dropbox_error(response, "the new folder")
        try:
            made = str(response.json()["metadata"]["path_display"])
        except (KeyError, TypeError, ValueError):
            made = folder
        return AlbumChoice(made, made.lstrip("/"))

    def sort_album(self, event: Profile) -> None:
        """Dropbox shows a folder in name order; there is nothing to sort."""


# ---------------------------------------------------------------------- fake
class FakeUploader:
    """Stands in for a real service in tests. Never touches the network.

    The first ``fail_times`` upload calls (and the first ``album_fail_times``
    album calls) raise an ``UploadError``; ``permanent`` chooses which kind.
    """

    def __init__(
        self,
        fail_times: int = 0,
        permanent: bool = False,
        album_fail_times: int = 0,
        albums: list[AlbumChoice] | None = None,
        list_error: UploadError | None = None,
    ) -> None:
        self.albums = list(albums or [])
        self.created: list[str] = []
        self.list_error = list_error
        self.fail_times = fail_times
        self.album_fail_times = album_fail_times
        self.permanent = permanent
        self.uploads: list[str] = []
        self.album_adds: list[str] = []
        self.sorts: list[str] = []
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

    def list_albums(self) -> list[AlbumChoice]:
        if self.list_error is not None:
            raise self.list_error
        return list(self.albums)

    def create_album(self, name: str) -> AlbumChoice:
        if self.list_error is not None:
            raise self.list_error
        choice = AlbumChoice(f"/api/v2/album/new{len(self.created) + 1}", name.strip())
        self.created.append(choice.name)
        self.albums.insert(0, choice)  # newest first
        return choice

    def sort_album(self, event: Profile) -> None:
        with self._lock:
            self.sorts.append(event.album)


def make_uploader(destination: str) -> Uploader:
    if destination == "smugmug":
        return SmugMugUploader()
    if destination == "flickr":
        return FlickrUploader()
    if destination == "dropbox":
        return DropboxUploader()
    raise UploadError("This event has nowhere to upload to. Choose a destination.", True)
