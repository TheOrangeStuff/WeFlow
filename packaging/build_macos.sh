#!/usr/bin/env bash
# Build WeFlow.app and WeFlow-<version>-MACOS-<arch>.dmg. Run on macOS from the repo root.
#
# Optional signing and notarisation, all read from the environment (NFR-07):
#   MACOS_SIGN_IDENTITY   e.g. "Developer ID Application: Your Name (TEAMID)"
#   APPLE_ID, APPLE_TEAM_ID, APPLE_APP_PASSWORD   for notarytool
# Without them the build is unsigned (Gatekeeper will warn on first open).
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(python -c 'import WeFlow; print(WeFlow.__version__)')"
ARCH="$(uname -m)"
if [[ "$ARCH" != "arm64" ]]; then
  echo "WeFlow is built for Apple silicon only; this Mac is $ARCH." >&2
  exit 1
fi
DMG="dist/WeFlow-${VERSION}-MACOS-${ARCH}.dmg"

rm -rf build dist
pyinstaller --noconfirm packaging/WeFlow.spec

if [[ -n "${MACOS_SIGN_IDENTITY:-}" ]]; then
  echo "Signing with ${MACOS_SIGN_IDENTITY}"
  codesign --force --deep --options runtime --timestamp \
    --entitlements packaging/entitlements.plist \
    --sign "${MACOS_SIGN_IDENTITY}" dist/WeFlow.app
  codesign --verify --deep --strict dist/WeFlow.app
else
  # Ad-hoc sign the whole bundle so its signature is valid and sealed. Without this,
  # macOS cannot remember "Always Allow" for the keychain and asks on every launch.
  echo "MACOS_SIGN_IDENTITY not set: ad-hoc signing (Gatekeeper will still warn)"
  codesign --force --deep --sign - dist/WeFlow.app
  codesign --verify --deep --strict dist/WeFlow.app
fi

STAGE="$(mktemp -d)"
cp -R dist/WeFlow.app "${STAGE}/"
ln -s /Applications "${STAGE}/Applications"
hdiutil create -volname "WeFlow" -srcfolder "${STAGE}" -ov -format UDZO "${DMG}"
rm -rf "${STAGE}"

if [[ -n "${MACOS_SIGN_IDENTITY:-}" ]]; then
  codesign --force --timestamp --sign "${MACOS_SIGN_IDENTITY}" "${DMG}"
fi
if [[ -n "${APPLE_ID:-}" && -n "${APPLE_TEAM_ID:-}" && -n "${APPLE_APP_PASSWORD:-}" ]]; then
  echo "Notarising ${DMG}"
  xcrun notarytool submit "${DMG}" --wait \
    --apple-id "${APPLE_ID}" --team-id "${APPLE_TEAM_ID}" --password "${APPLE_APP_PASSWORD}"
  xcrun stapler staple "${DMG}"
else
  echo "Apple notarisation variables not set: skipping notarisation"
fi
echo "Built ${DMG}"
