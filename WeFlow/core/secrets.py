"""The only place credentials are stored: the OS keychain, service name ``WeFlow``."""

from __future__ import annotations

import contextlib
import json
import logging
import threading
from typing import Protocol

from WeFlow import __version__

SERVICE = "WeFlow"

SMUGMUG_API_KEY = "smugmug_api_key"
SMUGMUG_API_SECRET = "smugmug_api_secret"
SMUGMUG_TOKEN = "smugmug_token"
SMUGMUG_TOKEN_SECRET = "smugmug_token_secret"
FLICKR_API_KEY = "flickr_api_key"
FLICKR_API_SECRET = "flickr_api_secret"
FLICKR_TOKEN = "flickr_token"
FLICKR_TOKEN_SECRET = "flickr_token_secret"

# Since 1.6.3 all of the above live together in this one entry, as JSON. Each entry
# costs its own macOS keychain prompt, so one entry means one prompt, not eight.
# The eight entries above are still read once to carry an older sign-in over.
BUNDLE_ENTRY = "credentials"
# Used when BUNDLE_ENTRY was made by another build and macOS will not let this one
# read or replace it. Read first: when it exists it is the newer of the two.
SPARE_ENTRY = "credentials-2"
_ACCOUNTS = (SPARE_ENTRY, BUNDLE_ENTRY)
_LEGACY_NAMES = (
    SMUGMUG_API_KEY,
    SMUGMUG_API_SECRET,
    SMUGMUG_TOKEN,
    SMUGMUG_TOKEN_SECRET,
    FLICKR_API_KEY,
    FLICKR_API_SECRET,
    FLICKR_TOKEN,
    FLICKR_TOKEN_SECRET,
)
_STAMP = "_written_by"

log = logging.getLogger("WeFlow.secrets")  # entry names and errors only: no values


class SecretsError(Exception):
    """The keychain could not be reached."""


class Backend(Protocol):
    def get(self, name: str) -> str: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class KeyringBackend:
    """All secrets in one keychain entry, read once per run and kept in memory.

    The first run of a new version writes the entry again. On macOS that makes this
    build the entry's creator, so later runs read it without asking, even when
    "Always Allow" did not stick (unsigned builds change identity every release).

    A keychain step the user refuses, or that macOS blocks, never stops sign-in:
    older entries are read best effort, and when the main entry cannot be read or
    replaced the secrets go to a spare entry this build creates itself.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, str] | None = None
        self._account = BUNDLE_ENTRY

    @staticmethod
    def _read(name: str) -> str:
        import keyring
        from keyring.errors import KeyringError

        try:
            return keyring.get_password(SERVICE, name) or ""
        except KeyringError as exc:
            log.warning("keychain read of %s failed: %s", name, exc)
            raise SecretsError("The system keychain is not available.") from exc

    def _write(self, values: dict[str, str]) -> None:
        import keyring
        from keyring.errors import KeyringError

        data = {k: v for k, v in values.items() if v}
        data[_STAMP] = __version__
        blob = json.dumps(data)
        accounts = [self._account] + [a for a in (BUNDLE_ENTRY, SPARE_ENTRY) if a != self._account]
        for account in accounts:
            try:
                keyring.set_password(SERVICE, account, blob)
            except KeyringError as exc:
                log.warning("keychain write of %s failed: %s", account, exc)
                continue
            self._account = account
            return
        raise SecretsError("The system keychain is not available.")

    def _load(self) -> dict[str, str]:
        if self._values is not None:
            return self._values
        raw, stamp, values = "", "", {}
        for account in _ACCOUNTS:
            try:
                raw = self._read(account)
            except SecretsError:
                continue  # refused or blocked: try the spare entry
            if raw:
                self._account = account
                break
        if raw:
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = {}
            if isinstance(parsed, dict):
                stamp = str(parsed.pop(_STAMP, ""))
                values = {str(k): str(v) for k, v in parsed.items() if v}
        else:
            # First run since 1.6.3: carry the separate entries over, best effort.
            for name in _LEGACY_NAMES:
                with contextlib.suppress(SecretsError):
                    if value := self._read(name):
                        values[name] = value
        if values and stamp != __version__:
            with contextlib.suppress(SecretsError):  # still usable from memory this run
                self._write(values)
        self._values = values
        return values

    def get(self, name: str) -> str:
        with self._lock:
            return self._load().get(name, "")

    def set(self, name: str, value: str) -> None:
        with self._lock:
            values = dict(self._load())
            values[name] = value
            self._write(values)
            self._values = values

    def delete(self, name: str) -> None:
        with self._lock:
            values = dict(self._load())
            if values.pop(name, None) is not None:
                self._write(values)
            self._values = values


class MemoryBackend:
    """In-process store for tests and CI (no keychain there)."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, name: str) -> str:
        return self.values.get(name, "")

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


_backend: Backend = KeyringBackend()


def use_backend(backend: Backend) -> None:
    global _backend
    _backend = backend


def get_secret(name: str) -> str:
    """Return the stored value, or ``""`` when there is none."""
    return _backend.get(name)


def set_secret(name: str, value: str) -> None:
    if value:
        _backend.set(name, value)
    else:
        _backend.delete(name)


def delete_secret(name: str) -> None:
    _backend.delete(name)
