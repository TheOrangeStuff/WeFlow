from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import pytest

from tests.conftest import PipelineFactory, Recorder
from tests.fixtures.make_card import make_card
from WeFlow.core import ingest
from WeFlow.core.config import Profile, Store
from WeFlow.core.events import BatchUpdated
from WeFlow.core.pipeline import MAX_ATTEMPTS, Pipeline, retry_delay
from WeFlow.core.state import Batch, EventState, PhotoRecord
from WeFlow.core.uploaders import FakeUploader, UploadError

NEW_PHOTOS = 7  # an 8-file fake card holds one duplicate


def flickr_event(store: Store, **changes: object) -> Profile:
    event = store.create_event("Gala")
    event.destination, event.album = "flickr", "Gala Night"
    for key, value in changes.items():
        setattr(event, key, value)
    return store.update_event(event)


def photos(store: Store, event: Profile) -> EventState:
    return EventState(store.event_root(event))


def test_card_goes_copying_safe_processing_uploading_done(
    store: Store, card: Path, recorder: Recorder, make_pipeline: PipelineFactory
) -> None:
    event = flickr_event(store)
    fake = FakeUploader()
    pipeline = make_pipeline(fake)
    batch = pipeline.submit(card, event, "CARD_A", is_card=True)
    done = recorder.wait_state(batch.id, "done")
    states = recorder.states(batch.id)
    assert states[0] == "copying" and states[-1] == "done"
    assert states.index("safe") < states.index("done")
    assert (done.total, done.copied, done.skipped) == (8, NEW_PHOTOS, 1)  # FR-14
    assert (done.processed, done.uploaded, done.failed) == (NEW_PHOTOS, NEW_PHOTOS, 0)
    assert sorted(fake.uploads) == sorted(
        p.name for p in (store.event_root(event) / "processed").iterdir()
    )
    assert len(fake.album_adds) == NEW_PHOTOS
    assert [n.level for n in recorder.notices("safe")] == ["safe"]
    root = store.event_root(event)
    assert len(list((root / "originals").iterdir())) == NEW_PHOTOS
    assert all(p.name.startswith("Gala_2026") for p in (root / "originals").iterdir())


def test_fr14_second_insert_counts_already_imported(
    store: Store, card: Path, recorder: Recorder, make_pipeline: PipelineFactory
) -> None:
    event = flickr_event(store)
    fake = FakeUploader()
    pipeline = make_pipeline(fake)
    first = pipeline.submit(card, event, "CARD_A", is_card=True)
    recorder.wait_state(first.id, "done")
    second = pipeline.submit(card, event, "CARD_A", is_card=True)
    done = recorder.wait_state(second.id, "done")
    assert (done.copied, done.skipped, done.verified) == (0, 8, True)
    assert len(fake.uploads) == NEW_PHOTOS


def test_fr15_safe_only_after_every_file_is_verified(
    store: Store, card: Path, recorder: Recorder, make_pipeline: PipelineFactory
) -> None:
    event = flickr_event(store)
    pipeline = make_pipeline()
    batch = pipeline.submit(card, event, "CARD_A", is_card=True)
    recorder.wait_state(batch.id, "safe")
    for update in recorder.batches(batch.id):
        if update.verified or update.state == "safe":
            assert update.copied + update.skipped == update.total == 8
        else:
            assert update.state == "copying"


