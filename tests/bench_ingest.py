"""NFR-02 / NFR-03 benchmark. Not part of CI:  python tests/bench_ingest.py

Compares a plain copy with WeFlow's verified copy on generated 24 MP JPEGs, then
times processing one of them with a single worker.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.fixtures.make_card import save_photo  # noqa: E402
from WeFlow.core import imaging, ingest  # noqa: E402
from WeFlow.core.config import Profile  # noqa: E402


def plain_copy(source: Path, target: Path) -> None:
    """The baseline: the same chunked copy and fsync, without any hashing."""
    with open(source, "rb") as src, open(target, "wb") as dst:
        while chunk := src.read(ingest.CHUNK):
            dst.write(chunk)
        dst.flush()
        os.fsync(dst.fileno())


def main(count: int = 12) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        card = root / "card"
        card.mkdir()
        print(f"Generating {count} 24 MP photos…")
        for index in range(count):
            save_photo(card / f"IMG_{index:04d}.jpg", index, size=(6000, 4000))
        files = sorted(card.iterdir())
        megabytes = sum(f.stat().st_size for f in files) / 1e6

        best_raw = best_verified = float("inf")
        for attempt in range(3):
            raw, verified = root / f"raw{attempt}", root / f"verified{attempt}"
            raw.mkdir()
            started = time.perf_counter()
            for f in files:
                plain_copy(f, raw / f.name)
            best_raw = min(best_raw, time.perf_counter() - started)
            started = time.perf_counter()
            known: set[str] = set()
            for f in files:
                ingest.ingest_file(f, verified, "Bench", known)
            best_verified = min(best_verified, time.perf_counter() - started)
        extra = (best_verified / best_raw - 1) * 100
        print(f"Raw copy:      {best_raw:.3f} s for {megabytes:.0f} MB")
        print(f"Verified copy: {best_verified:.3f} s  (+{extra:.0f}%; NFR-02: ≤ 30%)")
        print("Source files are in the page cache here. A real card reads far slower,")
        print("so the hashing share of the total is smaller than this figure.")

        started = time.perf_counter()
        imaging.process_file(files[0], root / "out.jpg", Profile(name="Bench"))
        print(f"Process one 24 MP photo: {time.perf_counter() - started:.2f} s (NFR-03: ≤ 1 s)")


if __name__ == "__main__":
    main()
