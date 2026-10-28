#!/bin/bash
# Build FairPlay.app (student exam app).   cd ~/code/fairplay/mac && bash build.sh
#
# Dev build (default): local server, public dev key, dev overrides (ServerURL default,
# safety auto-unlock), http allowed for local addresses. Pair with FAIRPLAY_DEV=1 on the server.
#
# Release build (audit 2026-10-12 fix order #2, #3, #6):
#   FAIRPLAY_BUILD=release \
#   FAIRPLAY_SERVER=https://exam.example.edu \
#   FAIRPLAY_APP_KEY=<same value as the server's FAIRPLAY_APP_KEY> \
#   FAIRPLAY_PINS=<base64 sha256 of a CA cert, comma-separated; see pin.sh>   (optional, recommended)
#   FAIRPLAY_EXTRA_ROOT_PEM=<path to a PEM file: the private CA of the exam server>    (optional)
#   FAIRPLAY_SIGN_ID="Developer ID Application: Your Uni (TEAMID)"           (optional)
#   FAIRPLAY_NOTARY_PROFILE=<notarytool keychain profile>                     (optional)
#   bash build.sh
# Every build needs the asset pack server/static/app-assets (made by the web build:
# cd web && npm ci && npm run build); see scripts/app-assets.sh.
# Release builds: https only, no ATS exceptions, no UserDefaults/env overrides, no
# safety unlock, hardened runtime; notarized when a profile is given.
set -euo pipefail
EXTRA_ROOT_FILE="${FAIRPLAY_EXTRA_ROOT_PEM:-}"
[[ -z "$EXTRA_ROOT_FILE" || "$EXTRA_ROOT_FILE" == /* ]] || EXTRA_ROOT_FILE="$PWD/$EXTRA_ROOT_FILE"
cd "$(dirname "$0")"
APP="Proctora"; BUNDLE="FairPlay.app"   # executable keeps its internal name
# Audit M-19: one version source (Config.swift).
VERSION="$(sed -n 's/.*static let version = "\(.*\)".*/\1/p' Sources/Proctora/Config.swift | head -1)"
[ -n "$VERSION" ] || { echo "could not read the version from Config.swift"; exit 1; }
MODE="${FAIRPLAY_BUILD:-dev}"
DEV_KEY="fairplay-dev-app-key-not-secret"

case "$MODE" in
  dev)
    SERVER="${FAIRPLAY_SERVER:-http://127.0.0.1:8000}"
    KEY="${FAIRPLAY_APP_KEY:-$DEV_KEY}"
    SWIFT_FLAGS=(-Xswiftc -DFAIRPLAY_DEV)
    ;;
  release)
    SERVER="${FAIRPLAY_SERVER:?set FAIRPLAY_SERVER=https://your-exam-server}"
    KEY="${FAIRPLAY_APP_KEY:?set FAIRPLAY_APP_KEY (same as the server)}"
    [[ "$SERVER" == https://* ]] || { echo "Release builds need an https:// server"; exit 1; }
    [[ "$KEY" != "$DEV_KEY" && ${#KEY} -ge 32 ]] || { echo "FAIRPLAY_APP_KEY must be a private value of 32+ characters"; exit 1; }
    # Audit M-04: without pins any CA an admin installs can read and change the traffic
    # (and fake the hand-in). Release builds must pin the server's CA (see pin.sh).
    [[ -n "${FAIRPLAY_PINS:-}" ]] || { echo "Release builds need FAIRPLAY_PINS (run: bash pin.sh your-exam-server[:port])"; exit 1; }
    SWIFT_FLAGS=()
    ;;
  *) echo "FAIRPLAY_BUILD must be dev or release"; exit 1 ;;
esac
PINS="${FAIRPLAY_PINS:-}"
# Dev builds write a readable trace (clicks, errors, lock, quits) to <repo>/logs/events.log.
if [ "$MODE" = dev ]; then
  DEBUG_LOG="${FAIRPLAY_DEBUG_LOG:-$(cd .. && pwd)/logs/events.log}"
  [[ "$DEBUG_LOG" != *'"'* && "$DEBUG_LOG" != *'\'* ]] || { echo "bad FAIRPLAY_DEBUG_LOG path"; exit 1; }
else
  DEBUG_LOG=""
fi
[[ "$SERVER" =~ ^https?://[A-Za-z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9._~/-]*)?$ ]] || { echo "bad FAIRPLAY_SERVER: $SERVER"; exit 1; }
[[ "$KEY" =~ ^[A-Za-z0-9._~+/=-]+$ ]] || { echo "FAIRPLAY_APP_KEY: use letters, digits and ._~+/=- only"; exit 1; }
PIN_LIST=()
IFS=',' read -ra PIN_ARR <<< "$PINS"
for p in "${PIN_ARR[@]:-}"; do
  p="$(echo "$p" | tr -d ' ')"; [ -z "$p" ] && continue
  [[ "$p" =~ ^[A-Za-z0-9+/]{43}=$ ]] || { echo "bad pin (want base64 SHA-256): $p"; exit 1; }
  PIN_LIST+=("$p")
done

EXTRA_ROOT=""
if [ -n "$EXTRA_ROOT_FILE" ]; then
  [ -f "$EXTRA_ROOT_FILE" ] || { echo "FAIRPLAY_EXTRA_ROOT_PEM: no such file: $FAIRPLAY_EXTRA_ROOT_PEM"; exit 1; }
  openssl x509 -in "$EXTRA_ROOT_FILE" -noout 2>/dev/null || { echo "FAIRPLAY_EXTRA_ROOT_PEM is not a PEM certificate"; exit 1; }
  EXTRA_ROOT="$(openssl x509 -in "$EXTRA_ROOT_FILE" | grep -v -- '-----' | tr -d '\n')"
fi

# Audit M-14: the source tree is never rewritten. Server, app key, pins and the trace path
# go into Contents/Resources/FairPlayBuild.plist inside the (then signed) app bundle;
# BuildConfig.swift only holds dev defaults. A crash mid-build can't leave a key behind.
if [ -n "${FAIRPLAY_UNIVERSAL:-}" ] || [ "$MODE" = release ]; then
  ARCH_FLAGS=(--arch arm64 --arch x86_64)    # Intel and Apple Silicon (needs full Xcode)
else
  ARCH_FLAGS=()
fi
echo "==> Compiling ($MODE build, server $SERVER${ARCH_FLAGS[*]:+, universal})..."
swift build -c release ${ARCH_FLAGS[@]+"${ARCH_FLAGS[@]}"} ${SWIFT_FLAGS[@]+"${SWIFT_FLAGS[@]}"} || {
  if [ ${#ARCH_FLAGS[@]} -gt 0 ]; then
    echo "Universal build failed (it needs full Xcode, not only the Command Line Tools)."
    echo "Install Xcode, or for a single-architecture test build: FAIRPLAY_UNIVERSAL= bash build.sh"
  fi
  exit 1
}
BIN="$(swift build -c release ${ARCH_FLAGS[@]+"${ARCH_FLAGS[@]}"} --show-bin-path)/$APP"
[ -f "$BIN" ] || { echo "no binary at $BIN"; exit 1; }

echo "==> Assembling $BUNDLE..."
rm -rf "$BUNDLE"; mkdir -p "$BUNDLE/Contents/MacOS" "$BUNDLE/Contents/Resources"
cp "$BIN" "$BUNDLE/Contents/MacOS/$APP"
# Review 2026-10-14: the watchdog runs as its own executable name, so `killall Proctora`
# (e.g. from a script left running before the exam) can't take it down with the app.
cp "$BIN" "$BUNDLE/Contents/MacOS/FairPlayGuard"
GUARD="$BUNDLE/Contents/MacOS/FairPlayGuard"
# App icon: the FairPlay logo (brand/FairPlay.icns, made from brand/mark.svg).
cp ../brand/FairPlay.icns "$BUNDLE/Contents/Resources/FairPlay.icns"
# 2026-10-23: the exam page's heavy files (Python engine, pdf.js) inside the app, served as
# fairplay-asset://bundle/... (AssetSchemeHandler.swift). From server/static/app-assets
# (the web build); a missing or incomplete pack fails the build. Dev builds only:
# FAIRPLAY_ALLOW_NO_ASSETS=1 builds without it.
if [ "$MODE" = dev ] && [ "${FAIRPLAY_ALLOW_NO_ASSETS:-}" = 1 ] && [ ! -f "${FAIRPLAY_APP_ASSETS:-../server/static/app-assets}/manifest.json" ]; then
  echo "==> No asset pack (FAIRPLAY_ALLOW_NO_ASSETS=1): the exam page downloads its files"
else
  echo "==> Asset pack..."
  bash ../scripts/app-assets.sh copy "$BUNDLE/Contents/Resources/app-assets"
fi
CONF="$BUNDLE/Contents/Resources/FairPlayBuild.plist"
plutil -create xml1 "$CONF"
plutil -insert ServerURL -string "$SERVER" "$CONF"
plutil -insert AppKey -string "$KEY" "$CONF"
plutil -insert DebugLogPath -string "$DEBUG_LOG" "$CONF"
plutil -insert Pins -array "$CONF"
i=0
for p in "${PIN_LIST[@]:-}"; do
  [ -n "$p" ] || continue
  plutil -insert "Pins.$i" -string "$p" "$CONF"; i=$((i + 1))
done
[ -z "$EXTRA_ROOT" ] || plutil -insert ExtraRootPEM -string "$EXTRA_ROOT" "$CONF"
chmod 644 "$CONF"   # readable when the app is installed by root (MDM / .pkg); the key is not secret from the student anyway (M-20)
if [ "$MODE" = dev ]; then
  # Dev: plain http to local addresses only (127.0.0.1, .local, LAN IPs). No arbitrary loads.
  ATS='<key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>'
else
  ATS=''   # release: default App Transport Security (https only)
fi
cat > "$BUNDLE/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>FairPlay</string>
  <key>CFBundleDisplayName</key><string>FairPlay</string>
  <key>CFBundleIdentifier</key><string>ai.proctora.student</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>FairPlay</string>
  <key>CFBundleExecutable</key><string>$APP</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.education</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
  $ATS
</dict></plist>
PLIST

if [ -n "${FAIRPLAY_SIGN_SELF:-}" ]; then
  # 2026-10-22: one fixed self-signed certificate (CI imports it from a secret). Not
  # Developer ID, so Gatekeeper still asks once ("Open Anyway"), but the signature stays
  # the same across versions: macOS keeps the Accessibility permission after updates.
  # No --timestamp: Apple's timestamp service only serves Apple-issued certificates.
  echo "==> Signing with '$FAIRPLAY_SIGN_SELF' (fixed certificate, hardened runtime)..."
  codesign --force --options runtime --sign "$FAIRPLAY_SIGN_SELF" "$GUARD"
  codesign --force --options runtime --sign "$FAIRPLAY_SIGN_SELF" "$BUNDLE"
elif [ -n "${FAIRPLAY_SIGN_ID:-}" ]; then
  echo "==> Signing with '$FAIRPLAY_SIGN_ID' (hardened runtime)..."
  codesign --force --options runtime --timestamp --sign "$FAIRPLAY_SIGN_ID" "$GUARD"
  codesign --force --options runtime --timestamp --sign "$FAIRPLAY_SIGN_ID" "$BUNDLE"
  if [ -n "${FAIRPLAY_NOTARY_PROFILE:-}" ]; then
    echo "==> Notarizing..."
    ditto -c -k --keepParent "$BUNDLE" FairPlay.zip
    # Audit M-19: notarytool exits 0 even when Apple says "Invalid"; check the status.
    NOTARY_OUT="$(xcrun notarytool submit FairPlay.zip --keychain-profile "$FAIRPLAY_NOTARY_PROFILE" ${NOTARY_KEYCHAIN:+--keychain "$NOTARY_KEYCHAIN"} --wait 2>&1)"
    echo "$NOTARY_OUT"
    echo "$NOTARY_OUT" | grep -q "status: Accepted" || { echo "Notarization was NOT accepted"; rm -f FairPlay.zip; exit 1; }
    xcrun stapler staple "$BUNDLE"
    rm -f FairPlay.zip
  fi
elif security find-identity -p codesigning 2>/dev/null | grep -q "Proctora Dev"; then
  # Accessibility permission is tied to the code signature: the self-signed
  # "Proctora Dev" certificate keeps the grant across rebuilds.
  echo "==> Signing with 'Proctora Dev'..."
  codesign --force --options runtime --sign "Proctora Dev" "$GUARD"
  codesign --force --options runtime --sign "Proctora Dev" "$BUNDLE"
else
  echo "==> Ad-hoc signing (Accessibility will be asked again after each rebuild)..."
  codesign --force --options runtime --sign - "$GUARD"
  codesign --force --options runtime --sign - "$BUNDLE"
fi
[ "$MODE" = release ] && [ -z "${FAIRPLAY_SIGN_ID:-}" ] && [ -z "${FAIRPLAY_SIGN_SELF:-}" ] && \
  echo "WARNING: release build without Developer ID signing/notarization (FAIRPLAY_SIGN_ID)."
echo "==> Done: $BUNDLE ($MODE)"
if [ -n "$DEBUG_LOG" ]; then echo "    Debug trace: $DEBUG_LOG"; fi