def test_fr15_error_batch_never_safe(
    store: Store,
    card: Path,
    recorder: Recorder,
    make_pipeline: PipelineFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real, calls = ingest.hash_file, []

    def flaky_hash(path: Path) -> str:
        calls.append(path)
        return "bad" if len(calls) == 3 else real(path)

    monkeypatch.setattr(ingest, "hash_file", flaky_hash)
    event = flickr_event(store)
    pipeline = make_pipeline()
    batch = pipeline.submit(card, event, "CARD_A", is_card=True)
    failed = recorder.wait_state(batch.id, "error")
    assert failed.verified is False and "did not copy correctly" in failed.error
    time.sleep(0.5)  # the two good copies still get processed and uploaded
    assert "safe" not in recorder.states(batch.id)
    assert all(not b.verified for b in recorder.batches(batch.id))
    assert recorder.notices("safe") == []
    assert recorder.notices("error")
    assert pipeline.snapshot()[0].state == "error"
    assert not list((store.event_root(event) / "originals").glob(".tmp-*"))


def test_fr15_removed_card_is_an_error_not_safe(
    store: Store, tmp_path: Path, recorder: Recorder, make_pipeline: PipelineFactory
) -> None:
    pipeline = make_pipeline()
    batch = pipeline.submit(tmp_path / "volumes" / "GONE", flickr_event(store), "GONE", True)
    failed = recorder.wait_state(batch.id, "error")
    assert not failed.verified and "removed" in failed.error


def test_fr18_folder_import_runs_same_pipeline_without_safe(
    store: Store, tmp_path: Path, recorder: Recorder, make_pipeline: PipelineFactory
) -> None:
    folder = tmp_path / "from-a-friend"
    folder.mkdir()
    shots = make_card(tmp_path / "scratch", count=5)
    for shot in shots:
        shutil.copy(shot, folder / shot.name)
    event = store.create_event("Local only")  # destination "none"
    pipeline = make_pipeline()
    batch = pipeline.submit(folder, event, "from-a-friend", is_card=False)
    done = recorder.wait_state(batch.id, "done")
    assert "safe" not in recorder.states(batch.id) and recorder.notices("safe") == []
    assert (done.copied, done.processed, done.uploaded) == (4, 4, 0)
    assert len(list((store.event_root(event) / "processed").iterdir())) == 4


def test_fr32_album_retry_never_uploads_again(
    store: Store, card: Path, recorder: Recorder, make_pipeline: PipelineFactory
) -> None:
    event = flickr_event(store)
    fake = FakeUploader(album_fail_times=5)
    pipeline = make_pipeline(fake)
    batch = pipeline.submit(card, event, "CARD_A", is_card=True)
    recorder.wait_state(batch.id, "done")
    assert fake.upload_calls == NEW_PHOTOS and len(set(fake.uploads)) == NEW_PHOTOS
    assert fake.album_calls == NEW_PHOTOS + 5
    assert sorted(fake.album_adds) == sorted(f"fake-{n}" for n in fake.uploads)


def test_fr33_retry_schedule() -> None:
    assert [retry_delay(n) for n in range(1, 9)] == [10, 20, 40, 60, 120, 300, 300, 300]
    assert MAX_ATTEMPTS == 12


def test_fr33_temporary_errors_retry_then_succeed(
    store: Store, card: Path, recorder: Recorder, make_pipeline: PipelineFactory
) -> None:
    event = flickr_event(store)
    fake = FakeUploader(fail_times=6)
    asked: list[int] = []

    def delay(attempts: int) -> float:
        asked.append(attempts)
        return 0.01

    pipeline = make_pipeline(fake, retry_delay=delay)
    batch = pipeline.submit(card, event, "CARD_A", is_card=True)
    done = recorder.wait_state(batch.id, "done")
    assert done.uploaded == NEW_PHOTOS and done.failed == 0
    assert len(asked) == 6 and len(set(fake.uploads)) == len(fake.uploads) == NEW_PHOTOS
    assert any("Fake upload failure" in n.message for n in recorder.notices("info"))


def test_fr33_gives_up_after_twelve_attempts(
    store: Store, card: Path, recorder: Recorder, make_pipeline: PipelineFactory
) -> None:
    event = flickr_event(store)
    fake = FakeUploader(fail_times=10_000)
    pipeline = make_pipeline(fake, retry_delay=lambda attempts: 0.0)
    batch = pipeline.submit(card, event, "CARD_A", is_card=True)
    stuck = recorder.wait_state(batch.id, "attention")
    assert stuck.failed == NEW_PHOTOS and fake.upload_calls == NEW_PHOTOS * MAX_ATTEMPTS
    records = photos(store, event).photos.values()
    assert all(p.attempts == MAX_ATTEMPTS and p.failed_stage == "upload" for p in records)


def test_fr33_permanent_error_fails_at_once_with_plain_message(
    store: Store, card: Path, recorder: Recorder, make_pipeline: PipelineFactory
) -> None:
    class Expired(FakeUploader):
        def upload(self, path: Path, event: Profile) -> str:
            self.upload_calls += 1
            raise UploadError("Flickr sign-in has expired. Reconnect it in Settings.", True)

    event = flickr_event(store)
    fake = Expired()
    pipeline = make_pipeline(fake)
    batch = pipeline.submit(card, event, "CARD_A", is_card=True)
    stuck = recorder.wait_state(batch.id, "attention")
    assert fake.upload_calls == NEW_PHOTOS  # one try each, no retries
    assert stuck.error == "7 photos failed. Flickr sign-in has expired. Reconnect it in Settings."
    assert recorder.notices("error")


def test_fr34_hold_then_release(
    store: Store, card: Path, recorder: Recorder, make_pipeline: PipelineFactory
) -> None:
    event = flickr_event(store, hold_uploads=True)
    fake = FakeUploader()
    pipeline = make_pipeline(fake)
    batch = pipeline.submit(card, event, "CARD_A", is_card=True)
    held = recorder.wait_state(batch.id, "held")
    assert held.held == NEW_PHOTOS and fake.upload_calls == 0
    assert pipeline.release_held("some-other-event") == 0
    assert pipeline.release_held(event.id) == NEW_PHOTOS
    done = recorder.wait_state(batch.id, "done")
    assert done.uploaded == NEW_PHOTOS and done.held == 0
    assert pipeline.release_held(event.id) == 0


def test_fr35_retry_failed_requeues_at_failed_stage(
    store: Store, card: Path, recorder: Recorder, make_pipeline: PipelineFactory
) -> None:
    event = flickr_event(store)
    fake = FakeUploader(album_fail_times=NEW_PHOTOS, permanent=True)
    pipeline = make_pipeline(fake)
    batch = pipeline.submit(card, event, "CARD_A", is_card=True)
    stuck = recorder.wait_state(batch.id, "attention")
    assert stuck.failed == NEW_PHOTOS and fake.upload_calls == NEW_PHOTOS
    assert all(p.failed_stage == "album" for p in photos(store, event).photos.values())
    assert pipeline.retry_failed(event.id) == NEW_PHOTOS
    recorder.wait(lambda: recorder.batches(batch.id)[-1].state == "done")
    assert fake.upload_calls == NEW_PHOTOS  # only the album step ran again
    assert len(fake.album_adds) == NEW_PHOTOS
    assert all(p.attempts == 0 for p in photos(store, event).photos.values())


def test_fr35_retry_failed_processing(
    store: Store, card: Path, recorder: Recorder, make_pipeline: PipelineFactory
) -> None:
    event = flickr_event(store, watermark=True, watermark_path=str(card.parent / "missing.png"))
    fake = FakeUploader()
    pipeline = make_pipeline(fake)
    batch = pipeline.submit(card, event, "CARD_A", is_card=True)
    stuck = recorder.wait_state(batch.id, "attention")
    assert stuck.failed == NEW_PHOTOS and stuck.processed == 0 and "watermark" in stuck.error
    event.watermark = False
    store.update_event(event)
    assert pipeline.retry_failed() == NEW_PHOTOS
    recorder.wait(lambda: recorder.batches(batch.id)[-1].state == "done")
    assert len(fake.uploads) == NEW_PHOTOS


def test_fr36_kill_and_restart_mid_run_uploads_each_photo_once(
    store: Store, card: Path, recorder: Recorder, make_pipeline: PipelineFactory, tmp_path: Path
) -> None:
    """Freeze the library as it is on disk mid-upload (a hard kill), then relaunch."""
    event = flickr_event(store)
    gate = threading.Event()

    class Stalls(FakeUploader):
        def upload(self, path: Path, event: Profile) -> str:
            if len(self.uploads) >= 3:
                gate.wait(10)
                raise UploadError("offline")
            return super().upload(path, event)

    first = Stalls()
    pipeline = make_pipeline(first, upload_workers=1)
    batch = pipeline.submit(card, event, "CARD_A", is_card=True)
    recorder.wait(lambda: len(first.album_adds) >= 3)
    time.sleep(0.2)
    frozen = tmp_path / "library-at-kill"
    shutil.copytree(store.library, frozen)  # journal and all, exactly as a kill leaves it
    gate.set()
    pipeline.stop()

    store.update_settings(library=str(frozen))
    relaunch = Recorder()
    second = FakeUploader()
    restarted = Pipeline(store, relaunch, uploader_factory=lambda d: second, process_workers=2)
    try:
        restarted.resume()
        relaunch.wait_state(batch.id, "done")
        assert sorted(first.uploads + second.uploads) == sorted(
            p.name for p in (frozen / event.folder / "processed").iterdir()
        )
        assert len(second.uploads) == NEW_PHOTOS - 3
        resumed = restarted.snapshot()[0]
        assert resumed.previous_session and resumed.uploaded == NEW_PHOTOS
    finally:
        restarted.stop()


def test_fr36_resume_album_step_only_for_photos_already_up(
    store: Store, card: Path, recorder: Recorder, make_pipeline: PipelineFactory
) -> None:
    event = flickr_event(store)
    first = FakeUploader(album_fail_times=10_000)
    pipeline = make_pipeline(first, retry_delay=lambda attempts: 30.0)
    batch = pipeline.submit(card, event, "CARD_A", is_card=True)
    recorder.wait(lambda: len(first.uploads) == NEW_PHOTOS)
    pipeline.stop()

    relaunch = Recorder()
    second = FakeUploader()
    restarted = Pipeline(store, relaunch, uploader_factory=lambda d: second, process_workers=2)
    try:
        restarted.resume()
        relaunch.wait_state(batch.id, "done")
        assert second.upload_calls == 0 and len(second.album_adds) == NEW_PHOTOS
    finally:
        restarted.stop()


def test_nfr05_kill_during_copy_is_never_safe_after_restart(
    store: Store,
    card: Path,
    recorder: Recorder,
    make_pipeline: PipelineFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = flickr_event(store)
    reached, release = threading.Event(), threading.Event()
    real = ingest.ingest_file

    def slow_ingest(source: Path, *args: object) -> object:
        if source.name == "IMG_0004.jpg":
            reached.set()
            release.wait(10)
        return real(source, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(ingest, "ingest_file", slow_ingest)
    pipeline = make_pipeline()
    batch = pipeline.submit(card, event, "CARD_A", is_card=True)
    assert reached.wait(10)
    stopper = threading.Thread(target=pipeline.stop)
    stopper.start()
    time.sleep(0.1)
    release.set()
    stopper.join()
    assert "safe" not in recorder.states(batch.id)

    relaunch = Recorder()
    fake = FakeUploader()
    restarted = Pipeline(store, relaunch, uploader_factory=lambda d: fake, process_workers=2)
    try:
        restarted.resume()
        after = relaunch.wait_state(batch.id, "error")
        assert not after.verified and "interrupted" in after.error
        relaunch.wait(lambda: len(fake.uploads) >= 3)  # photos already copied are not lost
        assert "safe" not in relaunch.states(batch.id)
        # Re-inserting the card finishes the job without duplicates.
        again = restarted.submit(card, event, "CARD_A", is_card=True)
        done = relaunch.wait_state(again.id, "done")
        assert done.verified and done.copied + done.skipped == 8
        relaunch.wait(lambda: len(fake.uploads) == NEW_PHOTOS)
        assert len(set(fake.uploads)) == NEW_PHOTOS
    finally:
        restarted.stop()


def test_batch_updates_are_coalesced(
    store: Store, tmp_path: Path, recorder: Recorder, make_pipeline: PipelineFactory
) -> None:
    make_card(tmp_path / "volumes" / "BIG", count=60, size=(96, 64))
    event = store.create_event("Local")
    pipeline = make_pipeline()
    started = time.monotonic()
    batch = pipeline.submit(tmp_path / "volumes" / "BIG", event, "BIG", is_card=True)
    recorder.wait_state(batch.id, "done")
    elapsed = time.monotonic() - started
    updates = [e for e in recorder.events if isinstance(e, BatchUpdated)]
    # ≤ 10/s per batch, plus the few forced emits at state changes.
    assert len(updates) <= elapsed * 10 + 6
    assert updates[-1].batch.processed == 59


def test_state_survives_torn_journal_line(store: Store, tmp_path: Path) -> None:
    from WeFlow.core.state import Batch, PhotoRecord

    state = EventState(tmp_path / "ev")
    state.put_batch(Batch(id=1, event_id="e"))
    state.put_photo(PhotoRecord("a.jpg", "h1", batch_id=1))
    state.sync()
    assert state._journal is not None
    state._journal.close()  # the process died: its handle is gone
    with open(state.journal_path, "a", encoding="utf-8") as fh:
        fh.write('{"photo": {"name": "b.jpg", "sha2')  # the kill landed mid-write
    loaded = EventState(tmp_path / "ev")
    assert list(loaded.photos) == ["a.jpg"] and list(loaded.batches) == [1]
    loaded.compact()
    assert not loaded.journal_path.exists()
    assert list(EventState(tmp_path / "ev").photos) == ["a.jpg"]


def test_stop_is_quick_and_idempotent(store: Store, make_pipeline: PipelineFactory) -> None:
    pipeline = make_pipeline()
    started = time.monotonic()
    pipeline.stop(timeout=5)
    pipeline.stop(timeout=5)
    assert time.monotonic() - started < 2
    assert pipeline.snapshot() == [] and not pipeline.has_pending()


def test_unexpected_uploader_crash_never_strands_a_photo(
    store: Store, card: Path, recorder: Recorder, make_pipeline: PipelineFactory
) -> None:
    class Buggy(FakeUploader):
        def upload(self, path: Path, event: Profile) -> str:
            if self.upload_calls < 3:
                self.upload_calls += 1
                raise RuntimeError("bug in the uploader")
            return super().upload(path, event)

    event = flickr_event(store)
    fake = Buggy()
    pipeline = make_pipeline(fake)
    batch = pipeline.submit(card, event, "CARD_A", is_card=True)
    done = recorder.wait_state(batch.id, "done")
    assert done.uploaded == NEW_PHOTOS and len(set(fake.uploads)) == NEW_PHOTOS
    assert all("bug in the uploader" not in n.message for n in recorder.notices())  # NFR-09


def test_nfr06_keychain_failure_is_plain_and_permanent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from WeFlow.core import secrets, uploaders

    def locked(name: str) -> str:
        raise secrets.SecretsError("The system keychain is not available.")

    monkeypatch.setattr(secrets, "get_secret", locked)
    assert uploaders.is_connected("smugmug") is False
    for uploader in (uploaders.SmugMugUploader(), uploaders.FlickrUploader()):
        with pytest.raises(UploadError) as caught:
            uploader.upload(tmp_path / "a.jpg", Profile(name="G", album="KEY"))
        assert caught.value.permanent and "keychain" in caught.value.message


def test_fr26_processed_copies_and_uploads_use_the_naming_rule(
    store: Store, card: Path, recorder: Recorder, make_pipeline: PipelineFactory
) -> None:
    event = flickr_event(store, file_naming="custom_seq", custom_name="Smith Wedding")
    fake = FakeUploader()
    pipeline = make_pipeline(fake)
    batch = pipeline.submit(card, event, "CARD_A", is_card=True)
    recorder.wait_state(batch.id, "done")
    root = store.event_root(event)
    expected = [f"Smith-Wedding-{n:04d}.jpg" for n in range(1, NEW_PHOTOS + 1)]
    assert sorted(p.name for p in (root / "processed").iterdir()) == expected
    assert sorted(fake.uploads) == expected
    assert all(p.name.startswith("Gala_2026") for p in (root / "originals").iterdir())
    records = sorted(photos(store, event).photos.values(), key=lambda p: p.seq)
    assert [p.seq for p in records] == list(range(1, NEW_PHOTOS + 1))
    assert all(p.original and p.original in p.name for p in records)  # card name kept


def test_fr26_name_is_fixed_once_processed(
    store: Store, card: Path, recorder: Recorder, make_pipeline: PipelineFactory
) -> None:
    """Changing the rule mid-event never renames photos that already have a name."""
    event = flickr_event(store, file_naming="filename_seq")
    fake = FakeUploader(album_fail_times=NEW_PHOTOS, permanent=True)
    pipeline = make_pipeline(fake)
    batch = pipeline.submit(card, event, "CARD_A", is_card=True)
    recorder.wait_state(batch.id, "attention")
    names = {p.name: p.output for p in photos(store, event).photos.values()}
    event.file_naming = "custom"
    store.update_event(event)
    pipeline.retry_failed(event.id)
    recorder.wait(lambda: recorder.batches(batch.id)[-1].state == "done")
    assert {p.name: p.output for p in photos(store, event).photos.values()} == names
    assert fake.upload_calls == NEW_PHOTOS


def test_fr26_custom_name_collisions_and_old_photos(
    store: Store, card: Path, recorder: Recorder, make_pipeline: PipelineFactory
) -> None:
    event = flickr_event(store, file_naming="custom", custom_name="Gala")
    fake = FakeUploader()
    pipeline = make_pipeline(fake)
    batch = pipeline.submit(card, event, "CARD_A", is_card=True)
    recorder.wait_state(batch.id, "done")
    processed = sorted(p.name for p in (store.event_root(event) / "processed").iterdir())
    assert processed == sorted(["Gala.jpg"] + [f"Gala-{n}.jpg" for n in range(2, NEW_PHOTOS + 1)])
    assert len(set(fake.uploads)) == NEW_PHOTOS


def test_fr26_photos_imported_before_keep_working(
    store: Store, card: Path, recorder: Recorder, make_pipeline: PipelineFactory
) -> None:
    """A 1.1.0 record (no output, seq or original) is named and uploaded on resume."""
    event = flickr_event(store, file_naming="filename")
    state = photos(store, event)
    state.originals.mkdir(parents=True)
    name = "Gala_20261111-143000_DSC_0007.jpg"
    shutil.copy(ingest.list_jpegs(card)[0], state.originals / name)
    state.put_batch(
        Batch(id=1, event_id=event.id, destination="flickr", state="processing", verified=True)
    )
    state.put_photo(PhotoRecord(name, "abc", "2026-11-11T14:30:00", 1))
    state.close()
    fake = FakeUploader()
    pipeline = make_pipeline(fake)
    pipeline.resume()
    recorder.wait_state(1, "done")
    assert fake.uploads == ["DSC_0007.jpg"]
    assert photos(store, event).photos[name].seq == 1
