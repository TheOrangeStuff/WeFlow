from __future__ import annotations

import shutil
from pathlib import Path

from tests.fixtures.make_card import make_card
from WeFlow.core import cards
from WeFlow.core.events import CardDetected, CardRemoved, Event


def test_fr10_fake_volumes_need_dcim(tmp_path: Path) -> None:
    volumes = tmp_path / "volumes"
    (volumes / "USB_STICK" / "Documents").mkdir(parents=True)
    make_card(volumes / "CARD_A", count=4)
    found = cards.find_cards()
    assert [(c.label, c.path) for c in found] == [("CARD_A", volumes / "CARD_A")]


def test_fr10_watcher_reports_insert_and_remove(tmp_path: Path) -> None:
    seen: list[Event] = []
    watcher = cards.CardWatcher(seen.append)
    watcher.poll()
    assert seen == []
    card = tmp_path / "volumes" / "EOS_DIGITAL"
    make_card(card, count=4)
    watcher.poll()
    watcher.poll()
    assert seen == [CardDetected(str(card), "EOS_DIGITAL")]
    shutil.rmtree(card)
    watcher.poll()
    assert seen[-1] == CardRemoved(str(card))


def test_paused_watcher_is_silent_then_announces_on_resume(tmp_path: Path) -> None:
    seen: list[Event] = []
    watcher = cards.CardWatcher(seen.append)
    watcher.set_paused(True)
    make_card(tmp_path / "volumes" / "CARD_A", count=4)
    watcher.poll()
    assert seen == []
    watcher.set_paused(False)
    watcher.poll()
    assert len(seen) == 1 and isinstance(seen[0], CardDetected)


def test_fr16_eject_fake_volume_leaves_card_untouched(tmp_path: Path) -> None:
    card = tmp_path / "volumes" / "CARD_A"
    make_card(card, count=4)
    before = sorted(p.name for p in card.rglob("*"))
    assert cards.eject(card) == (True, "Card ejected.")
    assert sorted(p.name for p in card.rglob("*")) == before


def test_fr10_windows_fixed_usb_card_readers_count() -> None:
    is_reader = cards.windows_drive_is_card_reader
    assert is_reader(cards.DRIVE_REMOVABLE, None, False, system_drive=False)
    assert is_reader(cards.DRIVE_FIXED, 7, False, system_drive=False)  # USB reader as fixed disk
    assert is_reader(cards.DRIVE_FIXED, 0x0C, False, system_drive=False)  # SD bus
    assert is_reader(cards.DRIVE_FIXED, None, True, system_drive=False)  # removable media
    assert not is_reader(cards.DRIVE_FIXED, 0x11, False, system_drive=False)  # internal NVMe
    assert not is_reader(cards.DRIVE_FIXED, 7, False, system_drive=True)  # the Windows drive
    assert not is_reader(4, 7, False, system_drive=False)  # network drive


def test_fr10_watcher_survives_a_failing_scan(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: list[Event] = []
    watcher = cards.CardWatcher(seen.append)

    def broken() -> list[cards.Card]:
        raise OSError("drive vanished")

    monkeypatch.setattr(cards, "find_cards", broken)
    watcher.poll()  # must not raise
    assert seen == []
