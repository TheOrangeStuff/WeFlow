from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from tests.conftest import PipelineFactory, Recorder
from tests.fixtures.make_card import make_card
from WeFlow.core import hotfolder
from WeFlow.core.config import Store
from WeFlow.core.events import Event, FolderPhotos
from WeFlow.core.uploaders import FakeUploader

from .test_pipeline import NEW_PHOTOS, flickr_event


def jpegs(folder: Path) -> list[Path]:
    found = folder.rglob("*")
    return sorted(p for p in found if p.suffix.lower() in (".jpg", ".jpeg") and p.name[0] != ".")


def aged(path: Path, seconds: float = 10.0) -> None:
    then = time.time() - seconds
    os.utime(path, (then, then))


def reported(seen: list[Event]) -> list[str]:
    return [Path(p).name for e in seen if isinstance(e, FolderPhotos) for p in e.paths]


def test_fr48_waits_until_file_settled(tmp_path: Path) -> None:
    photo = make_card(tmp_path / "card", count=4)[0]
    folder = tmp_path / "camera"
    folder.mkdir()
    data = photo.read_bytes()
    target = folder / "IMG_9001.JPG"
    target.write_bytes(data[: len(data) // 2])  # half sent
    seen: list[Event] = []
    watcher = hotfolder.FolderWatcher(seen.append)
    watcher.set_folder(folder)
    for _ in range(3):
        aged(target)
        watcher.poll()
    assert seen == []  # no end marker yet
    target.write_bytes(data)
    watcher.poll()  # just changed: not settled
    assert seen == []
    aged(target)
    watcher.poll()  # first look at the aged file
    watcher.poll()  # unchanged since: settled, and nothing else is arriving
    assert reported(seen) == ["IMG_9001.JPG"]
    watcher.poll()
    assert len(reported(seen)) == 1  # reported once


def test_fr48_batches_burst(tmp_path: Path) -> None:
    folder = tmp_path / "camera"
    make_card(folder, count=5)
    photos = jpegs(folder)
    for photo in photos:
        aged(photo)
    sending = photos[-1]
    sending.write_bytes(sending.read_bytes()[:-2])  # the last one is still arriving
    seen: list[Event] = []
    watcher = hotfolder.FolderWatcher(seen.append)
    watcher.set_folder(folder)
    for _ in range(3):
        aged(sending)
        watcher.poll()
    assert seen == []  # the others wait for it
    sending.write_bytes(photos[0].read_bytes())
    aged(sending)
    for _ in range(2):
        watcher.poll()
    events = [e for e in seen if isinstance(e, FolderPhotos)]
    assert len(events) == 1 and len(events[0].paths) == len(photos)


def test_fr48_never_writes_folder(tmp_path: Path) -> None:
    folder = tmp_path / "camera"
    make_card(folder, count=4)
    for photo in jpegs(folder):
        aged(photo)
    before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in jpegs(folder)}
    for path in [folder, *folder.rglob("*")]:
        path.chmod(0o555 if path.is_dir() else 0o444)
    try:
        seen: list[Event] = []
        watcher = hotfolder.FolderWatcher(seen.append)
        watcher.set_folder(folder)
        for _ in range(3):
            watcher.poll()
        assert reported(seen)
        assert {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in jpegs(folder)} == before
    finally:
        for path in [folder, *folder.rglob("*")]:
            path.chmod(0o755 if path.is_dir() else 0o644)


def test_fr48_live_then_card_no_duplicate(
    store: Store, card: Path, recorder: Recorder, make_pipeline: PipelineFactory, tmp_path: Path
) -> None:
    event = flickr_event(store)
    fake = FakeUploader()
    pipeline = make_pipeline(fake)
    folder = tmp_path / "camera"
    shutil.copytree(card / "DCIM", folder)
    live = pipeline.submit(folder, event, "Camera", is_card=False, files=jpegs(folder)[:4])
    done = recorder.wait_state(live.id, "done")
    assert done.live and done.total == 4 and not done.is_card
    later = pipeline.submit(card, event, "CARD_A", is_card=True)
    done = recorder.wait_state(later.id, "done")
    assert (done.copied, done.skipped) == (NEW_PHOTOS - 4, 5)  # FR-14: 4 came live, 1 dup
    assert len(fake.uploads) == len(set(fake.uploads)) == NEW_PHOTOS  # never twice
