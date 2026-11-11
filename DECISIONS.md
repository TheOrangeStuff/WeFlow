# Decisions and deviations from SPEC.md

Each entry says what was chosen and why. Entries marked **(confirmed)** were put to the
owner and accepted on 2026-11-11.

## Process

1. **No v0.1 prototype existed in the repo.** The core (`config`, `cards`, `ingest`,
   `imaging`, `uploaders`, `pipeline`) was written from SPEC §4–§5 and §8. M1 ("move v0.1")
   and M2 ("harden") were therefore done as one step, and "v0.1 behaviour unchanged"
   could not be checked.
2. **Milestones were built back to back** without stopping for review after each,
   at the owner's request. Each still has its own CHANGELOG section.
3. **Legacy secret names are a guess, and will stay one.** The owner no longer has the
   v0.1 code. The migration (§4.4) moves these keys out of a v0.1 `settings.json` if
   present: `smugmug_api_key`, `smugmug_api_secret`, `smugmug_token`,
   `smugmug_token_secret`, and the same four for `flickr_`. Anyone whose old file used
   other names simply connects again in Settings.

## Dependencies and tooling

4. **`PySide6-Essentials` instead of `PySide6`.** Same Qt Widgets and QtSvg, without
   WebEngine and the other add-ons; the installer is several hundred MB smaller.
5. **PySide6 pinned to 6.11.3, not 6.12.0.** On Python 3.11, every `Signal.emit()` in
   6.12.0 drops a reference to `True`, which ends in a fatal error at interpreter exit.
   6.8–6.11 do not do this.
6. **Tests run on Python 3.11; installers are built on 3.12.** Both are "3.11+".
7. **ruff ignores SIM112** because the spec names the variable `WeFlow_FAKE_VOLUMES`
   (mixed case). `WeFlow_DATA_DIR`, `WeFlow_THEME` and `WeFlow_NO_MOTION` follow the
   same pattern (data folder override; forced theme and no animation for tests).
8. **macOS x86_64 is built on the `macos-15-intel` runner** and marked optional in CI, so
   the run stays green if GitHub retires that runner. Builds are per architecture, not
   universal, because NumPy and Pillow wheels are not universal2.

## Layout

9. **Extra modules.** Core: `state.py` (Batch, PhotoRecord and their persistence),
   `logs.py` (NFR-10), `autostart.py` (start at login). UI: `tray.py`,
   `widgets/common.py` (Button, Toggle, Icon, ThinProgress), `widgets/notice_bar.py`.
10. **The UI imports core types from `ui/bridge.py`**, which re-exports them. A test
    fails if any other UI file imports `WeFlow.core`.

## Data model and persistence

11. **`state.json` plus `journal.jsonl`.** To save after every file transition (NFR-05)
    without rewriting the whole file thousands of times, each change is appended to
    `.WeFlow/journal.jsonl` and folded into `state.json` every 500 entries and on exit.
    Loading reads both. A torn last line (kill mid-write) is ignored. **(confirmed)**
12. **Batch gained three fields:** `destination` (the event's destination when the batch
    started, so later edits to the event do not strand it), `verified` (every file copied
    and checked), and `previous_session`. Batch ids are millisecond timestamps, unique
    across events and restarts.
13. **A batch stays in `safe` until its first photo is processed**, then moves to
    `processing`. "Safe to remove" in the window does not depend on that: it shows while
    `verified` is true and the card is still in, and it wins over Processing, Uploading,
    Held, Done and Attention until the card comes out. **(confirmed)**
14. **After a copy error, photos that did copy are still processed and uploaded.** The
    batch stays `error` (never safe). Re-inserting the card copies only what is missing.
    A batch that was mid-copy when the app was killed becomes `error` on the next launch.
15. **Rows from earlier sessions are hidden once `done` or `error`.** The spec names only
    `done`; an old copy error has nothing left to act on.
16. **Processing starts when the copy is verified**, not during it, so nothing competes
    with the card for disk and CPU while the photographer is waiting.

## Engine

