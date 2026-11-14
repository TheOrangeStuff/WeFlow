"""Memory-card detection and eject. Nothing here ever writes to a card."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from .events import CardDetected, CardRemoved, Emit

FAKE_VOLUMES_ENV = "WeFlow_FAKE_VOLUMES"
POLL_SECONDS = 1.0

log = logging.getLogger("WeFlow.cards")


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


# Windows drive types and storage bus types (winioctl.h).
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
CARD_BUSES = frozenset({4, 7, 0x0C, 0x0D})  # FireWire, USB, SD, MMC


def windows_drive_is_card_reader(
    drive_type: int, bus_type: int | None, removable_media: bool, system_drive: bool
) -> bool:
    """Whether a Windows drive could be a camera card (FR-10).

    Many USB and UHS-II card readers report a *fixed* disk, so a fixed drive counts
    when it sits on a USB/SD/MMC/FireWire bus or says its media is removable. The
    Windows drive and internal SATA/NVMe disks never count.
    """
    if system_drive:
        return False
    if drive_type == DRIVE_REMOVABLE:
        return True
    if drive_type == DRIVE_FIXED:
        return removable_media or bus_type in CARD_BUSES
    return False


_logged_drives: dict[str, str] = {}


def _log_drive_once(root: str, note: str) -> None:
    """Log what was decided about a drive, only when that changes."""
    if _logged_drives.get(root) != note:
        _logged_drives[root] = note
        log.info("Drive %s: %s", root[:2], note)


def _windows_cards() -> list[Card]:
    cards: list[Card] = []
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]  # fmt: skip
        kernel32.DeviceIoControl.restype = wintypes.BOOL
        kernel32.DeviceIoControl.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
        ]  # fmt: skip
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        invalid_handle = wintypes.HANDLE(-1).value
        ioctl_storage_query_property = 0x002D1400
        share_read_write = 0x1 | 0x2
        open_existing = 3

        def bus_of(root: str) -> tuple[int | None, bool]:
            # Access 0 = query only: the device is never opened for reading or writing.
            handle = kernel32.CreateFileW(
                f"\\\\.\\{root[:2]}", 0, share_read_write, None, open_existing, 0, None
            )
            if handle in (None, invalid_handle):
                return None, False
            try:
                query = (ctypes.c_ubyte * 12)()  # StorageDeviceProperty, PropertyStandardQuery
                out = (ctypes.c_ubyte * 1024)()
                got = wintypes.DWORD(0)
                ok = kernel32.DeviceIoControl(
                    handle, ioctl_storage_query_property, query, 12, out, 1024,
                    ctypes.byref(got), None,
                )  # fmt: skip
                if not ok or got.value < 32:
                    return None, False
                raw = bytes(out)
                return int.from_bytes(raw[28:32], "little"), bool(raw[10])
            finally:
                kernel32.CloseHandle(handle)

        sem_failcriticalerrors = 0x0001  # no "There is no disk in the drive" dialogs
        system = os.environ.get("SystemDrive", "C:").upper()[:2]
        previous = kernel32.SetErrorMode(sem_failcriticalerrors)
        try:
            mask = kernel32.GetLogicalDrives()
            for index in range(26):
                if not mask & (1 << index):
                    continue
                root = f"{chr(ord('A') + index)}:\\"
                drive_type = kernel32.GetDriveTypeW(root)
                bus, removable_media = (None, False)
                if drive_type == DRIVE_FIXED and root[:2] != system:
                    bus, removable_media = bus_of(root)
                if not windows_drive_is_card_reader(
                    drive_type, bus, removable_media, root[:2] == system
                ):
                    _log_drive_once(root, f"skipped (type {drive_type}, bus {bus})")
                    continue
                path = Path(root)
                if not _has_dcim(path):
                    _log_drive_once(root, f"no DCIM folder (type {drive_type}, bus {bus})")
                    continue
                _log_drive_once(root, f"card (type {drive_type}, bus {bus})")
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
            try:
                found = {str(c.path): c for c in find_cards()}
            except Exception:  # a bad drive must not stop the watcher for good
                log.exception("Looking for cards failed")
                return
            added = [c for key, c in found.items() if key not in self._known]
            removed = [key for key in self._known if key not in found]
            self._known = found
        for key in removed:
            self._emit(CardRemoved(key))
        for card in added:
            log.info("Card detected: %s", card.label)
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
