# WeFlow v1 — Build Specification

> Audience: Claude Code. Build WeFlow v1 from this spec, milestone by milestone (§10).
> Start from the v0.1 prototype in this repo (`WeFlow/`). Keep and harden its engine; replace its Tkinter UI.
> Where this spec is silent, choose the simplest option that satisfies it and note the choice in `DECISIONS.md`.

---

## 1. Product summary

WeFlow is a desktop app for event photographers. The photographer inserts a memory card, and WeFlow does the rest:

1. Copies every JPEG and verifies each copy with a checksum.
2. Signals **"Safe to remove"**, so the card can go back in the camera.
3. Applies auto white balance, auto tone and an optional PNG watermark in the background.
4. Uploads the results to SmugMug or Flickr.

All decisions are made once per event, before the shoot. **Inserting a card asks nothing.**

- Users: one photographer, on a macOS or Windows laptop, at a venue with unreliable Wi-Fi.
- Inputs: in-camera JPEGs, already culled in-camera. RAW files are ignored.

---

## 2. Tech stack (decided — do not substitute)

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | Type hints everywhere; `from __future__ import annotations` |
| GUI | **PySide6 (Qt 6, Qt Widgets)** + a custom QSS stylesheet generated from design tokens (§7.5) | No Tkinter. No QML. |
| Imaging | Pillow + NumPy | Keep v0.1 LUT approach (`imaging.py`) |
| HTTP / OAuth | `requests`, `requests-oauthlib` (SmugMug), `flickrapi` (Flickr) | |
| Secrets | `keyring` | macOS Keychain / Windows Credential Manager |
| Notifications | `QSystemTrayIcon.showMessage` | Works on both OSes; no extra deps |
| Packaging | PyInstaller (one-folder) → `.app` in a `.dmg` on macOS, `.exe` installer via Inno Setup on Windows | Build scripts in `packaging/` |
| Tests | `pytest`, `pytest-qt` | Must run headless (`QT_QPA_PLATFORM=offscreen`) |
| Lint/format | `ruff` (lint + format), `mypy --strict` on `WeFlow/core` | |
| CI | GitHub Actions matrix: `macos-latest`, `windows-latest`, `ubuntu-latest` (tests only on Ubuntu) | |

Dependencies are pinned in `pyproject.toml`. No other runtime dependencies without recording why in `DECISIONS.md`.

---

## 3. Repository layout (target)

```
WeFlow/
  core/                 # no Qt imports allowed in core/
    config.py           # Settings, Profile (event), Store  (from v0.1)
    cards.py            # volume detection + eject           (from v0.1)
    ingest.py           # verified copy, rename, dedup       (from v0.1)
    imaging.py          # auto WB, auto tone, watermark      (from v0.1)
    uploaders.py        # SmugMug, Flickr                    (from v0.1)
    pipeline.py         # workers, state, retries            (from v0.1)
    secrets.py          # keyring wrapper                    (new)
    events.py           # typed events emitted by pipeline   (new)
  ui/
    app.py              # QApplication, tray icon, theme switching
    theme.py            # design tokens -> QSS
    main_window.py      # the working face
    widgets/            # EventCard, StatusPanel, StepTracker, CardRow, Chip, ActivityDrawer
    event_editor.py     # event setup sheet
    settings_dialog.py
    first_run.py
    bridge.py           # Qt signals wrapping core.events (thread-safe)
  assets/icons/         # line icons (SVG), app icon
tests/
  fixtures/             # generated sample cards
  test_*.py
packaging/
pyproject.toml
DECISIONS.md
```

Rule: `WeFlow/core` must never import Qt. The UI talks to the core only through `ui/bridge.py`.

---

## 4. Domain model

### 4.1 Event (a.k.a. Profile)
Fields carry over from v0.1 `Profile`, with these changes:

