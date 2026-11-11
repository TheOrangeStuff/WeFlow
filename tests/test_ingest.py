from __future__ import annotations

import builtins
import hashlib
import os
import stat
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.make_card import make_card, save_photo
from WeFlow.core import ingest


def tree(root: Path) -> dict[str, tuple[int, int, str]]:
    return {
        str(p.relative_to(root)): (
            p.stat().st_size,
            p.stat().st_mtime_ns,
            hashlib.sha256(p.read_bytes()).hexdigest(),
        )
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_fr12_lists_jpegs_any_case_and_skips_dotfiles(card: Path) -> None:
    names = [p.name for p in ingest.list_jpegs(card)]
    assert "IMG_0001.JPG" in names and "IMG_0003.jpeg" in names
    assert len(names) == 8
    assert not any(n.startswith(".") or n.endswith(".CR3") for n in names)


def test_fr12_copy_is_verified_and_named(card: Path, tmp_path: Path) -> None:
    originals = tmp_path / "lib" / "originals"
    source = card / "DCIM" / "100TEST" / "IMG_0001.JPG"
    copied = ingest.ingest_file(source, originals, "Gala", set())
    assert copied is not None
    assert copied.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert (originals / copied.name).read_bytes() == source.read_bytes()
    assert not list(originals.glob(".tmp-*"))


def test_fr12_checksum_mismatch_deletes_temp_and_raises(
    card: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ingest, "hash_file", lambda path: "0" * 64)
    originals = tmp_path / "lib" / "originals"
    with pytest.raises(ingest.ChecksumMismatch):
        ingest.ingest_file(card / "DCIM" / "100TEST" / "IMG_0001.JPG", originals, "Gala", set())
    assert list(originals.iterdir()) == []


def test_fr13_rename_uses_exif_date_then_mtime(card: Path, tmp_path: Path) -> None:
    originals = tmp_path / "lib" / "originals"
    folder = card / "DCIM" / "100TEST"
    copied = ingest.ingest_file(folder / "IMG_0001.JPG", originals, "Gala", set())
    assert copied is not None
    assert copied.name == "Gala_20260516-183007_IMG_0001.jpg"
    assert copied.captured == "2026-05-16T18:30:07"

    plain = tmp_path / "noexif.jpg"
    from PIL import Image

    Image.new("RGB", (32, 32), "gray").save(plain, "JPEG")
    when = datetime(2025, 1, 2, 3, 4, 5).timestamp()
    os.utime(plain, (when, when))
    fallback = ingest.ingest_file(plain, originals, "Gala", set())
    assert fallback is not None and fallback.name == "Gala_20250102-030405_noexif.jpg"


def test_fr13_collisions_get_numbered(tmp_path: Path) -> None:
    originals = tmp_path / "originals"
    names = []
    for sub in ("a", "b", "c"):
        (tmp_path / sub).mkdir()
        save_photo(tmp_path / sub / "IMG_0001.jpg", 1, size=(40 + len(names), 30))
        copied = ingest.ingest_file(tmp_path / sub / "IMG_0001.jpg", originals, "Gala", set())
        assert copied is not None
        names.append(copied.name)
    base = "Gala_20260516-183007_IMG_0001"
    assert names == [f"{base}.jpg", f"{base}_2.jpg", f"{base}_3.jpg"]


def test_fr14_known_checksum_is_skipped(card: Path, tmp_path: Path) -> None:
    originals = tmp_path / "lib" / "originals"
    known: set[str] = set()
    results = [ingest.ingest_file(p, originals, "Gala", known) for p in ingest.list_jpegs(card)]
    assert sum(r is None for r in results) == 1  # the duplicate on the card
    assert len(list(originals.iterdir())) == 7
    again = [ingest.ingest_file(p, originals, "Gala", known) for p in ingest.list_jpegs(card)]
    assert all(r is None for r in again)
    assert len(list(originals.iterdir())) == 7


def test_fr17_ingest_never_writes_to_the_card(
    card: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for path in sorted(card.rglob("*"), reverse=True):
        path.chmod(stat.S_IREAD | (stat.S_IEXEC if path.is_dir() else 0))
    card.chmod(stat.S_IREAD | stat.S_IEXEC)
    before = tree(card)

    real_open = builtins.open

    def guarded_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if isinstance(file, str | os.PathLike) and Path(file).is_relative_to(card):
            assert set(mode) <= set("rb"), f"card file opened with mode {mode!r}: {file}"
        return real_open(file, mode, *args, **kwargs)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        for arg in args[:2]:
            if isinstance(arg, str | os.PathLike) and Path(arg).is_relative_to(card):
                raise AssertionError(f"tried to change the card: {arg}")
        return None

    monkeypatch.setattr(builtins, "open", guarded_open)
    real = {name: getattr(os, name) for name in ("replace", "rename", "remove", "unlink", "utime")}
    for name, function in real.items():

        def wrapper(*args: Any, _f: Any = function, **kwargs: Any) -> Any:
            forbidden(*args)
            return _f(*args, **kwargs)

        monkeypatch.setattr(os, name, wrapper)

    try:
        known: set[str] = set()
        originals = tmp_path / "lib" / "originals"
        copied = [ingest.ingest_file(p, originals, "Gala", known) for p in ingest.list_jpegs(card)]
        assert sum(c is not None for c in copied) == 7
        assert tree(card) == before
    finally:
        monkeypatch.undo()
        card.chmod(stat.S_IRWXU)
        for path in card.rglob("*"):
            path.chmod(stat.S_IRWXU)


def test_fr12_unreadable_card_is_a_copy_error(tmp_path: Path) -> None:
    with pytest.raises(ingest.CopyError):
        ingest.ingest_file(tmp_path / "gone.jpg", tmp_path / "originals", "Gala", set())


def test_make_card_covers_required_cases(tmp_path: Path) -> None:
    from PIL import Image

    paths = make_card(tmp_path / "CARD", count=6)
    folder = tmp_path / "CARD" / "DCIM" / "100TEST"
    assert len(paths) == 6
    assert (folder / "._IMG_0001.JPG").exists()
    assert paths[0].suffix == ".JPG"
    assert paths[-1].read_bytes() == paths[0].read_bytes()
    with Image.open(paths[1]) as img:
        assert img.getexif()[0x0112] == 6