17. **Hashing runs beside the write** (one helper thread), so verification costs one
    extra read of the copy rather than two hashing passes in series. `tests/bench_ingest.py`
    measured +12 % against a plain copy with fsync on the dev machine (NFR-02).
18. **A pixel budget (120 MP decoded at once) caps memory** (NFR-08): four 24 MP photos
    run together, 45 MP photos two at a time. Not yet measured with real 45 MP files.
19. **Watermark numbers.** `watermark_scale` is the watermark's width as a fraction of the
    photo's short edge (default 0.18); `watermark_margin` is a fraction of the short edge
    (default 0.03); opacity default 0.7; position default bottom right. v0.1's values
    were not available. **(confirmed)**
20. **Retries.** Temporary upload errors follow FR-33. A failed processing step is not
    retried automatically (a damaged file stays damaged); Retry runs it again.
    Unexpected exceptions in an uploader count as temporary so no photo is ever dropped.
21. **Two upload workers.** The spec gives no number.
22. **Flickr tokens.** `flickrapi` is created with `store_token=False`; otherwise it writes
    tokens to its own plain SQLite file, against NFR-06.
23. **The app's own API keys** are baked in at packaging time from environment variables
    (`packaging/write_builtin_keys.py`). Without them the build works and users enter
    their own key in Settings. The owner will obtain keys; until the four `WEFLOW_*`
    repository secrets are set, builds ship without them (see `packaging/README.md`).
24. **Fake volumes "eject" successfully without touching anything.** Linux is supported
    for development only (`/media/$USER`, `/run/media/$USER`, `umount`).

## Interface

25. **Derived colour tokens.** The spec's tokens alone cannot meet WCAG AA everywhere:
    - `good-strong`, `warn-strong`, `bad-strong`: the status colour nudged toward `text`
      until it reads at 4.5:1 on its tint. Used for status text and icons. The raw status
      colours are kept for edges and fills.
    - `on-accent`: white in light mode; the dark `bg` in dark mode, because white on
      `#60A5FA` is 2.5:1.
    - Sublines on tinted panels use `text`, not `text-2`; buttons on tinted panels are
      outlined in `text` rather than accent-coloured.
26. **The progress bar is neutral** (`text-2` on `border`). Amber on the green "Safe"
    panel would muddy the one signal that matters from 2 m.
27. **Type sizes are pixels** (28, 17, 15, 13, 12, 11), so both platforms match.
28. **Three always-visible controls** (CLAUDE.md): Pause, Settings and Switch. The
    "Earlier cards" list and the Activity handle appear only once there is something in
    them. "Import folder…" (FR-18) lives in the Switch popover. **(confirmed)**
29. **Release and Retry act on the event of the batch shown**, which is the active event
    unless the user switched events mid-job.
30. **Deleting an event** takes two clicks on the same button ("Delete" → "Really
    delete?") instead of a modal. Events with work still running cannot be deleted, and
    the library folder cannot be changed until work has finished.
31. **The sample photo for previews is one setting**, shared by all events.
32. **"Event done" notifications fire per card**, when that card's photos are all finished.
33. **Closing the window quits the app when nothing is pending**; otherwise it hides to
    the tray. Only one copy of WeFlow runs at a time.

## Releases

34. **Installers are published to a GitHub Release when a version tag is pushed.** SPEC
    M6 only asks for CI artifacts, but those sit inside a workflow run and expire. Pushing
    `v<version>` runs the usual tests and builds, then the **Release** job attaches the
    `.dmg` and `.exe` files to a release of that name. The tag must match
    `WeFlow.__version__`, or the job fails before publishing. Pushes to `main` still
    produce artifacts only. If the optional Intel build fails, the release goes out
    without the x86_64 `.dmg`. **(confirmed)**

## Not verified yet

These need hardware, accounts or other operating systems (see `docs/manual-test.md`):
real cards on macOS and Windows; eject; real SmugMug and Flickr uploads and connect
flows; tray icon and notifications; installers, signing and notarisation; start at
login; NFR-03 and NFR-08 with real 24 MP and 45 MP files.
