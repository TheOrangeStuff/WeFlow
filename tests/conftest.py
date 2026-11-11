from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.fixtures.make_card import make_card  # noqa: E402
from WeFlow.core import secrets  # noqa: E402
from WeFlow.core.config import Profile, Store  # noqa: E402
from WeFlow.core.events import BatchUpdated, Event, Notice  # noqa: E402
from WeFlow.core.pipeline import Pipeline  # noqa: E402
from WeFlow.core.state import Batch  # noqa: E402
from WeFlow.core.uploaders import FakeUploader, Uploader  # noqa: E402


@pytest.fixture(autouse=True)
def memory_secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> secrets.MemoryBackend:
    """No test may touch the real keychain, app-data folder or volumes."""
    backend = secrets.MemoryBackend()
    monkeypatch.setattr(secrets, "_backend", backend)
    monkeypatch.setenv("WeFlow_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("WeFlow_FAKE_VOLUMES", str(tmp_path / "volumes"))
    (tmp_path / "volumes").mkdir()
    return backend


@pytest.fixture
def store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "appdata")
    store.load()
    store.update_settings(library=str(tmp_path / "library"), first_run_done=True)
    return store


@pytest.fixture
def event(store: Store) -> Profile:
    return store.create_event("Test Event")


@pytest.fixture
def card(tmp_path: Path) -> Path:
    target = tmp_path / "volumes" / "CARD_A"
    make_card(target, count=8)
    return target


class Recorder:
    """Collects core events and waits for conditions on them."""

    def __init__(self) -> None:
        self.events: list[Event] = []
        self._cond = threading.Condition()

    def __call__(self, event: Event) -> None:
        with self._cond:
            self.events.append(event)
            self._cond.notify_all()

    def batches(self, batch_id: int | None = None) -> list[Batch]:
        with self._cond:
            return [
                e.batch
                for e in self.events
                if isinstance(e, BatchUpdated) and batch_id in (None, e.batch.id)
            ]

    def states(self, batch_id: int | None = None) -> list[str]:
        out: list[str] = []
        for batch in self.batches(batch_id):
            if not out or out[-1] != batch.state:
                out.append(batch.state)
        return out

    def notices(self, level: str | None = None) -> list[Notice]:
        with self._cond:
            return [e for e in self.events if isinstance(e, Notice) and level in (None, e.level)]

    def last(self, batch_id: int) -> Batch | None:
        found = self.batches(batch_id)
        return found[-1] if found else None

    def wait(self, predicate: Callable[[], bool], timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        with self._cond:
            while not predicate():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(f"timed out; saw states {self.states()}")
                self._cond.wait(min(remaining, 0.05))

    def wait_state(self, batch_id: int, state: str, timeout: float = 20.0) -> Batch:
        self.wait(lambda: any(b.state == state for b in self.batches(batch_id)), timeout)
        return next(b for b in reversed(self.batches(batch_id)) if b.state == state)


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


PipelineFactory = Callable[..., Pipeline]


@pytest.fixture
def make_pipeline(store: Store, recorder: Recorder) -> Iterator[PipelineFactory]:
    made: list[Pipeline] = []

    def factory(
        uploader: Uploader | None = None,
        retry_delay: Callable[[int], float] = lambda attempts: 0.01,
        store_: Store | None = None,
        **kwargs: object,
    ) -> Pipeline:
        fake = uploader or FakeUploader()
        pipeline = Pipeline(
            store_ or store,
            recorder,
            uploader_factory=lambda destination: fake,
            retry_delay=retry_delay,
            process_workers=2,
            **kwargs,  # type: ignore[arg-type]
        )
        made.append(pipeline)
        return pipeline

    yield factory
    for pipeline in made:
        pipeline.stop(timeout=5)


# ------------------------------------------------------------------ UI fixtures
@pytest.fixture
def fake_uploader() -> FakeUploader:
    return FakeUploader()


@pytest.fixture
def bridge(
    qapp: object, store: Store, fake_uploader: FakeUploader, monkeypatch: pytest.MonkeyPatch
):  # type: ignore[no-untyped-def]
    from WeFlow.ui import theme as theme_module
    from WeFlow.ui.bridge import Bridge

    monkeypatch.setenv("WeFlow_NO_MOTION", "1")
    theme_module.install(qapp)  # type: ignore[arg-type]
    theme_module.theme.set_scheme("light")
    made = Bridge(store, uploader_factory=lambda destination: fake_uploader, watch_interval=0.05)
    yield made
    made.shutdown()


@pytest.fixture
def window(qtbot, bridge, event: Profile):  # type: ignore[no-untyped-def]
    from WeFlow.ui.main_window import MainWindow

    win = MainWindow(bridge)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    return win
