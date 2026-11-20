# Packaging WeFlow

Both installers are built by CI (`.github/workflows/ci.yml`, job **Build**) and
attached to the run as artifacts. To build one by hand:

```bash
pip install -e ".[build]"
python packaging/make_icons.py          # only when the icon design changes
./packaging/build_macos.sh              # macOS  -> dist/WeFlow-<version>-MACOS-<arch>.dmg
pwsh packaging/build_windows.ps1        # Windows -> dist\WeFlow-Setup-<version>-WINDOWS.exe
```

The Windows build needs [Inno Setup 6](https://jrsoftware.org/isinfo.php).
The macOS build is for Apple silicon only and must run on an Apple silicon Mac.
Intel Macs are not supported.

## Releasing

1. Set the version in `WeFlow/__init__.py`, `pyproject.toml` and
   `packaging/WeFlow.spec`, and commit.
2. Tag that commit and push the tag:

   ```bash
   git tag v1.1.0 && git push origin v1.1.0
   ```

CI tests and builds the tag, then the **Release** job publishes the installers to a
GitHub Release called `v1.1.0`. The job stops if the tag does not match
`WeFlow.__version__`. Running it again for the same tag replaces the files.

### Releasing without CI

When Actions can't run, build on your own Mac and Windows PC and publish with
`packaging/publish.sh` (see its header). Push the tag, then:

1. Anywhere: `./packaging/publish.sh draft` makes a draft release. Drafts are never
   offered as updates.
2. On the Mac, at the tag: `./packaging/build_macos.sh`, then
   `./packaging/publish.sh upload dist/*.dmg`.
3. On Windows, at the tag: `pwsh packaging/build_windows.ps1`, then upload the `.exe` and
   `.exe.sha256` with `gh release upload <tag> <files> --clobber` (or `publish.sh upload`
   from Git Bash).
4. Anywhere: `./packaging/publish.sh go` checks all three files are there, then publishes.

### Where releases live

Releases are published on this repo, which is public. Installed apps (1.19.1 and newer)
ask it for its latest release to know when to offer an update (`WeFlow/core/updates.py`).

- **Release notes** are the `## <version>` section of `CHANGELOG.md`, so write that
  section for users. Without one, the notes are just the version name.
- **Pre-releases and drafts** are never offered as updates.
- **Everything in an installer is public**, which is one reason no API key is built in
  (below).

### The old releases repo

Until 1.18.0 this repo was private, so installers went to
[`TheOrangeStuff/weflow-releases`](https://github.com/TheOrangeStuff/weflow-releases),
which apps on 1.18.0 or older still check. It is archived with 1.19.1 as its last
release: those apps update to 1.19.1, which checks this repo. Don't delete it, or they
are never offered an update.

## Signing and notarisation

Nothing secret lives in the repo. The scripts read these environment variables
(in CI: repository secrets of the same names) and skip the step when they are unset.

| Variable | Used for |
|---|---|
| `MACOS_SIGN_IDENTITY` | `codesign` identity, e.g. `Developer ID Application: Name (TEAMID)` |
| `MACOS_CERT_P12_BASE64`, `MACOS_CERT_PASSWORD` | CI only: the certificate to import into a temporary keychain |
| `APPLE_ID`, `APPLE_TEAM_ID`, `APPLE_APP_PASSWORD` | `notarytool` submission and stapling |
| `WINDOWS_CERT_FILE`, `WINDOWS_CERT_PASSWORD` | `signtool` with a `.pfx` certificate |
| `WINDOWS_CERT_PFX_BASE64` | CI only: the `.pfx`, decoded to `WINDOWS_CERT_FILE` |

Unsigned builds work, but macOS Gatekeeper and Windows SmartScreen warn on first open.

## API keys and credentials

No SmugMug or Flickr credential is built into WeFlow or kept on GitHub, in the repo or as
a CI secret. Each user enters their own API key and secret in Settings (or at first run)
and signs in; both live only in that computer's keychain. `tests/test_architecture.py`
fails if a key reaches the workflow or the package, and lists the only CI secrets allowed:
the optional signing ones above.

## What an update must not break

- **Sign-in.** Installing a newer version leaves the keychain and the app-data folder
  alone, so nobody re-enters a key or reconnects. Keychain entry names are frozen by
  `tests/test_config.py`. Clear a stored sign-in only when the changed feature cannot work
  with it, and say so in that version's changelog section.
- **macOS keychain prompt.** The keychain ties each entry to the app's code signature. A
  build signed with the same Developer ID reads it silently. An unsigned build looks like
  a different app after every update, so macOS asks for the login password once ("Always
  Allow"). The stored values are still there; only signing removes the prompt.
- **What's new.** `CHANGELOG.md` is bundled into the app. The first start of a newer
  version shows the bullets under `## <version>` once; a new install shows nothing.
  The Release job and the tests fail when that section is missing.

## Checking a build

```bash
dist/WeFlow/WeFlow --smoke-test          # starts, shows the window, exits 0 after 2 s
```

CI runs this on every build. The full check on real hardware is `docs/manual-test.md`.
