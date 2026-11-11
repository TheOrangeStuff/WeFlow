# Changelog

## 1.1.0 — 2026-11-11

- WeFlow tells you when a newer version is out. A bar in the main window offers
  **Download**, which opens the download page, or **Skip** to hide that version.
  It only appears while WeFlow is idle, and can be switched off in Settings.
- After an update, WeFlow shows what's new once.
- Connecting SmugMug or Flickr asks for your own API key straight away, with a link to
  the page where you get one. Keys you already entered keep working.
- Installers are now also published to the public
  [weflow-releases](https://github.com/TheOrangeStuff/weflow-releases) page.
- WeFlow for Mac now needs a Mac with Apple silicon. Intel Macs are no longer supported.

## 1.0.0 — 2026-11-11

Built from `SPEC.md`, milestone by milestone. Nothing here has yet been tried with a
real card, a real SmugMug or Flickr account, or an installed build; see
`docs/manual-test.md`.

### M7 — Polish
- 200 ms crossfade between status-panel states; sliding activity drawer; animated toggles.
- Empty states: "Earlier cards" and the Activity handle appear only when there is
  something to show; the preview and the activity log explain themselves when empty.
- Every user-facing error is checked to be plain language, two short sentences at most.
- End-user `README.md` and the hardware checklist `docs/manual-test.md`.

### M6 — Packaging
- PyInstaller one-folder spec; `.app` + `.dmg` script for macOS; Inno Setup installer for Windows.
- Signing and notarisation hooks that read credentials from environment variables.
- App icon (`packaging/make_icons.py`), `--smoke-test` launch check, CI build jobs with artifacts.
- Pushing a `v<version>` tag publishes the installers to a GitHub Release.

### M5 — Event editor, Settings, first run
- Event editor (640 px): Destination, Look, Watermark, collapsed "More", inline validation.
- Live before/after preview with a draggable split, debounced and rendered off the UI thread (FR-04).
- Settings: library folder, SmugMug / Flickr connect, reconnect and disconnect, own API key,
  card sound, start at login.
- First-run window: library → connect a service (skippable) → first event.

### M4 — Main window
- The working face wired to the pipeline through `ui/bridge.py`.
- Card notice "Card X → Event" with Cancel and a 5 s auto-start (FR-11); event switcher when
  no event is active.
- Eject with success / failure reported in the window (FR-16).
- Tray icon with four states, menu, throttled notifications, hide-to-tray while working.

### M3 — Theme and widgets
- Design tokens → generated QSS, light and dark, following the OS live.
- EventCard, StatusPanel (all nine states), StepTracker, CardRow, Chip, ActivityDrawer.
- WCAG AA contrast test; screenshots of every state in both themes under `tests/artifacts/`.

### M1 + M2 — Core
- No v0.1 prototype was available, so the engine was written from the spec and hardened
  in the same step (see `DECISIONS.md`).
- Events with stable ids and frozen folders; settings and events saved atomically.
- Secrets in the OS keychain only, with migration of any plain-text v0.1 values.
- Card detection (macOS, Windows, fake volumes for tests) and eject.
- Verified copy: SHA-256 while copying, fsync, re-hash; rename by capture time; de-duplication.
- Auto white balance, auto tone, watermark, EXIF orientation, resize; atomic writes.
- SmugMug and Flickr uploaders; upload and add-to-album as separate, resumable steps.
- Pipeline with saved state after every transition, retries with back-off, hold / release,
  retry failed, resume on launch, coalesced typed events, `stop()`.
- Tooling: `pyproject.toml` with pinned dependencies, ruff, `mypy --strict` on core, pytest,
  GitHub Actions on Ubuntu, macOS and Windows.
