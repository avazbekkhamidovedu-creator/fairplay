#!/usr/bin/env bash
# Puts release files on a public download repository (GitHub Releases).
# Usage: scripts/publish-download.sh <owner/repo> <tag> <notes-file> <file>...
# GH_TOKEN must be a fine-grained token with Contents read and write on <owner/repo>.
# Creates the release when it is not there yet (another workflow may create it at the same
# time), then uploads the files, replacing files of the same name.
set -euo pipefail
R=$1 TAG=$2 NOTES=$3
shift 3
if ! gh release view "$TAG" -R "$R" >/dev/null 2>&1; then
  if ! out=$(gh release create "$TAG" -R "$R" --draft --title "FairPlay $TAG" --notes-file "$NOTES" 2>&1); then
    gh release view "$TAG" -R "$R" >/dev/null 2>&1 || {   # not created meanwhile by another workflow
      echo "$out"
      echo "::error::Creating release $TAG on $R failed: $(echo "$out" | tr '\n' ' ' | cut -c1-300)"
      echo "::error::The token needs Repository access to $R with Contents read and write"
      exit 1; }
  fi
fi
if [ "$(gh release view "$TAG" -R "$R" --json isDraft --jq .isDraft)" != true ] && [ "${REPLACE_PUBLISHED:-}" != yes ]; then
  echo "::error::Refusing to replace assets on a published release: $R $TAG"
  exit 1
fi
if ! out=$(gh release upload "$TAG" -R "$R" "$@" --clobber 2>&1); then
  echo "$out"
  echo "::error::Upload to $R failed: $(echo "$out" | tr '\n' ' ' | cut -c1-300)"
  exit 1
fi
echo "::notice::uploaded to $R $TAG: $*"
