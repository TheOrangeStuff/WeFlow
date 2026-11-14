from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from WeFlow.core import self_update, updates
from WeFlow.core.self_update import UpdateFailed

BODY = b"MZ fake installer " * 1000
GOOD_SUM = hashlib.sha256(BODY).hexdigest()


def serve(files: dict[str, bytes]) -> self_update.Chunks:
    def chunks(url: str) -> tuple[int, Iterator[bytes]]:
        if url not in files:
            raise OSError("404")
        data = files[url]
        return len(data), iter([data[i : i + 4096] for i in range(0, len(data), 4096)])

    return chunks


def release(checksum: str, body: bytes = BODY) -> self_update.Chunks:
    url = self_update.installer_url("9.0.0")
    return serve({url: body, url + ".sha256": f"{checksum}  WeFlow-Setup-9.0.0.exe".encode()})


def test_fr44_installer_comes_from_the_public_releases_repo() -> None:
    assert self_update.installer_url("9.0.0") == (
        f"https://github.com/{updates.RELEASES_REPO}/releases/download/v9.0.0/"
        "WeFlow-Setup-9.0.0.exe"
    )


def test_fr44_download_checks_the_checksum(tmp_path: Path) -> None:
    tmp_path = tmp_path / "updates"
    tmp_path.mkdir()
    (tmp_path / "WeFlow-Setup-8.0.0.exe").write_bytes(b"old")  # an earlier update is cleared
    seen: list[tuple[int, int]] = []
    path = self_update.download(
        "9.0.0", tmp_path, lambda d, t: seen.append((d, t)), release(GOOD_SUM)
    )
    assert path.read_bytes() == BODY
    assert [p.name for p in tmp_path.iterdir()] == ["WeFlow-Setup-9.0.0.exe"]
    assert seen[-1] == (len(BODY), len(BODY))


@pytest.mark.parametrize("checksum", ["0" * 64, "not a checksum"])
def test_fr44_bad_download_is_never_kept(tmp_path: Path, checksum: str) -> None:
    tmp_path = tmp_path / "updates"
    with pytest.raises(UpdateFailed):
        self_update.download("9.0.0", tmp_path, None, release(checksum))
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


def test_fr44_missing_checksum_or_offline_fails(tmp_path: Path) -> None:
    with pytest.raises(UpdateFailed):
        self_update.download("9.0.0", tmp_path, None, serve({}))


def test_fr44_silent_upgrade_keeps_the_install_scope(tmp_path: Path) -> None:
    local = tmp_path / "Local"
    per_user = self_update.install_arguments(local / "Programs/WeFlow/WeFlow.exe", str(local))
    everyone = self_update.install_arguments(
        tmp_path / "Program Files/WeFlow/WeFlow.exe", str(local)
    )
    assert "/CURRENTUSER" in per_user and "/ALLUSERS" in everyone
    assert {"/SILENT", "/RELAUNCH=1"} <= set(per_user)


def test_fr44_installer_relaunches_after_a_silent_update() -> None:
    script = (Path(__file__).parents[1] / "packaging" / "installer.iss").read_text()
    assert "Check: ShouldRelaunch" in script and "{param:RELAUNCH|0}" in script


# ---------------------------------------------------------------------------- UI
def test_fr44_update_now_downloads_waits_and_quits(  # type: ignore[no-untyped-def]
    qtbot, window, bridge, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(updates, "fetch_latest_tag", lambda: "v9.0.0")
    bridge.can_self_update = True
    launched: list[Path] = []
    installer = tmp_path / "WeFlow-Setup-9.0.0.exe"
    monkeypatch.setattr(self_update, "download", lambda v, f, p: (p(50, 100), installer)[1])
    monkeypatch.setattr(self_update, "launch", launched.append)
    with qtbot.waitSignal(bridge.update_changed, timeout=3000):
        bridge.check_for_updates()
    bar = window.notice_bar
    assert bar.action.text() == "Update now"

    with qtbot.waitSignal(window.quit_requested, timeout=3000):
        qtbot.mouseClick(bar.action, Qt.MouseButton.LeftButton)
    assert launched == [installer]
    assert bar.text.text().startswith("Installing WeFlow 9.0.0")


def test_fr44_failed_download_says_so_and_offers_again(  # type: ignore[no-untyped-def]
    qtbot, window, bridge, monkeypatch
) -> None:
    monkeypatch.setattr(updates, "fetch_latest_tag", lambda: "v9.0.0")
    bridge.can_self_update = True

    def fail(*_: object) -> Path:
        raise UpdateFailed("offline")

    monkeypatch.setattr(self_update, "download", fail)
    with qtbot.waitSignal(bridge.update_changed, timeout=3000):
        bridge.check_for_updates()
    with qtbot.waitSignal(bridge.update_install_changed, timeout=3000) as blocker:
        bridge.install_update()
    qtbot.waitUntil(lambda: not bridge.installing_update, timeout=3000)
    assert window.notice_bar.text.text() == "The update could not be downloaded. Try again later."
    assert blocker.args is not None


def test_fr44_update_waits_while_photos_are_working(  # type: ignore[no-untyped-def]
    qtbot, window, bridge, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(updates, "fetch_latest_tag", lambda: "v9.0.0")
    launched: list[Path] = []
    monkeypatch.setattr(self_update, "download", lambda v, f, p: tmp_path / "setup.exe")
    monkeypatch.setattr(self_update, "launch", launched.append)
    busy = [True]
    monkeypatch.setattr(bridge, "has_pending", lambda: busy[0])
    with qtbot.waitSignal(bridge.update_changed, timeout=3000):
        bridge.check_for_updates()
    bridge.install_update()
    qtbot.waitUntil(lambda: "ready" in window.notice_bar.text.text(), timeout=3000)
    assert launched == []
    busy[0] = False
    with qtbot.waitSignal(window.quit_requested, timeout=5000):
        pass
    assert launched == [tmp_path / "setup.exe"]
