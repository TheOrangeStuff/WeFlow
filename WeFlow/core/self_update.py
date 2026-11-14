"""One-click updates on Windows (FR-44).

The installer and its ``.sha256`` checksum are downloaded from the public releases
repo, the checksum is checked, and the installer then runs silently: it closes
WeFlow, upgrades it in place (events, settings and sign-ins untouched) and starts
it again. macOS and source runs keep "Download", which opens the release page.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

from .updates import RELEASES_REPO, TIMEOUT_SECONDS

log = logging.getLogger("WeFlow.self_update")

MAX_INSTALLER_BYTES = 400 * 1024 * 1024
CHUNK_BYTES = 256 * 1024
_SHA256 = re.compile(r"\s*([0-9a-fA-F]{64})\b")

Progress = Callable[[int, int], None]  # bytes so far, total (0 when unknown)
Chunks = Callable[[str], tuple[int, Iterator[bytes]]]  # url -> (size or 0, body)


class UpdateFailed(Exception):
    """The update could not be downloaded or checked. Nothing was installed."""


def supported() -> bool:
    """Only the installed Windows app can update itself."""
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def installer_name(version: str) -> str:
    return f"WeFlow-Setup-{version}.exe"


def installer_url(version: str) -> str:
    return (
        f"https://github.com/{RELEASES_REPO}/releases/download/v{version}/{installer_name(version)}"
    )


def _http_chunks(url: str) -> tuple[int, Iterator[bytes]]:
    import requests

    response = requests.get(
        url, headers={"User-Agent": "WeFlow"}, timeout=TIMEOUT_SECONDS, stream=True
    )
    response.raise_for_status()
    size = int(response.headers.get("Content-Length") or 0)
    return size, response.iter_content(CHUNK_BYTES)


def download(
    version: str,
    folder: Path,
    progress: Progress | None = None,
    chunks: Chunks | None = None,
) -> Path:
    """Download and verify the installer for ``version`` into ``folder``.

    Raises ``UpdateFailed`` on any network error, a missing or bad checksum, a
    checksum mismatch or an oversized file. A half-downloaded file is removed.
    """
    get = chunks or _http_chunks
    url = installer_url(version)
    try:
        _, body = get(url + ".sha256")
        match = _SHA256.match(b"".join(body)[:1024].decode("ascii", "replace"))
        if match is None:
            raise UpdateFailed("checksum file is not a SHA-256")
        expected = match.group(1).lower()

        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("WeFlow-Setup-*.exe*"):  # earlier updates, partial or done
            old.unlink(missing_ok=True)
        target = folder / installer_name(version)
        partial = target.with_name(target.name + ".part")
        digest = hashlib.sha256()
        done = 0
        size, body = get(url)
        try:
            with partial.open("wb") as out:
                for chunk in body:
                    done += len(chunk)
                    if done > MAX_INSTALLER_BYTES:
                        raise UpdateFailed("installer is too large")
                    digest.update(chunk)
                    out.write(chunk)
                    if progress is not None:
                        progress(done, size)
            if digest.hexdigest() != expected:
                raise UpdateFailed("checksum does not match")
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
    except UpdateFailed as exc:
        log.info("update download failed: %s", exc)
        raise
    except (OSError, ValueError) as exc:  # offline, timeout, HTTP error, disk full
        log.info("update download failed: %s", type(exc).__name__)
        raise UpdateFailed(type(exc).__name__) from exc
    log.info("update %s downloaded and verified", version)
    return target


def install_arguments(app_exe: Path, local_app_data: str) -> list[str]:
    """Silent-upgrade switches. Keeps the existing install: per-user or for everyone."""
    scope = "/ALLUSERS"
    if local_app_data:
        try:
            app_exe.resolve().relative_to(Path(local_app_data).resolve())
            scope = "/CURRENTUSER"
        except ValueError:
            pass
    return [
        "/SILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
        "/RELAUNCH=1",
        scope,
    ]


def launch(installer: Path) -> None:
    """Start the installer detached. The caller quits WeFlow straight after."""
    args = [
        str(installer),
        *install_arguments(Path(sys.executable), os.environ.get("LOCALAPPDATA", "")),
    ]
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    log.info("starting installer %s", installer.name)
    subprocess.Popen(args, creationflags=flags, close_fds=True)  # noqa: S603
