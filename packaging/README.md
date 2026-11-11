# Packaging WeFlow

Both installers are built by CI (`.github/workflows/ci.yml`, job **Build**) and
attached to the run as artifacts. To build one by hand:

```bash
pip install -e ".[build]"
python packaging/make_icons.py          # only when the icon design changes
./packaging/build_macos.sh              # macOS  -> dist/WeFlow-<version>-<arch>.dmg
pwsh packaging/build_windows.ps1        # Windows -> dist\WeFlow-Setup-<version>.exe
```

The Windows build needs [Inno Setup 6](https://jrsoftware.org/isinfo.php).
macOS builds are per architecture: run the script on Apple silicon for arm64 and
on an Intel Mac for x86_64.

## Releasing

1. Set the version in `WeFlow/__init__.py`, `pyproject.toml` and
   `packaging/WeFlow.spec`, and commit.
2. Tag that commit and push the tag:

   ```bash
   git tag v1.0.0 && git push origin v1.0.0
   ```

CI tests and builds the tag, then the **Release** job publishes the installers to a
GitHub Release called `v1.0.0`. The job stops if the tag does not match
`WeFlow.__version__`. Running it again for the same tag replaces the files.

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

## The app's own API keys

SmugMug and Flickr both need an API key that identifies the app.
`packaging/write_builtin_keys.py` bakes them in from
`WEFLOW_SMUGMUG_API_KEY`, `WEFLOW_SMUGMUG_API_SECRET`, `WEFLOW_FLICKR_API_KEY` and
`WEFLOW_FLICKR_API_SECRET`. If they are not set, the build still works: users enter
their own key under **Settings → Use my own API key…**.

## Checking a build

```bash
dist/WeFlow/WeFlow --smoke-test          # starts, shows the window, exits 0 after 2 s
```

CI runs this on every build. The full check on real hardware is `docs/manual-test.md`.
