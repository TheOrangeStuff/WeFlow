"""Update check: ask the public WeFlow repo, offer the download, never nag."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt

from tests.ui_states import batch
from WeFlow.core import updates
from WeFlow.core.updates import (
    DOWNLOAD_PAGE,
    LATEST_API,
    OLD_RELEASES_REPO,
    RELEASES_REPO,
    Update,
)
from WeFlow.ui.bridge import Bridge
from WeFlow.ui.settings_dialog import SettingsDialog

ROOT = Path(__file__).resolve().parent.parent


# -------------------------------------------------------------------------- core
def test_update_versions_compare_as_numbers() -> None:
    assert updates.parse_version("v1.10.0") == (1, 10)
    assert updates.parse_version("1.2") == updates.parse_version("1.2.0")
    assert updates.parse_version("1.10.0") > updates.parse_version("1.9.3")  # type: ignore[operator]
    for bad in ("", "latest", "1.2-beta", "v1.2.0rc1", "1..2", "<b>2.0</b>"):
        assert updates.parse_version(bad) is None


@pytest.mark.parametrize(
    ("tag", "found"),
    [
        ("v1.1.0", Update("1.1.0")),
        ("2.0", Update("2.0")),
        ("v1.0.0", None),  # the same version
        ("v0.9.0", None),  # older
        ("latest", None),  # not a version
        ("", None),  # nothing published yet
    ],
)
def test_update_check_offers_only_newer_versions(tag: str, found: Update | None) -> None:
    assert updates.check("1.0.0", lambda: tag) == found
    if found is not None:
        assert found.url == DOWNLOAD_PAGE == f"https://github.com/{RELEASES_REPO}/releases/latest"


@pytest.mark.parametrize("error", [OSError("offline"), TimeoutError(), ValueError("bad json")])
def test_update_check_never_raises(error: Exception) -> None:
    def fetch() -> str:
        raise error

    assert updates.check("1.0.0", fetch) is None


class FakeResponse:
    def __init__(self, status: int, body: Any) -> None:
        self.status_code, self.body = status, body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise OSError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self.body


def test_update_fetch_asks_the_public_repo_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import requests

    calls: list[tuple[str, dict[str, Any]]] = []
    replies = [
        FakeResponse(200, {"tag_name": "v1.4.0", "html_url": "https://example.invalid/x"}),
        FakeResponse(404, {"message": "Not Found"}),
        FakeResponse(200, ["unexpected"]),
        FakeResponse(403, {"message": "rate limit"}),
    ]

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        calls.append((url, kwargs))
        return replies[len(calls) - 1]

    monkeypatch.undo()  # drop the autouse stub: this test exercises the real fetch
    monkeypatch.setattr(requests, "get", fake_get)
    assert updates.fetch_latest_tag() == "v1.4.0"
    assert updates.fetch_latest_tag() == ""  # no release yet
    assert updates.fetch_latest_tag() == ""  # not the JSON we expect
    assert updates.check("1.0.0") is None  # rate-limited: quiet, no update
    url, kwargs = calls[0]
    assert url == LATEST_API == f"https://api.github.com/repos/{RELEASES_REPO}/releases/latest"
    assert kwargs["timeout"] == updates.TIMEOUT_SECONDS
    assert "Authorization" not in kwargs["headers"]


def test_update_checks_the_main_repo_and_ci_copies_to_the_old_one() -> None:
    """New apps check the source repo; ci.yml still copies releases for 1.18.0 and older."""
    assert RELEASES_REPO == "TheOrangeStuff/WeFlow"
    assert OLD_RELEASES_REPO == "TheOrangeStuff/weflow-releases"
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert r"""sed -n 's/^OLD_RELEASES_REPO = "\(.*\)"$/\1/p' WeFlow/core/updates.py""" in workflow
    source = (ROOT / "WeFlow" / "core" / "updates.py").read_text(encoding="utf-8")
    assert re.findall(r'^OLD_RELEASES_REPO = "(.*)"$', source, re.M) == [OLD_RELEASES_REPO]


# ---------------------------------------------------------------------------- UI
@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    urls: list[str] = []
    monkeypatch.setattr(Bridge, "open_url", staticmethod(urls.append))
    return urls


def publish(monkeypatch: pytest.MonkeyPatch, tag: str) -> list[int]:
    calls: list[int] = []

    def fetch() -> str:
        calls.append(1)
        return tag

    monkeypatch.setattr(updates, "fetch_latest_tag", fetch)
    return calls


def test_update_offer_download_opens_the_release_page(  # type: ignore[no-untyped-def]
    qtbot, window, bridge, store, monkeypatch, opened
) -> None:
    bar = window.notice_bar
    bridge.check_for_updates()  # nothing published: no offer
    qtbot.wait(100)
    assert bridge.update is None and not bar.isVisible()

    publish(monkeypatch, "v9.0.0")
    with qtbot.waitSignal(bridge.update_changed, timeout=3000):
        bridge.check_for_updates()
    assert bar.isVisible() and bar.offering_update()
    assert bar.text.text() == "WeFlow 9.0.0 is available"
    assert (bar.action.text(), bar.cancel.text()) == ("Download", "Skip")

    qtbot.mouseClick(bar.action, Qt.MouseButton.LeftButton)
    assert opened == [DOWNLOAD_PAGE]
    assert not bar.isVisible() and store.settings.skipped_update == ""
    window.refresh()
    assert not bar.isVisible()  # answered for this session


def test_update_skip_is_remembered_and_settings_still_lists_it(  # type: ignore[no-untyped-def]
    qtbot, window, bridge, store, monkeypatch, opened
) -> None:
    publish(monkeypatch, "v9.0.0")
    with qtbot.waitSignal(bridge.update_changed, timeout=3000):
        bridge.check_for_updates()
    qtbot.mouseClick(window.notice_bar.cancel, Qt.MouseButton.LeftButton)
    assert store.settings.skipped_update == "9.0.0"
    assert not window.notice_bar.isVisible() and opened == []
    window.refresh()
    assert not window.notice_bar.isVisible()

    dialog = SettingsDialog(bridge, window)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.update_link.isVisible() and dialog.update_link.text() == "Download 9.0.0"
    dialog.update_link.click()
    assert opened == [DOWNLOAD_PAGE]

    publish(monkeypatch, "v9.1.0")  # a later version is offered again
    with qtbot.waitSignal(bridge.update_changed, timeout=3000):
        bridge.check_for_updates()
    assert window.notice_bar.text.text() == "WeFlow 9.1.0 is available"
    assert dialog.update_link.text() == "Download 9.1.0"


def test_update_offer_never_competes_with_a_card(  # type: ignore[no-untyped-def]
    qtbot, window, bridge, event, monkeypatch
) -> None:
    bar = window.notice_bar
    working = batch("copying", event_id=event.id, label="CARD_A", copied=3)
    bridge.batch_updated.emit(working)
    monkeypatch.setattr(bridge.pipeline, "has_pending", lambda event_id=None: True)
    publish(monkeypatch, "v9.0.0")
    with qtbot.waitSignal(bridge.update_changed, timeout=3000):
        bridge.check_for_updates()
    assert window.status() == "working" and not bar.isVisible()

    monkeypatch.setattr(bridge.pipeline, "has_pending", lambda event_id=None: False)
    bridge.batch_updated.emit(batch("done", event_id=event.id, label="CARD_A", copied=3))
    assert bar.offering_update()

    bar.show_card("/Volumes/CARD_B", "CARD_B", "Test Event")  # the countdown takes the bar
    assert bar.text.text() == "Card CARD_B → Test Event"
    assert not bar.offering_update() and bar.cancel.text() == "Cancel"
    bar.dismiss_card("/Volumes/CARD_B")
    assert bar.offering_update() and bar.text.text() == "WeFlow 9.0.0 is available"

    bar.message_ms = 50  # a short result shows first, then the offer returns
    bar.show_message("safe", "Card ejected. You can take it out.")
    assert not bar.offering_update() and bar.cancel.text() == "Dismiss"
    qtbot.waitUntil(bar.offering_update, timeout=2000)


def test_update_check_can_be_switched_off(  # type: ignore[no-untyped-def]
    qtbot, window, bridge, store, monkeypatch
) -> None:
    calls = publish(monkeypatch, "v9.0.0")
    dialog = SettingsDialog(bridge, window)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.updates.isChecked() and not dialog.update_link.isVisible()

    dialog.updates.click()
    assert store.settings.check_updates is False
    bridge.check_for_updates()
    qtbot.wait(100)
    assert calls == [] and bridge.update is None

    with qtbot.waitSignal(bridge.update_changed, timeout=3000):
        dialog.updates.click()  # switching it on checks at once
    assert calls == [1] and window.notice_bar.offering_update()
    assert dialog.update_link.text() == "Download 9.0.0"

    dialog.updates.click()  # off again: the offer goes away
    assert bridge.update is None and not window.notice_bar.isVisible()
    assert not dialog.update_link.isVisible()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_update_screenshots_for_review(qtbot, window, bridge, scheme: str) -> None:  # type: ignore[no-untyped-def]
    from WeFlow.ui.theme import theme

    artifacts = ROOT / "tests" / "artifacts"
    artifacts.mkdir(exist_ok=True)
    theme.set_scheme(scheme)
    bridge.update = Update("1.1.0")
    bridge.update_changed.emit()
    dialog = SettingsDialog(bridge, window)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.wait(30)
    assert window.notice_bar.offering_update()
    assert window.grab().save(str(artifacts / f"main-update-{scheme}.png"))
    assert dialog.grab().save(str(artifacts / f"settings-update-{scheme}.png"))
    theme.set_scheme("light")


# -------------------------------------------------------------------- what's new
CHANGES = """# Changelog

