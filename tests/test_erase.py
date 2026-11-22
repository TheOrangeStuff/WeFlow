"""FR-47: "Erase card" removes only photos with a verified copy on this computer."""

from __future__ import annotations

import stat
from pathlib import Path

from tests.conftest import PipelineFactory, Recorder
from tests.fixtures.make_card import make_card
from WeFlow.core import erase, ingest
from WeFlow.core.config import Store


def copy_all(card: Path, originals: Path) -> dict[str, Path]:
    known: set[str] = set()
    copies: dict[str, Path] = {}
    for path in ingest.list_jpegs(card):
        copied = ingest.ingest_file(path, originals, "Gala", known)
        if copied is not None:
            copies[copied.sha256] = originals / copied.name
    return copies


def test_fr47_erases_only_verified_copies_and_keeps_other_files(card: Path, tmp_path: Path) -> None:
    copies = copy_all(card, tmp_path / "originals")
    others = [p for p in card.rglob("*") if p.is_file() and p not in ingest.list_jpegs(card)]
    assert others  # the fixture has a RAW file and dotfiles
    sha, damaged = next(iter(copies.items()))
    damaged.write_bytes(b"not the photo")  # this copy no longer matches: keep its card file
    keep = [p for p in ingest.list_jpegs(card) if ingest.hash_file(p) == sha]

    result = erase.erase_copied(card, copies)

    assert result.ok and result.kept == len(keep) and result.erased == 8 - len(keep)
    assert all(p.is_file() for p in others)
    assert ingest.list_jpegs(card) == keep
    assert (card / "DCIM").is_dir()  # folders stay: nothing is formatted


def test_fr47_photos_not_copied_stay_on_the_card(card: Path) -> None:
    before = ingest.list_jpegs(card)
    result = erase.erase_copied(card, {})
    assert result.ok and result.erased == 0 and ingest.list_jpegs(card) == before


def test_fr47_locked_card_says_so(card: Path, tmp_path: Path) -> None:
    copies = copy_all(card, tmp_path / "originals")
    folders = [p for p in card.rglob("*") if p.is_dir()]
    for folder in folders:
        folder.chmod(stat.S_IREAD | stat.S_IEXEC)
    try:
        result = erase.erase_copied(card, copies)
    finally:
        for folder in folders:
            folder.chmod(stat.S_IRWXU)
    assert not result.ok and result.message == erase.LOCKED


def test_fr47_pipeline_erases_only_after_safe(
    store: Store, card: Path, tmp_path: Path, recorder: Recorder, make_pipeline: PipelineFactory
) -> None:
    event = store.create_event("Gala")
    pipeline = make_pipeline()
    assert not pipeline.erase_card(12345).ok
    batch = pipeline.submit(card, event, "CARD_A", is_card=True)
    recorder.wait_state(batch.id, "safe")
    result = pipeline.erase_card(batch.id)
    assert result.ok and result.erased == 8 and ingest.list_jpegs(card) == []

    folder = tmp_path / "folder"
    make_card(folder, count=4)
    imported = pipeline.submit(folder, event, "folder", is_card=False)
    recorder.wait(lambda: bool((last := recorder.last(imported.id)) and last.verified))
    assert not pipeline.erase_card(imported.id).ok  # a folder import is never erased
    assert ingest.list_jpegs(folder)
