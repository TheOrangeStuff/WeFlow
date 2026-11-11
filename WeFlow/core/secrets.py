"""The only place credentials are stored: the OS keychain, service name ``WeFlow``."""

from __future__ import annotations

from typing import Protocol

SERVICE = "WeFlow"

SMUGMUG_API_KEY = "smugmug_api_key"
SMUGMUG_API_SECRET = "smugmug_api_secret"
SMUGMUG_TOKEN = "smugmug_token"
SMUGMUG_TOKEN_SECRET = "smugmug_token_secret"
FLICKR_API_KEY = "flickr_api_key"
FLICKR_API_SECRET = "flickr_api_secret"
FLICKR_TOKEN = "flickr_token"
FLICKR_TOKEN_SECRET = "flickr_token_secret"


class SecretsError(Exception):
    """The keychain could not be reached."""


class Backend(Protocol):
    def get(self, name: str) -> str: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class KeyringBackend:
    def get(self, name: str) -> str:
        import keyring
        from keyring.errors import KeyringError

        try:
            return keyring.get_password(SERVICE, name) or ""
        except KeyringError as exc:
            raise SecretsError("The system keychain is not available.") from exc

    def set(self, name: str, value: str) -> None:
        import keyring
        from keyring.errors import KeyringError

        try:
            keyring.set_password(SERVICE, name, value)
        except KeyringError as exc:
            raise SecretsError("The system keychain is not available.") from exc

    def delete(self, name: str) -> None:
        import keyring
        from keyring.errors import KeyringError

        try:
            if keyring.get_password(SERVICE, name) is not None:
                keyring.delete_password(SERVICE, name)
        except KeyringError as exc:
            raise SecretsError("The system keychain is not available.") from exc


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