## Unreleased

- Not out yet.

## 1.1.0 — 2026-12-01

Thanks for updating.

- WeFlow tells you when a **newer version** is out, with a
  [download page](https://example.invalid/releases).
- Press `Skip` to hide a version.

### Fixes
- Eject works on more card readers.
  - Including USB hubs.

## 1.0.0

- First release.
"""


def test_whats_new_reads_the_bullets_of_one_version(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGES, encoding="utf-8")
    expected = [
        "WeFlow tells you when a newer version is out, with a download page.",
        "Press Skip to hide a version.",
        "Eject works on more card readers.",
        "Including USB hubs.",
    ]
    assert updates.whats_new("1.1.0", changelog) == expected
    assert updates.whats_new("1.1", changelog) == expected
    assert updates.whats_new("1.0.0", changelog) == ["First release."]
    assert updates.whats_new("2.0.0", changelog) == []  # no section
    assert updates.whats_new("Unreleased", changelog) == []
    assert updates.whats_new("1.1.0", tmp_path / "missing.md") == []


def test_whats_new_since_lists_every_skipped_version(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## 1.2.0\n\n- Two.\n\n" + CHANGES[12:], encoding="utf-8")
    notes = updates.release_notes(changelog)
    assert [v for v, _ in notes] == ["1.2.0", "1.1.0", "1.0.0"]  # "Unreleased" left out
    since = updates.whats_new_since
    assert [v for v, _ in since("1.0.0", "1.2.0", changelog)] == ["1.2.0", "1.1.0"]
    assert since("1.1.0", "1.2.0", changelog) == [("1.2.0", ["Two."])]
    assert [v for v, _ in since("1.0.0", "1.1.0", changelog)] == ["1.1.0"]  # never ahead
    assert since("", "1.2.0", changelog) == [("1.2.0", ["Two."])]  # seen not recorded
    assert since("1.2.0", "1.2.0", changelog) == []
    assert since("1.0.0", "1.2.0", tmp_path / "missing.md") == []


def test_whats_new_shows_headings_only_for_several_versions(qtbot) -> None:  # type: ignore[no-untyped-def]
    from WeFlow.ui.whats_new import WhatsNew

    one = WhatsNew("1.2.0", [("1.2.0", ["Two."])])
    several = WhatsNew("1.2.0", [("1.2.0", ["Two."]), ("1.1.0", ["One.", "More."])])
    qtbot.addWidget(one)
    qtbot.addWidget(several)
    assert one.versions == [] and [i.text() for i in one.items] == ["Two."]
    assert [v.text() for v in several.versions] == ["1.2.0", "1.1.0"]
    assert [i.text() for i in several.items] == ["Two.", "One.", "More."]
    several.show()
    artifacts = ROOT / "tests" / "artifacts"
    artifacts.mkdir(exist_ok=True)
    assert several.grab().save(str(artifacts / "whats-new-several-light.png"))


def test_whats_new_exists_for_the_version_being_built() -> None:
    """Bumping the version without writing its changelog section fails here and in CI."""
    from WeFlow import __version__

    assert updates.whats_new(__version__), f"CHANGELOG.md needs a '## {__version__}' section"
    spec = (ROOT / "packaging" / "WeFlow.spec").read_text(encoding="utf-8")
    assert '(str(ROOT / "CHANGELOG.md"), ".")' in spec  # bundled where updates.CHANGELOG looks
    assert updates.CHANGELOG == ROOT / "CHANGELOG.md"


@pytest.fixture
def app(qtbot, qapp, bridge, event, monkeypatch):  # type: ignore[no-untyped-def]
    from WeFlow.ui.app import App

    monkeypatch.setattr(qapp, "quit", lambda: None)
    made = App(qapp, bridge)
    qtbot.addWidget(made.window)
    made.start()
    made.shown = []
    real = made.open_whats_new
    monkeypatch.setattr(made, "open_whats_new", lambda v, notes: made.shown.append(real(v, notes)))
    yield made
    made.tray.hide()


def test_whats_new_shows_once_after_an_update(qtbot, app, bridge, store, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from WeFlow import __version__

    notes = ["WeFlow tells you when a newer version is out.", "Press Skip to hide a version."]
    monkeypatch.setattr(updates, "whats_new_since", lambda seen, now: [(now, notes)])
    assert store.settings.first_run_done and store.settings.seen_version == ""  # as 1.0.0 left it

    app.show_whats_new()
    qtbot.waitUntil(lambda: bool(app.shown), timeout=3000)
    dialog = app.shown[0]
    assert dialog.isVisible() and dialog.windowTitle() == "What's new"
    assert dialog.heading.text() == f"What's new in WeFlow {__version__}"
    assert [item.text() for item in dialog.items] == notes
    assert store.settings.seen_version == __version__
    artifacts = ROOT / "tests" / "artifacts"
    artifacts.mkdir(exist_ok=True)
    assert dialog.grab().save(str(artifacts / "whats-new-light.png"))
    dialog.done_button.click()
    assert not dialog.isVisible()

    app.show_whats_new()  # every later start of the same version
    qtbot.wait(100)
    assert len(app.shown) == 1


@pytest.mark.parametrize("case", ["new install", "older build installed", "no notes"])
def test_whats_new_stays_quiet(qtbot, app, bridge, store, monkeypatch, case: str) -> None:  # type: ignore[no-untyped-def]
    from WeFlow import __version__

    monkeypatch.setattr(updates, "whats_new_since", lambda seen, now: [(now, ["Changed."])])
    if case == "new install":
        bridge.finish_first_run()
    elif case == "older build installed":
        bridge.update_settings(seen_version="99.0.0")
    else:
        monkeypatch.setattr(updates, "whats_new_since", lambda seen, now: [])
    app.show_whats_new()
    qtbot.wait(100)
    assert app.shown == []
    assert store.settings.seen_version == __version__


def test_update_check_now_button_in_settings(  # type: ignore[no-untyped-def]
    qtbot, window, bridge, store, monkeypatch
) -> None:
    from WeFlow import __version__

    store.update_settings(check_updates=False)  # works even with the daily check off
    dialog = SettingsDialog(bridge, window)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.check_now.isVisible() and dialog.check_now.text() == "Check for updates"

    publish(monkeypatch, f"v{__version__}")
    dialog.check_now.click()
    qtbot.waitUntil(lambda: dialog.check_now.text() == "Up to date", timeout=3000)

    def offline() -> str:
        raise OSError("no network")

    monkeypatch.setattr(updates, "fetch_latest_tag", offline)
    dialog.check_now.click()
    qtbot.waitUntil(lambda: dialog.check_now.text().startswith("Can't check"), timeout=3000)
    from tests.test_dialogs import shoot

    shoot(dialog, "settings-check-failed-light")

    publish(monkeypatch, "v9.0.0")
    dialog.check_now.click()
    qtbot.waitUntil(lambda: dialog.update_link.isVisible(), timeout=3000)
    assert dialog.update_link.text() == "Download 9.0.0" and not dialog.check_now.isVisible()
