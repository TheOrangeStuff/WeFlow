"""Memory-card detection and eject. Nothing here ever writes to a card."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from .events import CardDetected, CardRemoved, Emit

FAKE_VOLUMES_ENV = "WeFlow_FAKE_VOLUMES"
POLL_SECONDS = 1.0


@dataclass(frozen=True)
class Card:
    path: Path
    label: str


def _has_dcim(volume: Path) -> bool:
    try:
        return (volume / "DCIM").is_dir()
    except OSError:
        return False


def _subfolders(parent: Path) -> list[Path]:
    try:
        return sorted(p for p in parent.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return []


def _windows_cards() -> list[Card]:
    cards: list[Card] = []
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        sem_failcriticalerrors = 0x0001  # no "There is no disk in the drive" dialogs
        drive_removable = 2
        previous = kernel32.SetErrorMode(sem_failcriticalerrors)
        try:
            mask = kernel32.GetLogicalDrives()
            for index in range(26):
                if not mask & (1 << index):
                    continue
                root = f"{chr(ord('A') + index)}:\\"
                if kernel32.GetDriveTypeW(root) != drive_removable:
                    continue
                path = Path(root)
                if not _has_dcim(path):
                    continue
                name = ctypes.create_unicode_buffer(261)
                kernel32.GetVolumeInformationW(root, name, 261, None, None, None, None, 0)
                cards.append(Card(path, name.value or root[:2]))
        finally:
            kernel32.SetErrorMode(previous)
    return cards


def find_cards() -> list[Card]:
    """Mounted removable volumes that contain ``DCIM/`` (FR-10)."""
    fake = os.environ.get(FAKE_VOLUMES_ENV)
    if fake:
        return [Card(p, p.name) for p in _subfolders(Path(fake)) if _has_dcim(p)]
    if sys.platform == "win32":
        return _windows_cards()
    if sys.platform == "darwin":
        parents = [Path("/Volumes")]
    else:  # Linux, for development only
        user = os.environ.get("USER", "")
        parents = [Path("/media") / user, Path("/run/media") / user]
    return [Card(p, p.name) for parent in parents for p in _subfolders(parent) if _has_dcim(p)]


def eject(path: Path) -> tuple[bool, str]:
    """Eject the volume (FR-16). Returns ``(ok, plain-language message)``."""
    if os.environ.get(FAKE_VOLUMES_ENV):
        return True, "Card ejected."
    failed = "The card could not be ejected. Close other apps using it and try again."
    try:
        if sys.platform == "darwin":
            done = subprocess.run(
                ["diskutil", "eject", str(path)], capture_output=True, timeout=30, check=False
            )
            ok = done.returncode == 0
        elif sys.platform == "win32":
            drive = str(path)[:2]
            script = (
                "$d=(New-Object -ComObject Shell.Application).Namespace(17)"
                f".ParseName('{drive}'); if($d){{$d.InvokeVerb('Eject'); Start-Sleep 2}}"
                f"; if(Test-Path '{drive}\\'){{exit 1}}"
            )
            done = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                timeout=30,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            ok = done.returncode == 0
        else:
            done = subprocess.run(
                ["umount", str(path)], capture_output=True, timeout=30, check=False
            )
            ok = done.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False, failed
    return (True, "Card ejected.") if ok else (False, failed)


class CardWatcher:
    """Polls for cards and emits ``CardDetected`` / ``CardRemoved``."""

    def __init__(self, emit: Emit, interval: float = POLL_SECONDS) -> None:
        self._emit = emit
        self._interval = interval
        self._known: dict[str, Card] = {}
        self._paused = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def paused(self) -> bool:
        return self._paused

    def set_paused(self, paused: bool) -> None:
        """While paused nothing is reported; cards already in are announced on resume."""
        with self._lock:
            self._paused = paused
            if paused:
                self._known.clear()

    def present(self) -> list[Card]:
        with self._lock:
            return list(self._known.values())

    def poll(self) -> None:
        with self._lock:
            if self._paused:
                return
            found = {str(c.path): c for c in find_cards()}
            added = [c for key, c in found.items() if key not in self._known]
            removed = [key for key in self._known if key not in found]
            self._known = found
        for key in removed:
            self._emit(CardRemoved(key))
        for card in added:
            self._emit(CardDetected(str(card.path), card.label))

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="card-watcher", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll()
            self._stop.wait(self._interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
