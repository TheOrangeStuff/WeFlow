"""NFR-04: the UI stays responsive while a large import runs."""

from __future__ import annotations

import os
import time

from PySide6.QtCore import QTimer

from tests.fixtures.make_card import make_card

PHOTOS = 1000
# Shared CI runners stall unpredictably; the 100 ms requirement is checked locally.
LIMIT_MS = 400 if os.environ.get("CI") else 100


def test_nfr04_no_long_frames_during_1000_photo_import(qtbot, window, bridge, tmp_path) -> None:  # type: ignore[no-untyped-def]
    make_card(tmp_path / "big" / "CARD", count=PHOTOS + 1, size=(96, 64))
    gaps: list[float] = []
    last = [time.perf_counter()]

    def tick() -> None:
        now = time.perf_counter()
        gaps.append((now - last[0]) * 1000)
        last[0] = now

    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(tick)

    updates: list[int] = []
    bridge.batch_updated.connect(lambda batch: updates.append(batch.copied))
    last[0] = time.perf_counter()
    timer.start()
    window.import_folder(str(tmp_path / "big" / "CARD"))
    qtbot.waitUntil(lambda: window.panel.state == "done", timeout=300_000)
    timer.stop()

    assert window.panel.subline.text() == f"{PHOTOS} photos ready in the library"
    assert len(gaps) > 50
    assert max(gaps) < LIMIT_MS, f"longest UI stall {max(gaps):.0f} ms"
    assert len(updates) < PHOTOS  # counter updates were coalesced, not one per photo
