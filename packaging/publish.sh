#!/usr/bin/env bash
# Release without CI: the same result as CI's Release job, from your own computers.
# Run from the repo root, signed in with `gh auth login`, after the tag is pushed.
#
#   ./packaging/publish.sh draft          # 1. draft releases with the changelog notes
#   ./packaging/publish.sh upload FILE... # 2. on each build machine: add its installers
#   ./packaging/publish.sh go             # 3. both installers there: publish
#
# Each step works on this repo's release, which installed apps check for updates, and
# on the old releases repo, which apps on 1.18.0 or older still check. Drafts are never offered as updates, so nothing is
# visible until `go`. Windows without bash can upload with two `gh release upload` lines
# (packaging/README.md).
set -euo pipefail

VERSION="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' WeFlow/__init__.py)"
TAG="v${VERSION}"
OLD_RELEASES_REPO="$(sed -n 's/^OLD_RELEASES_REPO = "\(.*\)"$/\1/p' WeFlow/core/updates.py)"
SOURCE_REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
ASSETS=("WeFlow-${VERSION}-arm64.dmg" "WeFlow-Setup-${VERSION}.exe" "WeFlow-Setup-${VERSION}.exe.sha256")

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
    for repo in "$SOURCE_REPO" "$OLD_RELEASES_REPO"; do
      gh release upload "$TAG" "$@" --repo "$repo" --clobber
      echo "Uploaded to ${repo}"
    done
    ;;
  go)
    for repo in "$SOURCE_REPO" "$OLD_RELEASES_REPO"; do
      have="$(gh release view "$TAG" --repo "$repo" --json assets -q '.assets[].name')"
      for asset in "${ASSETS[@]}"; do
        grep -qxF "$asset" <<< "$have" || fail "${repo} is missing ${asset}"
      done
    done
    for repo in "$SOURCE_REPO" "$OLD_RELEASES_REPO"; do
      gh release edit "$TAG" --repo "$repo" --draft=false --latest
      echo "Published ${TAG} in ${repo}"
    done
    ;;
  *)
    fail "usage: packaging/publish.sh draft | upload FILE... | go"
    ;;
esac