| Field | Type | Default | Notes |
|---|---|---|---|
| `id` | str (uuid4) | new | **New.** Stable key; folder name still derives from `name` slug at creation and never changes after |
| `name` | str | `"YYYY-MM-DD New Event"` | Unique |
| `folder` | str | slug of name at creation | **New.** Frozen at creation |
| `destination` | `"none" \| "smugmug" \| "flickr"` | `"none"` | |
| `album` | str | `""` | SmugMug gallery URL / AlbumUri / key, or Flickr album title |
| `album_name` | str | `""` | *(1.4.0)* The gallery's name when picked from the list (FR-39); display only, `""` when typed or pasted |
| `flickr_privacy` | enum | `"private"` | private, friends, family, friends+family, public |
| `keywords` | str | `""` | comma-separated |
| `auto_wb` / `wb_strength` | bool / float 0–1 | true / 0.6 | |
| `auto_tone` | bool | true | |
| `watermark` + `watermark_path/position/scale/opacity/margin` | | as v0.1 | |
| `max_long_edge` | int | 0 | 0 = full size |
| `jpeg_quality` | int 0–100 | 92 | Lightroom's range (1.2.0; was 60–100) |
| `hold_uploads` | bool | false | |
| `keep_processed` | bool | false | *(1.5.0)* Keep processed copies after upload (FR-40) |
| `file_naming` | `"import" \| "custom_seq" \| "custom" \| "date_filename" \| "filename" \| "filename_seq"` | `"import"` | FR-26 (1.2.0) |
| `custom_name` | str | `""` | Empty = event name |
| `start_number` | int ≥ 0 | 1 | First sequence number |
| `limit_file_size` / `file_size_kb` | bool / int 10–100000 | false / 500 | FR-27; K = 1000 bytes |

### 4.2 Batch
One card insertion (or folder import). Fields as v0.1 `Batch`, plus a `state` enum:
`copying → safe → processing → uploading → held → done` and `error` (copy failed) / `attention` (some photos failed).
In Paced mode (FR-38) a batch goes `copying → ready` and waits there; `ready` is reached only after every file is verified.

### 4.3 Photo record (persisted per event in `.WeFlow/state.json`)
`name, sha256, captured, batch_id, status (copied|processed|uploaded|failed), failed_stage, remote_id, held, attempts, error`.
From 1.2.0 also `original` (card file name, no extension), `seq` (1-based import order in the event) and `output` (processed file name; empty = `name`).

### 4.4 Persistence
- Settings and events: `<app-data>/settings.json`, `<app-data>/events.json` (atomic writes).
- Export presets (FR-28): `<app-data>/presets.json` (atomic writes).
- Secrets (API keys and secrets, OAuth tokens): **keyring only**, under the service name `WeFlow`.
- On first launch, migrate any v0.1 secrets found in `settings.json` into keyring, then blank them out.
- Library on disk:
  ```
  <Library>/<event.folder>/originals/
  <Library>/<event.folder>/processed/           # local-only events, kept copies (FR-40)
  <Library>/<event.folder>/.WeFlow/state.json
  <Library>/<event.folder>/.WeFlow/work/        # 1.5.0: processed copies waiting to upload
  ```

---

## 5. Functional requirements

Each requirement has an ID. Reference these IDs in commit messages and test names (for example, `test_fr12_checksum_mismatch_not_safe`).

### 5.1 Events
- **FR-01** Create, edit, duplicate and delete events. Deleting an event never deletes photos.
- **FR-02** Exactly one event is active at a time. Switching takes ≤ 2 clicks from the main window.
- **FR-03** A new event copies the active event's settings and pre-fills its name as `YYYY-MM-DD New Event` (today's date).
- **FR-04** The event editor shows a live before/after preview of a user-chosen sample JPEG. The preview updates within 500 ms of any setting change (debounced, rendered off the UI thread).

### 5.2 Cards and copying
- **FR-10** Detect a removable volume containing `DCIM/` within 3 s of it mounting.
  - macOS: poll `/Volumes`.
  - Windows: `GetLogicalDrives` + `DRIVE_REMOVABLE`, with `SetErrorMode` set to suppress "no disk" dialogs.
- **FR-11** On detection, show a non-modal notice: "Card <label> → <event name>", with Cancel. Auto-start after 5 s.
  - If no event is active, show the event switcher instead and start once one is chosen.
- **FR-12** Copy each JPEG (`.jpg` / `.jpeg`, any case; skip dotfiles and `._*`) to a temp file while hashing it with SHA-256, then `fsync`.
  - Re-hash the copy. On a mismatch, delete the temp file and mark the batch `error`.
