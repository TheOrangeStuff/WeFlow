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
8. **macOS builds are per architecture**, not universal, because NumPy and Pillow wheels
   are not universal2. Only arm64 is built now (decision 40).

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
23. **The app has no API keys of its own** (replaces the earlier plan to bake keys in at
    packaging time; see decision 37). Each user enters their own key.
24. **Fake volumes "eject" successfully without touching anything.** *(1.14.0)* WeFlow is
    macOS and Windows only. On other systems it finds no real cards (use
    `WeFlow_FAKE_VOLUMES`) and keeps its data in `~/.WeFlow`; the Linux card and
    `umount` code was removed.

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
    produce artifacts only. **(confirmed)**

35. **Installers are also published to a public repo, `TheOrangeStuff/weflow-releases`.**
    This repo is private, so an installed app cannot read its releases without a token,
    and a token must not ship inside the app. The Release job publishes the same files
    to the public repo using the `RELEASES_REPO_TOKEN` secret, and fails early if the
    secret is missing. The private release (decision 34) is kept. Public release notes
    come from the matching `CHANGELOG.md` section. **(owner's choice)**
36. **Update notice (not in SPEC).** At start, and every 24 h while open, the app asks
    the public repo for its latest release (`core/updates.py`) with no credentials and
    sends nothing about the user. A newer version is offered in the notice bar with
    **Download** (opens the release page in the browser) and **Skip** (remembers that
    version). The app never downloads or installs by itself: that needs signed builds
    and an updater framework. *(1.9.0: on Windows it now does, see the 1.9.0 section and
    FR-44. macOS still opens the page.)*
    - The offer shows only while the status is idle, and waits behind a card countdown
      or a short message, so it never competes with "safe to remove".
    - A skipped version stays listed as a link beside the version number in Settings.
    - A failed check (offline, rate limit) is silent. "Check for new versions" in
      Settings turns it off. The `--smoke-test` launch does not check.
    - Tests replace the fetch, so they never call GitHub.

37. **No SmugMug or Flickr credentials on GitHub or in a build.** The built-in key path
    (`packaging/write_builtin_keys.py`, the four `WEFLOW_*` CI secrets, the
    `_builtin_keys` module) is removed. Users enter their own API key and secret, so the
    key fields show whenever none is stored, with a link to the service's API-key page.
    No release ever contained a built-in key, so existing users are unaffected.
    The remaining CI secrets are the optional signing certificates and
    `RELEASES_REPO_TOKEN`; a test pins that list. **(owner's rule)**
38. **"What's new" (not in SPEC).** The first start of a newer version shows a small
    dialog with the bullets of that version's `CHANGELOG.md` section, once
    (`seen_version` in settings). A new install and a downgrade show nothing.
    `CHANGELOG.md` is bundled into the app as the single source, shared with the public
    release notes. The Release job and a test fail if the section is missing.
39. **An update never signs the user out.** Sign-in lives in the keychain and settings
    in app-data; neither installer touches them. Keychain entry names are frozen by a
    test, and another test starts from 1.0.0's files to prove nothing is asked again.
    A release may clear a stored sign-in only when the changed feature cannot work with
    it, and its changelog section must say so. On macOS, unsigned builds trigger one
    keychain password prompt after each update, because the signature changed; a
    Developer ID signature avoids it. **(owner's rule)**

40. **No Intel Mac build (deviates from NFR-01).** From 1.1.0, macOS releases are
    Apple silicon (arm64) only; Windows x64 is unchanged. The `macos-15-intel` CI job is
    removed and `build_macos.sh` refuses to run on anything but arm64. The 1.0.0 Intel
    `.dmg` stays on its private release, but no later version is built for Intel, and
    1.0.0 has no update check, so no Intel Mac is ever offered an update it cannot run.
    **(owner's choice)**

## 1.2.0 — export settings and presets

41. **New requirements FR-26 (file naming), FR-27 (quality, file-size limit) and FR-28
    (export presets)** were added to SPEC.md at the owner's request. Resize options beyond
    the existing long edge were offered and declined. **(owner's choice)**
42. **Naming applies to processed copies only.** Originals keep their FR-13 import name,
    so they still trace back to the card. **(owner's choice)**
43. **A Lightroom-style list of naming rules**, not a free-form pattern. **(owner's choice)**
44. **Presets hold export settings only** (naming, custom text, start number, quality,
    size limit), stored in `<app-data>/presets.json`. Events do not remember which preset
    they came from; the editor shows the preset whose settings match, else "No preset".
    **(owner's choice)**
45. **A processed name is fixed when first processed** and stored as `PhotoRecord.output`,
    because the upload, retry and resume steps all look the file up by it. Photos from
    1.1.0 have no `output`, `seq` or `original`. They keep working: an empty `output`
    means the import name, `seq` is given on first processing, and the card name is
    recovered from the import name.
46. **Sequence = import order within the event** (card order, which follows the camera's
    numbering), not processing order, which varies with parallel workers.
47. **Empty custom text uses the event name**, so one preset suits every event.
48. **Quality range widened from 60–100 to 0–100** to match Lightroom.
49. **"K" means 1000 bytes**, so a file is under the limit whichever way a site counts.
    If no quality fits, the quality-0 version is written and a warning logged; failing the
    photo would hold up the upload over a soft limit.
50. **The size limit costs up to 7 extra encodes per photo** (a binary search on
    quality), so NFR-03's 1 s per 24 MP photo may not hold while it is on. Not measured.
51. **Number fields get text margins in code.** Fusion ignores `QSpinBox` padding from
    the stylesheet, so the text sat against the border. This also fixes the existing
    resize field.
52. **Auto / Paced mode (1.3.0, FR-38)** is one app-wide setting (`Settings.paced`),
    not per event, because the owner asked for a switch at the top of the window. It
    is a 4th always-visible control, so UI-05 now allows 4. **(owner's choice)**
53. **Paced reuses `PhotoRecord.held`**: it now means "waiting for the go-ahead to the
    next stage". A copied photo with `held` waits to be processed; a processed one waits
    to be uploaded, as before. No new photo field, and older state files load unchanged.
54. **New batch state `ready`** (copied and verified, waiting to process) with a
    `Batch.ready` count. It is only reached after `verified`, so FR-15 holds. With the
    card in, the panel still says "Safe to remove" and offers **Process** and **Eject**.
55. **Switching to Auto releases waiting photos**, except processed photos of events
    with "Hold uploads" on: that hold is the event's own choice.
56. **The Held panel reads "Ready to upload"** with an **Upload** button (was "Ready for
    review" / Release), so Paced and Hold uploads ask the same plain question.
57. **Gallery picker (1.4.0, FR-39) stores the AlbumUri** in the existing `album` field,
    which the uploader already accepts, so uploads and older events are unchanged. The
    new `album_name` field is display only; older versions ignore it. **(owner's choice)**
58. **One editable dropdown, not a dropdown plus a separate link box.** Its text is the
    album field: a listed name maps to that gallery, anything else is used as typed.
    A pasted `/n-` link or AlbumUri is shown by its gallery name once the list loads.
59. **Same-named galleries show their folder** ("Gala — Events") so they can be told
    apart. The list is newest first and stops at 2,000 galleries.
60. **The first-run wizard keeps its plain album box.** The user may not be signed in yet
    there; the event editor offers the list afterwards.
61. **Creating a SmugMug gallery from WeFlow is deferred** to a later version, at the
    owner's request. **(owner's choice)**

## 1.5.0 — originals are the lasting copy (FR-40)

62. **Processed copies wait in `<event>/.WeFlow/work/`**, not in app data. It sits on the
    same drive as the event, so keeping a copy is a plain move, and it goes wherever the
    event folder goes. The library then shows originals only. **(owner's choice)**
63. **Delete only after the album step succeeds**, not right after upload. An album
    failure keeps the file until retry gets it in. The file is tidied before the photo
    is marked uploaded, so a kill in between only repeats the album step, which needs
    no file. **(owner's choice)**
64. **"Keep processed copies after upload" is an event toggle, off by default.** On, the
    copy moves into `processed/` instead of being deleted. **(owner's choice)**
65. **Existing files are left alone.** Processed copies already in `processed/` from
    earlier versions are never deleted, even when those photos upload after the
    update. **(owner's choice)**
66. **A missing processed copy is remade before upload**, rather than failing, since the
    original is always there.
67. **"Open folder" opens `originals/`** for upload events that don't keep processed
    copies, since `processed/` will be empty.

## 1.6.0 — make a gallery from WeFlow (FR-41)

68. **Creating galleries is back in scope** at the owner's request. This replaces #61.
    **(owner's choice)**
69. **The first-run wizard now shows the gallery list**, which replaces #60. Step 2 signs
    in just before, so the plain box there was where people looked for the list and
    couldn't find it. If the user skipped sign-in, the box works as typed, as before.
70. **New SmugMug galleries go at the top of the site, Unlisted.** Unlisted means people
    with the link can see the gallery, but it isn't listed on the site. Change it on SmugMug if
    needed. WeFlow has no folder or privacy picker, so the face stays small.
71. **No change to the SmugMug sign-in scope** (still `Access=Full, Permissions=Add`).
    Adding a gallery counts as adding content. If SmugMug refuses with 403, the message
    asks the user to reconnect. Nobody is signed out by the update.
72. **Flickr "new album" is only a title.** Flickr albums need a photo, so the album is
    made on the first upload, exactly as a typed title was. WeFlow says so under the box.
73. **The gallery box is one widget (`ui/widgets/album_picker.py`)** shared by the event
    editor and the wizard, so the two can't drift apart.

## 1.6.1 — logs to share, a shorter gallery list

74. **Copy log copies only the newest 60,000 characters**, cut at a whole line, so the text
    fits in a message. The files are now 3 × 500 KB.
75. **SmugMug calls are logged with their path, status, time and error type**, never
    headers or bodies, so OAuth tokens can't leak into a pasted log.
76. **The list still fetches every gallery**, because SmugMug's gallery list isn't
    sorted by date on the server. Only the dropdown is cut to 20 + "More…". To make
    the fetch faster, each page asks for 5 fields (`_filter`), and reads may take
    45 s, up from 15 s. **(owner's request: about 20, then more)**

## 1.6.2 — check for updates on demand

77. **"Check for updates" sits in the Settings footer, next to the version.** It works even
    with the daily check off, because pressing it is an explicit request. It says "Up to
    date" or "Can't check right now", unlike the silent daily check (#36). A new version
    shows as the existing "Download x.y.z" link, which replaces the button.

## Not verified yet

These need hardware, accounts or other operating systems (see `docs/manual-test.md`):
real cards on macOS and Windows; eject; real SmugMug and Flickr uploads and connect
flows, including creating a SmugMug gallery (FR-41); tray icon and notifications; installers, signing and notarisation; start at
login; NFR-03 and NFR-08 with real 24 MP and 45 MP files.

## 1.6.3 — one keychain entry for all secrets

Unsigned macOS builds change code identity every release, and each of the eight keychain
entries asked for permission on its own; "Always Allow" often did not stick. Secrets now
live in one entry (`credentials`, JSON) read once per run and cached in memory. The first
run of each version rewrites it, which makes that build the entry's creator so later runs
do not ask. The eight old entries are read once to migrate and left in place, so an
older version still finds its sign-in. The unsigned build is now ad-hoc signed as a whole
bundle so its signature is valid.

## 1.6.4 — keychain failures never block sign-in

If macOS will not let this build read or replace the `credentials` entry (made by an
older unsigned build, or the user pressed Deny), secrets go to a spare entry,
`credentials-2`, that this build creates itself. The spare entry is read first when it
exists. Old per-item entries are read best effort, one refusal no longer fails the rest.
Keychain errors are logged (entry name and OS error only) so "Copy log" shows the cause.

## 1.6.6 — "Flowing…" cover for slow buttons

SPEC §7 has no busy state for a whole window. Buttons whose job can take seconds
(Connect, Finish, Disconnect, Check for updates, gallery Reload and Create) now cover
their window with the background colour at 82% and a centred "Flowing…" with a thin
bar moving along it. SPEC §7 avoids spinners where a progress bar can be shown, and
these jobs have no progress to show, so the bar moves without a fixed end. The cover
waits 300 ms before appearing, so quick answers never flicker, and it blocks clicks and
keys until the job ends. The gallery list loaded when the editor opens stays inline
("Loading your galleries…"), so the other fields can be filled in meanwhile.

## 1.6.8 — Looping logo on the "Flowing…" cover

The owner asked for the We Run Huntsville loading loop (60 PNG frames, 2.4 s, 400 × 520
at 2x) in place of the thin flowing bar. It is the one place outside status where colour
appears, because it is brand artwork, not a widget colour; the word "Flowing…" stays
under it. The frames keep their own white panel (rounded corners) in both themes, as the
kit asks, and shrink to fit short windows. They are decoded once on a worker thread at
start-up, so the UI thread does no file I/O; until they are ready the cover shows just
the word. With motion off it shows the kit's still image. Only frames, still and
manifest ship (6 MB); the kit's GIF, WebP, intro video and sources do not.

## 1.7.0 — Transfer speed, "copy done", resize removed

The owner asked to drop the "Resize long edge" option (FR-24's optional resize). The field
is gone from the event editor and processing always keeps full size, so an older event
that had a size set is no longer shrunk silently. `max_long_edge` stays in the event JSON
(always 0 when saved) so older and newer versions read the same files.

While copying, the status line shows photos done, photos left, MB/s and time left; while
uploading, the same from finished uploads (uploaders send whole files, so the speed is
averaged over the last 20 s of finished photos). The safe line keeps the §7.3 text and
adds how long the copy took. When the card is out, or for a folder import, "Processing"
now starts with "Copy done", and a folder import logs a "copy done" notice. Speed fields
are saved with the batch but reset to 0 on the next launch.

## 1.7.1 — No "More" section in the event editor

SPEC §7 puts rarely changed fields (resize, margin) in a collapsed "More" section. Resize
went in 1.7.0, leaving only the margin, so the margin slider ("Margin") now sits with Size
and Opacity under Watermark and the "More" section is removed. Thin separators divide the
Destination, Look, Watermark and Export sections. The saved field (`watermark_margin`) is
unchanged.

## 1.8.1 — Gallery chooser, no Keywords box, zoomable preview, one main panel

The owner found the gallery dropdown confusing, especially while making a new gallery
(was the photo going to the new one or the selected one?). The editor now shows the
chosen gallery in a read-only box with a "Choose Gallery…" button. That opens a window
listing every gallery (searchable, no "More" paging), with "Create a gallery…" as the
first row. Creating swaps the list for a naming step, and the new gallery is chosen
when it is made. Pasting a SmugMug link still works through the search box ("Use this
link"). Typing a Flickr title in the field is replaced by "Create an album…", which
already worked that way (FR-41). The bridge fetches each signed-in service's list in the
background after every sign-in check, so the list is ready before the editor opens.

The Keywords box is removed at the owner's request. `keywords` stays in the event JSON,
and keywords saved by older versions are still sent with uploads, so events read the
same in every version.

The before/after preview zooms (trackpad pinch, touch pinch, Ctrl + scroll, +/-, 0) up to
8x, around the fingers or pointer. The split line is stored as a point on the photo, so
it zooms and pans with the photo. Dragging near the line moves it; dragging elsewhere
pans; double-click toggles between 2x and the whole photo.

The main window's event card and status panel are merged: the event line ("Photos go to
<event>  Switch") sits in the status panel under the headline, in every state, and the
status icon is 56 px instead of 20. Switch is still always visible, so UI-05's control
count is unchanged.


## 1.9.0 — Update now on Windows (FR-44)

At the owner's request the Windows app updates itself. It reuses the Inno Setup
installer, which already upgrades in place because the AppId never changes, so there is no
separate updater framework.

- The download URL is built from the version (`releases/download/v<ver>/WeFlow-Setup-<ver>.exe`
  in the public repo), so the update check is unchanged. CI publishes
  `WeFlow-Setup-<ver>.exe.sha256`, written after signing; a missing or mismatched checksum
  means nothing is installed. Files go to `<app-data>/updates/`, and earlier ones are removed.
- The installer runs with `/SILENT` (a progress window, no questions) and `/RELAUNCH=1`,
  which a `[Code]` check uses to start WeFlow again. `/CURRENTUSER` or `/ALLUSERS` follows
  where WeFlow is installed now, so an update never makes a second copy.
- It never starts while a batch is copying, processing or uploading. Photos on hold do
  not block it. The bar says the update is ready and will install when the photos are done.
- The app downloads the file itself, so Windows does not mark it as from the internet and
  SmartScreen does not prompt. Only the HTTPS GitHub download and the checksum guard it;
  signing the installer is still recommended.
- macOS keeps **Download**: replacing a running `.app` needs signing, notarisation and
  a framework such as Sparkle.
- Versions before 1.9.0 can only offer **Download**, so getting onto 1.9.0 is one last
  manual install.

## 1.13.1 — Highlights slider (FR-21b), gentler white balance default (1.13.0 never shipped)

- **CI tests on macOS and Windows only** (the owner asked). WeFlow never shipped for
  Linux; the Linux test job only re-ran the same tests, and it hung once on the 1.13.1
  tag. Local development on Linux still works (`QT_QPA_PLATFORM=offscreen pytest`).
- **Released as 1.13.1.** The v1.13.0 tag was pushed while GitHub Actions could not run
  (billing), then What's new (FR-45) was added. A new version avoids force-moving a pushed
  tag. Nothing was ever built or published as 1.13.0.

- **Highlights mirrors Shadows** (`v + k·v²·(1−v)`), so it fits the existing lookup table
  and costs nothing per photo. Negative pulls bright tones down, as in Lightroom.
- **Considered and left out** (from an outside Lightroom-style processing proposal):
  Clarity, Texture, extra sharpening and noise reduction. Camera JPEGs are already
  sharpened and denoised, a second pass on 8-bit data risks halos and rough skin, and
  full-size blurs would slow every photo during a live event. Runner detection would
  ship a machine-learning model in both installers. Vibrance is deferred until it can be
  judged on real photos.
- **Auto WB default strength 0.4** (was 0.6; the owner asked for a lower default).
  Modern cameras' own white balance is good, and gray-world guesses wrong on scenes
  dominated by one colour. Saved events store their strength, so only new events change.
- **`tools/compare_looks.py`** writes before/after pairs for a folder of real photos so
  Look changes can be judged on the owner's own pictures. It is not in the app or builds.
- **What's new covers every skipped version (FR-45, new).** It already stored
  `seen_version`, so it now shows each changelog section after it, newest first, in one
  window with a heading per version. With no `seen_version` (an install older than 1.1.0)
  only the running version is shown, as before. The public release page still carries one
  version's notes; people read notes in the app, so that is left alone.

## 1.12.0 — Speed setting (FR-25)

- **Three levels, not a free number** (the owner asked for Low / Medium / High). Medium is
  the spec's old fixed behaviour, so nothing changes until a user moves the slider.
- **High** runs `cpu_count - 1` processing workers, 4 uploads and a 240 MP pixel budget
  (about 2 GB). NFR-08's 1 GB cap now holds for Medium only. Card copying is unchanged
  at every level: it is limited by the card reader, not the computer.
- **Changes apply live.** The pipeline starts enough threads for High once, and an
  adjustable slot count decides how many may work at a time. A worker that already holds
  a photo finishes it before a lower limit takes effect.
- **The slider lives in Settings**, not on the working face, which already has its four
  controls (UI-05). It uses the accent colour like the toggles; it isn't a status.

## 1.11.0 — Flickr albums sorted by date taken (FR-31a)

- **Sorting is a fixed order, not Flickr's sort setting.** Flickr's public API has no call
  for an album's "Sort by date taken" option; it's website-only. WeFlow applies the same
  order with `photosets.reorderPhotos` instead (the owner agreed). Photos added later on
  the website won't slot in until WeFlow next sorts that album.
- **Once per batch** (the owner's choice), when nothing in the batch is left to upload.
  Two upload workers can finish together, so the pipeline remembers how many photos were
  up at the batch's last sort and skips a repeat. A later Retry that gets more photos up
  sorts again.
- **The whole album is sorted**, including photos that were in it before (the owner's
  choice). Equal or missing dates keep the album's existing order.
- **A failed sort is not retried on its own.** The photos are already up, and the next
  batch for that album sorts it anyway. One notice says so.
- **SmugMug is unchanged** (the owner's choice): `sort_album` is a no-op there.

## 1.10.1 — Three stages on the main screen, auto-eject, camera file names, Shadows

1.10.0 was tagged but never shipped: its Windows tests showed the event editor could still
cut off its switches. The settings column now re-measures whenever its contents re-lay
themselves out (Windows applies fonts when the dialog is first shown, after the first
measurement), and widens itself if the form still ends up wider than it.

At the owner's request:

- **Three stages replace the 4-step tracker and progress bar** (§7.3). Transfer, Process
  and Upload sit in columns, each a big icon in a ring that fills while it works and turns
  into a green tick when done. The icons are 28 px, larger than the 16/20 px icon rule,
  because they are the main thing to read from across the room. The headline stays, so
  the 2 m test ("can the card come out?") still has a sentence to answer it. "Done" is no
  longer a separate step: three ticks mean done.
- **Each stage can show a different card** (the owner's choice). A stage only borrows an
  earlier card when the newest card has not reached it yet. That way an error or a waiting
  step on the newest card is never hidden behind another card's progress.
- **Motion:** one shared 1.6 s clock drives a breathing halo and the progress arc. It runs
  only while a stage is active and stops when nothing is working. A quarter arc goes round
  only while the total is unknown ("Reading the card…"), so the "no spinners where a
  progress bar can be shown" rule still holds.
- **Window:** default height 720 → 760, minimum 600 → 640, and "Earlier cards" shows 3
  rows before scrolling (was 5), so the stages fit at the minimum size.
- **Auto-eject** is a setting, off by default (the owner's choice). It runs once per batch,
  only after the batch is verified, and only while WeFlow still sees the card. If it fails,
  the message says the card is safe to remove and asks the user to eject it, because the
  copy is already safe.
- **Originals keep the camera's file name**, extension case included (`IMG_0001.JPG`).
  Collisions get `_2`, `_3` before the extension, as before. The capture date is still read
  and stored, so "Date - Filename" naming is unchanged. Files imported earlier keep their
  old names, and `naming.original_stem` still understands them. With the default "Import
  name" rule, processed copies now carry the camera name too.
- **Shadows** is a per-event slider from −100 to +100 (`Profile.shadows`, −1…1, default
  0). It is not part of export presets, because presets never hold look settings (FR-28).
- **The white-balance slider seemed to do nothing** under strong indoor light. Every
  neutral pixel looked too saturated, so no pixels qualified and the gains stayed at 1.0.
  Saturation is now judged after a first clamped gray-world pass, and the gain limits widen
  from [0.75, 1.35] to [0.7, 1.6] so tungsten can be corrected. A clearly coloured subject
  (a red wall) is still not treated as a cast.
- **Event editor width:** the settings column was fixed at 304 px. At larger Windows text
  sizes the rows grew wider than that and the toggles were cut off (the right-arrow key
  scrolled them into view). The column now sizes to its widest row (at least 320 px), long
  toggle captions wrap, and the dialog is resizable but never bigger than the screen.

## 1.14.0 — new main screen, Speed in the top bar

- **The main screen follows the owner's mockup** (a wide, dark screen: brand and
  Auto | Paced on top, the upload destination under it, a big headline over a photo, and
  the three stages along the bottom). SPEC §7.2, §7.3 and §7.5 were updated to match.
  Deviations from the old §7, all asked for by the mockup:
  - **Green accent** (`#157A3A` light, `#1DB954` dark) instead of blue. Both pass AA as
    text on every neutral surface. In dark mode the label on a green button is `bg`
    (near black), because white on a bright green fails AA.
  - **The hero has no tinted panel.** Status colour is carried by the icon beside the
    headline and by the words, so colour still always comes with an icon or a word.
  - **One gradient**: the hero photo fades into `bg`. It is painted, not in the QSS.
  - **Bigger window** (960×720, minimum 760×640) and **2 earlier-card rows** before the
    list scrolls (was 3), so the hero keeps its room.
- **The hero photo is the active event's newest photo** (processed if any are kept,
  otherwise the originals), in black and white. The mockup used a stock photo; WeFlow has
  none to ship, and the user's own latest shot is more useful. It is found and decoded on
  a worker thread (`Bridge.latest_photo`), again when the event or the newest card's
  state changes. With no photos yet, the hero is plain.
- **Pause stays in the top bar** as a small "Pause" / "Resume" button. The mockup shows
  only the paused state (where the big Resume button is), but watching must still be
  pausable from the other states.
- **Speed moved from Settings to the top bar, labelled LOW · DEFAULT · MAX** (the owner
  asked). That makes five always-visible controls (UI-05 said four); the owner asked for
  it. The stored values stay `low`, `medium` and `high`, so settings files from 1.12.0
  and 1.13.x keep working.
- **The speed slider glitch on macOS** came from naming the knob's animated Qt property
  `pos`, which QWidget already has: the animation moved the whole slider over the
  heading. It is now `knob`. The same trap with `size` was avoided for the new big
  buttons (their style property is `scale`). A test checks the slider stays put.
- **Linux code removed**: card discovery under `/media` and `/run/media`, and ejecting
  with `umount`. See decision 24.

