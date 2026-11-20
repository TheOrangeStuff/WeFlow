"""Ask WeFlow's public GitHub repo whether a newer WeFlow exists.

Releases are published on ``RELEASES_REPO``, which anyone can read without a token.
This module only looks; it never downloads or installs anything.

Versions up to 1.18.0 check the archived ``TheOrangeStuff/weflow-releases`` instead,
from when the source repo was private. Its last release is 1.19.1, which checks here.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

RELEASES_REPO = "TheOrangeStuff/WeFlow"
LATEST_API = f"https://api.github.com/repos/{RELEASES_REPO}/releases/latest"
DOWNLOAD_PAGE = f"https://github.com/{RELEASES_REPO}/releases/latest"
TIMEOUT_SECONDS = 10.0
CHECK_EVERY_HOURS = 24
# Bundled beside the WeFlow package by packaging/WeFlow.spec; the repo root in development.
CHANGELOG = Path(__file__).resolve().parents[2] / "CHANGELOG.md"

log = logging.getLogger("WeFlow.updates")
_VERSION = re.compile(r"v?(\d{1,6}(?:\.\d{1,6}){0,3})")


@dataclass(frozen=True)
class Update:
    """A published version newer than the one running."""

    version: str
    url: str = DOWNLOAD_PAGE


def parse_version(text: str) -> tuple[int, ...] | None:
    """``"v1.2.0"`` -> ``(1, 2)``. Anything that is not plain numbers and dots is None."""
    match = _VERSION.fullmatch(text.strip())
    if match is None:
        return None
    parts = [int(part) for part in match.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()  # 1.2 and 1.2.0 are the same version
    return tuple(parts)


def is_newer(candidate: str, than: str) -> bool:
    """True only when both are plain versions and ``candidate`` is the higher one."""
    a, b = parse_version(candidate), parse_version(than)
    return a is not None and b is not None and a > b


def fetch_latest_tag() -> str:
    """The tag of the newest published release, or "" when there is none yet.

    GitHub's "latest" leaves out drafts and pre-releases.
    """
    import requests

    response = requests.get(
        LATEST_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "WeFlow"},
        timeout=TIMEOUT_SECONDS,
    )
    if response.status_code == 404:
        return ""
    response.raise_for_status()
    data = response.json()
    tag = data.get("tag_name") if isinstance(data, dict) else None
    return tag if isinstance(tag, str) else ""


def latest(current: str, fetch: Callable[[], str] | None = None) -> Update | None:
    """Return the newer version, or None when up to date.

    Raises ``OSError`` or ``ValueError`` when the check itself fails (offline,
    timeout, rate limit, bad JSON), so "Check for updates" can say so.
    """
    tag = (fetch or fetch_latest_tag)()
    if not is_newer(tag, current):
        return None
    return Update(tag.strip().removeprefix("v"))


def check(current: str, fetch: Callable[[], str] | None = None) -> Update | None:
    """Return the newer version, or None when up to date, offline or unsure.

    Never raises: a failed check is logged and treated as "nothing new".
    """
    try:
        return latest(current, fetch)
    except (OSError, ValueError) as exc:  # offline, timeout, rate limit, bad JSON
        log.info("update check failed: %s", type(exc).__name__)
        return None


_HEADING = re.compile(r"##\s+v?(\d+(?:\.\d+)*)")
_BULLET = re.compile(r"\s*[-*]\s+(.*)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")


def _plain(text: str) -> str:
    """Markdown inline marks removed: the UI shows plain text."""
    text = _LINK.sub(r"\1", text)
    return re.sub(r"\*\*|__|`", "", text).strip()


Notes = list[tuple[str, list[str]]]  # (version, plain-text bullets), newest first


def release_notes(changelog: Path | None = None) -> Notes:
    """Every ``## <version>`` section of the changelog that has bullets, newest first.

    Empty when the file is missing. Text that is not a bullet (headings, paragraphs)
    is left out.
    """
    try:
        lines = (changelog or CHANGELOG).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    sections: list[tuple[str, list[str]]] = []
    items: list[str] | None = None
    in_bullet = False
    for line in lines:
        if line.startswith("## "):
            heading = _HEADING.match(line)
            items = None
            if heading:
                items = []
                sections.append((heading.group(1), items))
            in_bullet = False
            continue
        if items is None:
            continue
        bullet = _BULLET.fullmatch(line)
        if bullet:
            items.append(bullet.group(1))
            in_bullet = True
        elif in_bullet and line.startswith(" ") and line.strip():
            items[-1] += " " + line.strip()  # a wrapped line of the same bullet
        else:
            in_bullet = False
    notes = [(v, [t for t in map(_plain, b) if t]) for v, b in sections]
    notes = [(v, b) for v, b in notes if b]
    return sorted(notes, key=lambda n: parse_version(n[0]) or (), reverse=True)


def whats_new(version: str, changelog: Path | None = None) -> list[str]:
    """The bullet points under ``## <version>`` in the changelog, as plain text."""
    wanted = parse_version(version)
    for found, items in release_notes(changelog):
        if wanted and parse_version(found) == wanted:
            return items
    return []


def whats_new_since(seen: str, current: str, changelog: Path | None = None) -> Notes:
    """Notes for every version after ``seen``, up to and including ``current``.

    Someone who skipped versions sees all they missed, newest first. With no ``seen``
    (an install from before 1.1.0 kept track), only ``current`` is shown.
    """
    top = parse_version(current)
    floor = parse_version(seen) if seen else top
    if not top or not floor:
        return []
    return [
        (v, items)
        for v, items in release_notes(changelog)
        if (parsed := parse_version(v))
        and parsed <= top
        and (parsed > floor or (not seen and parsed == top))
    ]