- **FR-13** Rename each file to `<folder>_<YYYYMMDD-HHMMSS>_<original stem>.jpg`, using EXIF DateTimeOriginal (falling back to the file's modified time). Add `_2`, `_3`… on collision.
- **FR-14** Skip any photo whose checksum is already recorded for the event, and count it as "already imported".
- **FR-15** Move the batch to `safe` only after every file is copied and verified. A batch in `error` must never show as safe.
- **FR-16** The Eject action calls `diskutil eject` on macOS and the Shell.Application Eject verb on Windows.
  - Report success or failure in the UI.
- **FR-17** **Never** open card files for writing. Never rename, move or delete anything on a card. Enforce with a test that runs ingest against a read-only fixture.
- **FR-18** "Import folder…" runs the same pipeline on any folder.

### 5.3 Processing
- **FR-20** Auto white balance: gray-world on mid-tones with saturated pixels excluded (v0.1 `wb_gains`).
  - Gains are blended toward 1.0 by the strength setting and clamped to [0.75, 1.35].
- **FR-21** Auto tone: luminance percentiles 0.3 / 99.7 set the black and white points (black ≤ 0.12, white ≥ 0.80).
  - A gamma correction moves the median 60% of the way toward 0.46, clamped to [0.72, 1.35].
- **FR-22** Watermark: the PNG is scaled to `scale × short edge` and placed at one of 5 positions with a margin, at the set opacity.
  - It must look identical on portrait and landscape photos.
- **FR-23** Apply EXIF orientation before the watermark, then write orientation = 1.
  - Preserve the rest of the EXIF data, and the ICC profile when the source is RGB.
- **FR-24** Optional resize (Lanczos) and per-event JPEG quality.
- **FR-25** Writes are atomic: write to `.tmp-<name>`, then rename. Run `min(4, cpu_count // 2)` processing workers.
- **FR-26** *(1.2.0)* Processed copies are named by the event's File naming rule, Lightroom-style: Import name (FR-13, the default), Custom name - Sequence, Custom name, Date - Filename, Filename, Filename - Sequence.
  - Sequence numbers have 4 digits, count from the start number, and follow import order within the event. "Filename" is the card's file name; "Date" is the capture date `YYYYMMDD`.
  - Names are made safe on both OSes. A taken name gets `-2`, `-3`…
  - The name is fixed the first time a photo is processed and stored on its record. Changing the rule later never renames a photo that has a name, so uploads and retries always find the file. Originals are never renamed by this rule.
- **FR-27** *(1.2.0)* JPEG quality is 0–100. "Limit file size to N K" ignores quality and writes the highest quality that fits; if even quality 0 is too big, that smallest version is written.
- **FR-28** *(1.2.0)* Export presets hold FR-26/27 settings (naming, custom text, start number, quality, size limit) under a name. Any event, new or existing, can pick one; saving with an existing name replaces it. Presets never hold destination, look or watermark settings.

### 5.4 Uploading
- **FR-30** SmugMug: OAuth 1.0a out-of-band flow, with `Access=Full` and `Permissions=Add`.
  - Upload by POSTing to `https://upload.smugmug.com/` with these headers: `X-Smug-AlbumUri`, `X-Smug-Version: v2`, `X-Smug-ResponseType: JSON`, `X-Smug-FileName`, `X-Smug-Title`, and `X-Smug-Keywords` when set.
  - Resolve gallery URLs containing `/n-<id>` through `GET /api/v2/node/<id>` → `Uris.Album.Uri`.
- **FR-31** Flickr: authorize with `flickrapi`, `perms=write`.
  - Upload with privacy flags and quoted tags.
  - Add each photo to the album with the event's title, creating the album (`photosets.create`) the first time.
- **FR-32** Upload and add-to-album are separate steps. A photo with a `remote_id` is **never uploaded again**; only the album step is retried.
- **FR-33** Temporary errors (network, 5xx, 429) retry after 10, 20, 40, 60, 120, then every 300 s, up to 12 attempts.
  - Permanent errors (401/403, bad album, auth) fail at once with a plain-language message.
- **FR-34** Hold uploads: processed photos wait until "Release" is pressed. Release applies to all held photos in the active event.
- **FR-35** Retry failed: resets attempts and requeues each photo at the stage where it failed.
- **FR-36** On launch, resume every unfinished photo in every event.
- **FR-37** Credentials are entered and connected in Settings. Store them through `core/secrets.py` only.
- **FR-39** *(1.4.0)* In the event editor, the album field is a searchable dropdown of the signed-in user's galleries (SmugMug) or albums (Flickr), with a Reload button. Typing or pasting still works.
  - SmugMug: `GET /api/v2!authuser` → `Uris.UserAlbums.Uri`, paged by `start`/`count`, newest first. Picking a gallery stores its AlbumUri in `album` and its name in `album_name`.
  - Flickr: `photosets.getList`, newest first by `date_create`; picking stores the title in `album`, as typing does.
  - The list loads off the UI thread. If it can't load (not signed in, offline), say why under the field and keep what is saved.
  - Creating a SmugMug gallery from WeFlow is out of scope for now. *(Superseded by FR-41 in 1.6.0.)*
- **FR-41** *(1.6.0)* Make a new gallery or album from WeFlow, and pick from the list in the first-run wizard too.
  - A **+** button next to Reload opens a name box (suggesting the event name) with Create and Cancel.
  - SmugMug: `GET /api/v2!authuser` → `Uris.Node.Uri`, then `POST <node>!children` with `Type: Album`, the name, a `UrlName` built from it and `Privacy: Unlisted`. A 409 (address taken) retries with `-2` … `-5`. The new gallery is picked and shown at the top of the list.
  - Flickr can't hold an empty album, so the title is picked and Flickr makes the album with the first upload, as a typed title always did. No API call.
  - The first-run "Create your first event" step uses the same picker as the event editor.
  - *(1.6.1)* The dropdown shows the 20 newest, then a **More galleries…** row that adds 20 more each time it is picked. Typing a name or pasting a link still finds any gallery. SmugMug pages ask only for the fields the list needs (`_filter`), with a 45 s read timeout.
- **FR-40** *(1.5.0)* Originals are the lasting copy. For events that upload, processed copies are written to the hidden `.WeFlow/work/` folder.
  - Once a photo is uploaded **and** added to its album, its processed copy is deleted, or moved into `processed/` when the event's "Keep processed copies after upload" is on (off by default).
  - Held, waiting and failed photos keep their processed copy until they are up. If it goes missing before upload, it is made again from the original.
  - Local-only events (destination none) still write straight to `processed/`.
  - Files already in `processed/` from earlier versions are never deleted.

### 5.5 Pipeline mode *(1.3.0)*
- **FR-38** An app-wide **Auto / Paced** switch (`Settings.paced`, default Auto).
  - Auto: every step runs on its own, as in FR-12–FR-36.
  - Paced: copied photos wait (`held` on a `copied` photo) until **Process** is pressed; processed photos wait until **Upload** is pressed (as FR-34).
  - Switching to Auto sends waiting photos on, except photos held by the event's own `hold_uploads`.

---

## 6. Non-functional requirements

- **NFR-01** Targets: macOS 13+ (arm64 and x86_64) and Windows 10/11 x64, with the same features on both.
- **NFR-02** Verification adds ≤ 30% to raw copy time. Measure it in `tests/bench_ingest.py`; the benchmark is not part of CI.
- **NFR-03** Processing a 24 MP JPEG takes ≤ 1 s on a 2022 laptop with one worker.
- **NFR-04** The UI thread never does I/O or image work. The UI stays responsive (no frame > 100 ms) during a 1000-photo import.
- **NFR-05** State is saved after every file transition. Killing the app at any point loses no photos and never shows a false "safe".
- **NFR-06** No secrets in plain files or logs.
- **NFR-07** Ships as a double-click installer; no Python needed.
  - Code signing and notarization steps are scripted, and read their credentials from environment variables.
- **NFR-08** Memory use stays ≤ 1 GB while processing 45 MP files with 4 workers.
- **NFR-09** Every error shown to the user says what happened and what to do, in ≤ 2 short sentences. No stack traces in the UI.
- **NFR-10** Logs rotate in `<app-data>/logs/` (3 × 500 KB since 1.6.1, was 5 × 1 MB). Every SmugMug and Flickr call is logged with its path, status, time taken or error type, and never headers, tokens or bodies. Settings has **Copy log**, which copies the newest 60,000 characters to the clipboard.

---

## 7. UI and visual design

**Goal: a sleek, modern, minimal interface. The working face must be as functionally simple as possible.**

Acceptance test for the design: from about 2 m away, someone can tell (a) which event is active and (b) whether the card can come out.

### 7.1 Principles (apply to every screen)
1. **One working screen.** Setup lives in separate sheets and dialogs.
2. **Show state, not controls.** Actions appear only when they apply.
3. **Calm and flat.** Use whitespace instead of borders: no gradients, no drop shadows heavier than token `shadow-1`, one accent colour.
4. **Colour means status only:** green = safe or done, amber = working or held, red = needs attention. Always pair a colour with an icon or a word.
5. **No modal dialogs while working.** The only exception is choosing an event when none is active.

### 7.2 Main window: the working face

Default size 560×720; minimum 480×600. One column, 24 px outer padding. From top to bottom:

| # | Element | Spec |
|---|---|---|
| 1 | **Top bar** (48 px) | App name left (`type-heading`). Right: an "Auto ◯ Paced" switch (FR-38), Pause/Resume toggle (icon + label) and a Settings gear icon button. **No other always-visible controls (UI-05).** |
| 2 | **Event card** | `surface-2` background, radius 12, padding 16. Line 1: event name (`type-title`). Line 2 (`type-secondary`): destination · album · "Watermark" · "Held" (only those that apply). Right side: "Switch ▾" text button that opens a popover list of events + "New event…" + "Edit this event…". (UI-01) |
| 3 | **Status panel** — takes the remaining vertical space above the list | States below (§7.3). Radius 16, padding 24. (UI-02, UI-03) |
| 4 | **Earlier cards** list | Header `type-label` "Earlier cards". Rows 44 px: `Card label · N photos` left; status chip right; contextual action (Retry / Release / Open folder) as a text button left of the chip, **only when applicable** (UI-04, UI-06). Newest first. Rows from previous app sessions are hidden after they reach `done`. Max 5 visible, then the list scrolls. |
| 5 | **Activity drawer** handle | Bottom, 32 px, "Activity ›" in `type-secondary`. Opens a 320 px drawer with the log from the right edge (UI-07). Errors also appear as row state; the log is never the only place an error shows. |

### 7.3 Status panel states

| State | Background | Headline (`type-display`, 28 pt) | Subline | Extras |
|---|---|---|---|---|
| Idle | `surface-2` | "Insert a card" | "Photos go to <event>" | Small card icon |
| Paused | `surface-2` | "Paused" | "Card watching is off" | Resume button |
| Copying | `warn-tint` | "Copying — keep card in" | "<n> of <total> photos" | Tracker on step 1, progress bar |
| **Safe** | `good-tint` + 2 px `good` border | "**Safe to remove**" | "<label> · <n> photos copied and verified" | **Eject** button (outline, `good`); tracker continues (Process/Upload) |
| Working (no card in) | `surface-2` | "Processing" / "Uploading" | "<n> of <total>" | Tracker + progress bar |
| Ready (Paced) | `good-tint` + 2 px `good` border | "Safe to remove" (card in) / "Copied and verified" | "<n> photos copied. Process and auto-tune them?" | **Process** (primary), Eject while the card is in; tracker on Process |
| Held | `warn-tint` | "Ready to upload" | "<n> photos tuned. Upload them to <destination>?" | **Upload** (primary) and "Open folder" |
| Done | `good-tint` | "All done" | "<n> photos uploaded to <destination>" | Fades to Idle after 10 s |
| Attention | `bad-tint` + 2 px `bad` border | "Needs attention" | One-line reason | **Retry** (primary), "Details" opens the drawer |
| Copy error | `bad-tint` | "Don't remove yet — copy failed" | Reason + "Re-insert the card to try again" | Retry |

The panel shows the most recent batch. The **4-step tracker** (Copy · Process · Upload · Done) is a row of 18 px circles joined by a 1 px line. Each step is drawn as done (filled `good`), current (2 px `good` ring) or pending (1 px `border` ring). Labels sit under the circles in `type-label`. The progress bar under the tracker is 4 px high and fully rounded.

**State transitions crossfade over 200 ms.** No spinners where a progress bar can be shown.

### 7.4 Secondary screens

- **Event editor** — a sheet (macOS) or dialog (Windows), 640 px wide.
  - Left column holds 4 groups, each with a heading:
    - **Destination:** where, album (*1.8.1:* a Choose Gallery… button that opens a list of the user's galleries, FR-39, with Create a gallery… at the top, FR-41), privacy, hold toggle.
    - **Look:** auto WB with strength slider, auto tone.
    - **Watermark:** toggle, file picker with thumbnail, position as a 3×3 grid picker (corners + center only enabled), size slider, opacity slider.
    - **Export** *(1.2.0)*: preset picker (Save as preset… / Delete preset), file naming, custom text and start number (only when the rule uses them), an example file name, quality slider, Limit file size toggle and size.
  - Rarely changed fields (resize, margin) sit in a collapsed "More" section.
  - Right column: before/after preview of the sample photo (FR-04), with a draggable split line. *(1.8.1)* Pinch or Ctrl + scroll zooms; the line stays pinned to the photo.
  - Validate inline (red helper text under the field). The Save button is disabled until the form is valid.
- **Settings:**
  - Library folder.
  - SmugMug and Flickr rows, each showing Connected/Not connected, a Connect/Reconnect button and a "Disconnect" link.
  - API key fields stay hidden until the user clicks "Use my own API key…".
  - Card-detected sound on/off.
  - Start at login on/off (Should).
- **First run:** 3 steps in one window — library folder → connect a service (skippable) → create the first event. Then the main window opens.
- **Tray / menu-bar icon** with 4 states (idle, working, safe, attention). Its menu: Show WeFlow, Pause watching, Quit.
  - Closing the window hides it to the tray while work is pending.
- **Notifications** (`showMessage`) for: card detected, safe to remove, event done, attention needed. Throttle to at most one per 5 s per type.

### 7.5 Design tokens

Generate the QSS in `ui/theme.py` from these values.

Follow the OS theme live: on macOS listen to `QStyleHints.colorScheme`, and do the same on Windows.

| Token | Light | Dark |
|---|---|---|
| `bg` | #F7F7F8 | #111113 |
| `surface` | #FFFFFF | #1A1A1D |
| `surface-2` | #F0F0F2 | #232327 |
| `border` | #E4E4E7 | #2E2E33 |
| `text` | #111114 | #F4F4F5 |
| `text-2` | #6B6B73 | #A1A1AA |
| `accent` | #2563EB | #60A5FA |
| `good` | #16A34A | #22C55E |
| `warn` | #D97706 | #F59E0B |
| `bad` | #DC2626 | #F87171 |
| `good-tint` / `warn-tint` / `bad-tint` | status colour at 12% over `surface` | status colour at 16% over `surface` |
| `shadow-1` | 0 1px 2px rgba(0,0,0,.06) | none |

**Typography:**
- Typeface: the system font ("SF Pro Text" / "Segoe UI Variable", falling back to the platform default).
- Sizes: `type-display` 28/600, `type-title` 17/600, `type-heading` 15/600, `type-body` 13/400, `type-secondary` 12/400 in `text-2`, `type-label` 11/500 in `text-2`.

**Spacing and shape:**
- Spacing: 4, 8, 12, 16, 24, 32 only.
- Radii: 8 for controls, 12 for cards, 16 for the status panel.
- Controls are 32 px tall; primary buttons use an `accent` fill with white text.

**Icons:** one line-icon set (for example, Lucide SVGs, bundled) at a 1.5 px stroke, 16 or 20 px.

**Accessibility:**
- All text and status colour pairs meet WCAG AA in both themes. Add a test in `tests/test_theme_contrast.py`.
- Full keyboard navigation with visible focus rings (2 px `accent`).

---

## 8. Core ↔ UI contract

`core/events.py` defines frozen dataclasses that the pipeline emits through a callback:

```python
BatchUpdated(batch: Batch)          # any counter/state change (coalesce to ≤ 10/s per batch)
CardDetected(path: str, label: str)
CardRemoved(path: str)
Notice(level: Literal["info","safe","error"], message: str, batch_id: int | None)
```

`ui/bridge.py` receives these on worker threads and re-emits them as Qt signals (queued connections). The UI never polls the pipeline.

Pipeline public API (keep the v0.1 names where they exist):

- `submit(source: Path, event: Profile, label: str, is_card: bool) -> Batch`
- `release_held(event_id: str | None = None) -> int`
- `retry_failed(event_id: str | None = None) -> int`
- `resume() -> None`
- `snapshot() -> list[Batch]`
- `stop(timeout: float = 5) -> None`

---

## 9. Testing requirements

- **Fake cards:** `tests/fixtures/make_card.py` generates a `DCIM/100TEST/` folder of N synthetic JPEGs. It must cover: an orientation-6 photo, a duplicate photo, an AppleDouble `._` file, and an uppercase `.JPG` extension.
- **Fake volumes:** when the environment variable `WeFlow_FAKE_VOLUMES=<dir>` is set, `cards.find_cards()` treats each subfolder of `<dir>` as a mounted volume. Tests use this to simulate insert and remove.
- **Fake uploaders:** provide `FakeUploader(fail_times=n, permanent=False)` for retry, hold and idempotency tests. **CI never calls the real SmugMug or Flickr APIs.**
- **Required tests:** one or more per FR or NFR that can be automated.
  - At minimum: FR-12/14/15/17 (ingest), FR-20–23 (imaging; assert a channel-mean shift on a casted image, orientation = 1, EXIF date kept, watermark bounding box in the same relative corner for portrait and landscape), FR-32/33/34/35/36 (pipeline with the fake uploader, including killing and restarting the pipeline mid-run), and UI smoke tests with `pytest-qt` for every status-panel state in §7.3.
- **Screenshot tests:** render the main window in each §7.3 state, in light and dark, to `tests/artifacts/*.png` at 560×720 for human review. Do not compare pixels.
- **Manual test script:** `docs/manual-test.md` lists the steps that need real hardware and accounts: a real card on each OS, real SmugMug/Flickr uploads to throwaway albums, eject, and notifications.

---

## 10. Milestones (build in order; each ends green)

| # | Milestone | Done when |
|---|---|---|
| M1 | **Restructure**: move v0.1 into `WeFlow/core`, add `pyproject.toml`, ruff, mypy, pytest, CI | CI green on 3 OSes; v0.1 behaviour unchanged |
| M2 | **Core hardening**: event ids/frozen folders, keyring secrets + migration, typed events, fake volumes, coalesced updates, `stop()` | All §9 core tests pass; `mypy --strict` clean on core |
| M3 | **Theme + widgets**: tokens → QSS, light/dark switching, EventCard, StatusPanel (all states), StepTracker, CardRow, Chip, ActivityDrawer | Screenshot artifacts for every state, both themes; contrast test passes |
| M4 | **Main window** wired to the pipeline via the bridge; card notice with 5 s cancel; eject; tray icon + notifications | pytest-qt flow: fake card inserted → Safe state → Done state |
| M5 | **Event editor + live preview**, Settings with Connect flows, First-run | pytest-qt: create event, switch, edit, validate; preview renders off-thread |
| M6 | **Packaging**: PyInstaller specs, `.dmg` and Inno Setup scripts, signing hooks, app icon | Installers build in CI as artifacts; app launches from installer on both OSes (manual check) |
| M7 | **Polish**: motion, empty states, error copy review, `docs/manual-test.md`, README for end users | Every §7.3 state reviewed in screenshots; manual test doc complete |

After each milestone:
1. Update `CHANGELOG.md`.
2. List any spec deviations in `DECISIONS.md`.
3. Stop, and summarise what's ready for review.

---

## 11. Out of scope (v1)

- RAW files and RAW+JPEG pairs.
- Culling or per-photo editing.
- Destinations other than SmugMug and Flickr.
- Wireless or tethered transfer.
- Creating SmugMug galleries from the app.
- Lightroom catalog sync.
- Uploading to two destinations at once.
