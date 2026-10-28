#!/bin/bash
# Writes ios/FairPlay/FairPlayBuild.plist (server, app key, TLS pins) for a RELEASE build
# of the iPad app; Config.swift reads it from the signed bundle. Then archive in Xcode
# (Product > Archive, Release) or:
#   xcodebuild -project FairPlay.xcodeproj -scheme FairPlay -configuration Release archive
#
#   FAIRPLAY_SERVER=https://exam.example.edu \
#   FAIRPLAY_APP_KEY=<same value as the server's FAIRPLAY_APP_KEY> \
#   FAIRPLAY_PINS=<base64 sha256 of CA certs, comma-separated; see mac/pin.sh> \
#   FAIRPLAY_EXTRA_ROOT_PEM=<path to a PEM file: the private CA of the exam server> (optional)
#   bash ios/configure-release.sh
#
# FAIRPLAY_WATCHED_LOCK=1 makes a TEST build (the sideloaded .ipa on the GitHub release,
# or TestFlight before Apple grants the Assessment Mode entitlement): without Assessment
# Mode it locks in "watched" mode, which the server and the live room count as NOT locked.
#
# Never commit the generated plist (it is in .gitignore). Delete it to go back to the dev
# defaults (BuildConfig in Config.swift).
set -euo pipefail
EXTRA_ROOT_FILE="${FAIRPLAY_EXTRA_ROOT_PEM:-}"
[[ -z "$EXTRA_ROOT_FILE" || "$EXTRA_ROOT_FILE" == /* ]] || EXTRA_ROOT_FILE="$PWD/$EXTRA_ROOT_FILE"
cd "$(dirname "$0")"

DEV_KEY="fairplay-dev-app-key-not-secret"
SERVER="${FAIRPLAY_SERVER:?set FAIRPLAY_SERVER=https://your-exam-server}"
KEY="${FAIRPLAY_APP_KEY:?set FAIRPLAY_APP_KEY (same as the server)}"
PINS="${FAIRPLAY_PINS:?release builds need FAIRPLAY_PINS (run: bash ../mac/pin.sh your-exam-server[:port])}"

[[ "$SERVER" =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9._~/-]*)?$ ]] || { echo "FAIRPLAY_SERVER must be https: $SERVER"; exit 1; }
[[ "$KEY" != "$DEV_KEY" && ${#KEY} -ge 32 ]] || { echo "FAIRPLAY_APP_KEY must be a private value of 32+ characters"; exit 1; }

EXTRA_ROOT=""
if [ -n "$EXTRA_ROOT_FILE" ]; then
  [ -f "$EXTRA_ROOT_FILE" ] || { echo "FAIRPLAY_EXTRA_ROOT_PEM: no such file: $FAIRPLAY_EXTRA_ROOT_PEM"; exit 1; }
  openssl x509 -in "$EXTRA_ROOT_FILE" -noout 2>/dev/null || { echo "FAIRPLAY_EXTRA_ROOT_PEM is not a PEM certificate"; exit 1; }
  EXTRA_ROOT="$(openssl x509 -in "$EXTRA_ROOT_FILE" | grep -v -- '-----' | tr -d '\n')"
fi

OUT=FairPlay/FairPlayBuild.plist
rm -f "$OUT"
/usr/libexec/PlistBuddy -c "Add :ServerURL string $SERVER" "$OUT" >/dev/null
/usr/libexec/PlistBuddy -c "Add :AppKey string $KEY" "$OUT" >/dev/null
/usr/libexec/PlistBuddy -c "Add :Pins array" "$OUT" >/dev/null
IFS=',' read -ra P <<< "$PINS"
for pin in "${P[@]}"; do
  [[ "$pin" =~ ^[A-Za-z0-9+/=]{44}$ ]] || { echo "bad pin: $pin"; rm -f "$OUT"; exit 1; }
  /usr/libexec/PlistBuddy -c "Add :Pins: string $pin" "$OUT" >/dev/null
done
if [ -n "$EXTRA_ROOT" ]; then
  /usr/libexec/PlistBuddy -c "Add :ExtraRootPEM string $EXTRA_ROOT" "$OUT" >/dev/null
fi
if [ "${FAIRPLAY_WATCHED_LOCK:-0}" = 1 ]; then
  /usr/libexec/PlistBuddy -c "Add :WatchedLock bool true" "$OUT" >/dev/null
  echo "Test build: watched lock allowed without Assessment Mode (counts as NOT locked)."
fi
echo "Wrote $OUT for $SERVER (${#P[@]} pin(s)). Archive the Release configuration now."
