# WeFlow — notes for Claude Code

WeFlow is a desktop app (macOS + Windows) that copies JPEGs from a camera card, verifies the copies, signals "safe to remove", then auto-adjusts, watermarks and uploads the photos to SmugMug or Flickr.

**The full build spec is in `SPEC.md`.** Read it before starting any milestone. Work through the milestones in §10 in order, and stop for review after each one.

## Non-negotiable rules

- **Never write to, rename or delete anything on a memory card**, in code or in tests. Ingest opens card files read-only.
- **Never show "safe to remove"** until every file is copied and its checksum verified.
- **Never upload a photo twice.** Upload and add-to-album are separate steps, and a stored `remote_id` means the photo is already up.
- `WeFlow/core` never imports Qt. The UI reaches core only through `WeFlow/ui/bridge.py`.
- The UI thread never does file I/O, image work or network calls.
- Secrets live only in the OS keychain (`WeFlow/core/secrets.py`), never in JSON files or logs.
- **No SmugMug or Flickr credentials on GitHub or in a build**: not in the repo, not in CI secrets, not baked into installers. Each user enters their own API key and signs in.
- **An update never signs the user out.** Keychain entry names and the settings files stay readable by newer versions. Only clear a stored sign-in when the changed feature cannot work with it, and say so in that version's changelog section.
- Tests and CI never call the real SmugMug or Flickr APIs. Use `FakeUploader`.
- The design follows SPEC §7 exactly: sleek, modern, minimal. Use design tokens only; no hard-coded colours in widgets.
  - Colour is reserved for status, and always comes with an icon or a word.
  - The working face may show at most 3 always-visible controls.

## Commands

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
ruff check . && ruff format --check .
mypy --strict WeFlow/core
QT_QPA_PLATFORM=offscreen pytest -q
python -m WeFlow                                  # run the app
WeFlow_FAKE_VOLUMES=tests/fixtures/volumes python -m WeFlow   # simulate cards
python tests/fixtures/make_card.py tests/fixtures/volumes/CARD_A --count 25
```

## Conventions

- Python 3.11+, type hints throughout, dataclasses for data, `pathlib` for paths.
- Name tests after the requirement they cover: `test_fr15_error_batch_never_safe`.
- Commit messages start with the requirement or milestone id: `M3: StatusPanel safe state (UI-03)`.
- User-facing text is plain language, at most 2 short sentences, saying what happened and what to do.
- Record any deviation from SPEC.md, and why, in `DECISIONS.md`.
- Every release needs a `## <version>` section in `CHANGELOG.md`, written for users as plain bullets. The app shows it as "What's new" and the public release page uses it as notes.
- When the spec is ambiguous and the choice is hard to undo (data layout, public API, the visual system), ask. Otherwise choose the simplest option and record it.
