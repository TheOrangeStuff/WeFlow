#!/usr/bin/env bash
# Release without CI: the same result as CI's Release job, from your own computers.
# Run from the repo root, signed in with `gh auth login`, after the tag is pushed.
#
#   ./packaging/publish.sh draft          # 1. draft releases with the changelog notes
#   ./packaging/publish.sh upload FILE... # 2. on each build machine: add its installers
#   ./packaging/publish.sh go             # 3. both installers there: publish
#
# Each step works on this repo's release, which installed apps check for updates, and
# on the old releases repo, which apps on 1.18.0 or older still check. That repo gets
# copies under the old file names (no -MACOS / -WINDOWS), which those apps download.
# Drafts are never offered as updates, so nothing is visible until `go`. Windows
# without bash: see packaging/README.md.
set -euo pipefail

VERSION="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' WeFlow/__init__.py)"
TAG="v${VERSION}"
OLD_RELEASES_REPO="$(sed -n 's/^OLD_RELEASES_REPO = "\(.*\)"$/\1/p' WeFlow/core/updates.py)"
SOURCE_REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
ASSETS=("WeFlow-${VERSION}-MACOS-arm64.dmg" "WeFlow-Setup-${VERSION}-WINDOWS.exe" "WeFlow-Setup-${VERSION}-WINDOWS.exe.sha256")
# Apps on 1.18.0 or older download these names from the old releases repo.
OLD_ASSETS=("WeFlow-${VERSION}-arm64.dmg" "WeFlow-Setup-${VERSION}.exe" "WeFlow-Setup-${VERSION}.exe.sha256")

# dist/WeFlow-1.2.0-MACOS-arm64.dmg -> a copy named WeFlow-1.2.0-arm64.dmg in $2.
old_copy() {
  local name; name="$(basename "$1")"
  name="${name/-MACOS-/-}"; name="${name/-WINDOWS.exe/.exe}"
  if [[ "$name" == *.sha256 ]]; then
    printf '%s  %s' "$(cut -d' ' -f1 "$1")" "${name%.sha256}" > "$2/$name"
  else
    cp "$1" "$2/$name"
  fi
  echo "$2/$name"
}

fail() { echo "error: $*" >&2; exit 1; }

case "${1:-}" in
  draft)
    git ls-remote --exit-code --tags origin "refs/tags/${TAG}" > /dev/null \
      || fail "push the tag first: git push origin ${TAG}"
    grep -qE "^## v?${VERSION//./\\.}( |$)" CHANGELOG.md \
      || fail "CHANGELOG.md has no '## ${VERSION}' section"
    notes="$(mktemp)"
    trap 'rm -f "$notes"' EXIT
    awk -v v="$VERSION" '/^## /{on = ($2 == v); next} on' CHANGELOG.md > "$notes"
    for repo in "$SOURCE_REPO" "$OLD_RELEASES_REPO"; do
      if gh release view "$TAG" --repo "$repo" > /dev/null 2>&1; then
        gh release edit "$TAG" --repo "$repo" --notes-file "$notes"
      else
        gh release create "$TAG" --repo "$repo" --draft \
          --title "WeFlow ${VERSION}" --notes-file "$notes"
      fi
      echo "Draft ${TAG} ready in ${repo}"
    done
    ;;
  upload)
    shift
    [ $# -gt 0 ] || fail "name the installer files to upload"
    gh release upload "$TAG" "$@" --repo "$SOURCE_REPO" --clobber
    echo "Uploaded to ${SOURCE_REPO}"
    old="$(mktemp -d)"
    trap 'rm -rf "$old"' EXIT
    copies=()
    for file in "$@"; do copies+=("$(old_copy "$file" "$old")"); done
    gh release upload "$TAG" "${copies[@]}" --repo "$OLD_RELEASES_REPO" --clobber
    echo "Uploaded to ${OLD_RELEASES_REPO}"
    ;;
  go)
    check() {
      local repo="$1"; shift
      have="$(gh release view "$TAG" --repo "$repo" --json assets -q '.assets[].name')"
      for asset in "$@"; do
        grep -qxF "$asset" <<< "$have" || fail "${repo} is missing ${asset}"
      done
    }
    check "$SOURCE_REPO" "${ASSETS[@]}"
    check "$OLD_RELEASES_REPO" "${OLD_ASSETS[@]}"
    for repo in "$SOURCE_REPO" "$OLD_RELEASES_REPO"; do
      gh release edit "$TAG" --repo "$repo" --draft=false --latest
      echo "Published ${TAG} in ${repo}"
    done
    ;;
  *)
    fail "usage: packaging/publish.sh draft | upload FILE... | go"
    ;;
esac
