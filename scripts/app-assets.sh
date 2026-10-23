#!/bin/bash
# The asset pack the Mac and iPad apps ship (2026-10-23): the exam page's heavy files (the
# Python engine, pdf.js worker, character maps, fonts), made by the web build into
# server/static/app-assets with manifest.json {"version": "...", "files": {"path": "sha256"}}.
# The apps serve it to the exam page as fairplay-asset://bundle/<path>.
#
#   bash scripts/app-assets.sh verify [dir]   every listed file there with the right sha256
#   bash scripts/app-assets.sh copy <dest>    verify, copy into an app bundle, verify the copy
#   bash scripts/app-assets.sh xcode          the iPad Xcode build phase ("Asset pack")
#   bash scripts/app-assets.sh ensure         CI: build the web app when the pack is missing
#
# The source is server/static/app-assets (FAIRPLAY_APP_ASSETS=<dir> for another one). A
# missing or incomplete pack fails the build, so no app ships without it. Dev builds only:
# FAIRPLAY_ALLOW_NO_ASSETS=1 builds without (the exam page then downloads the files).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${FAIRPLAY_APP_ASSETS:-$REPO/server/static/app-assets}"

verify() {
  local dir="$1"
  if [ ! -f "$dir/manifest.json" ]; then
    echo "error: no asset pack at $dir (manifest.json missing). Build the web app first: cd web && npm ci && npm run build" >&2
    return 1
  fi
  if [ -n "$(find "$dir" -type l -print -quit)" ]; then
    echo "error: the asset pack $dir contains symlinks" >&2
    return 1
  fi
  python3 -I - "$dir" <<'PY'
import base64, hashlib, json, os, sys

root = os.path.realpath(sys.argv[1])
with open(os.path.join(root, "manifest.json"), "rb") as f:
    manifest = json.load(f)
version = manifest.get("version") if isinstance(manifest, dict) else None
files = manifest.get("files") if isinstance(manifest, dict) else None
errors = []
if not isinstance(version, str) or not version.strip() or len(version.strip()) > 128:
    errors.append("manifest.json has no usable version")
if not isinstance(files, dict) or not files:
    errors.append("manifest.json lists no files")
    files = {}
total = 0
for rel, want in sorted(files.items()):
    parts = rel.split("/")
    # Same rule as the apps (AssetPack.safeRelativePath): plain names only.
    if rel.startswith("/") or "\\" in rel or any(p in ("", "~") or p.startswith(".") for p in parts):
        errors.append("unsafe path %r" % rel)
        continue
    path = os.path.join(root, *parts)
    if not os.path.isfile(path):
        errors.append("missing " + rel)
        continue
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    want = want if isinstance(want, str) else ""
    if want.startswith("sha256-"):
        want = want[len("sha256-"):]
    if want.lower() != h.hexdigest() and want != base64.b64encode(h.digest()).decode():
        errors.append("sha256 differs: " + rel)
        continue
    total += os.path.getsize(path)
# 0.6.2 review: nothing in the pack that the manifest does not list (the apps serve only
# listed files anyway; an extra file means the pack was changed by hand).
listed = set(files) | {"manifest.json"}
for dirpath, dirnames, filenames in os.walk(root):
    for name in filenames:
        if name == ".DS_Store":       # Finder's; copy_into deletes it from the app copy
            continue
        rel = os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/")
        if rel not in listed:
            errors.append("not in the manifest: " + rel)
if errors:
    more = " (+%d more)" % (len(errors) - 10) if len(errors) > 10 else ""
    print("error: asset pack %s: %s%s" % (root, "; ".join(errors[:10]), more), file=sys.stderr)
    sys.exit(1)
print("asset pack %s: %d files, %.1f MB (%s)" % (version.strip(), len(files), total / 1e6, root))
PY
}

copy_into() {
  local dest="$1"
  verify "$SRC"
  rm -rf "$dest"
  mkdir -p "$(dirname "$dest")"
  if command -v ditto >/dev/null 2>&1; then ditto "$SRC" "$dest"; else cp -R "$SRC" "$dest"; fi
  find "$dest" -name .DS_Store -delete
  verify "$dest"
}

case "${1:-}" in
  verify)
    verify "${2:-$SRC}"
    ;;
  copy)
    [ -n "${2:-}" ] || { echo "usage: $0 copy <dest>" >&2; exit 2; }
    copy_into "$2"
    ;;
  xcode)
    : "${TARGET_BUILD_DIR:?run from the Xcode build phase}" "${UNLOCALIZED_RESOURCES_FOLDER_PATH:?run from the Xcode build phase}"
    DEST="$TARGET_BUILD_DIR/$UNLOCALIZED_RESOURCES_FOLDER_PATH/app-assets"
    if [ "${CONFIGURATION:-}" != Release ] && [ "${FAIRPLAY_ALLOW_NO_ASSETS:-}" = 1 ] && [ ! -f "$SRC/manifest.json" ]; then
      echo "warning: no asset pack (FAIRPLAY_ALLOW_NO_ASSETS=1): the exam page downloads its files"
      rm -rf "$DEST"
      exit 0
    fi
    copy_into "$DEST"
    ;;
  ensure)
    # CI: the committed pack when it is complete, else build it from the web app.
    if verify "$SRC" 2>/dev/null; then exit 0; fi
    echo "The committed asset pack is missing or incomplete: building the web app"
    (cd "$REPO/web" && npm ci --no-audit --no-fund && npm run build)
    if ! verify "$SRC" 2>/dev/null && [ -f "$REPO/web/dist/app-assets/manifest.json" ]; then
      rm -rf "$SRC"
      mkdir -p "$(dirname "$SRC")"
      cp -R "$REPO/web/dist/app-assets" "$SRC"
    fi
    verify "$SRC"
    ;;
  *)
    echo "usage: $0 verify [dir] | copy <dest> | xcode | ensure" >&2
    exit 2
    ;;
esac
