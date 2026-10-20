#!/bin/bash
# Writes ios/FairPlay/FairPlayBuild.plist (server, app key, TLS pins) for a RELEASE build
# of the iPad app; Config.swift reads it from the signed bundle. Then archive in Xcode
# (Product > Archive, Release) or:
#   xcodebuild -project FairPlay.xcodeproj -scheme FairPlay -configuration Release archive
#
#   FAIRPLAY_SERVER=https://exam.example.edu \
#   FAIRPLAY_APP_KEY=<same value as the server's FAIRPLAY_APP_KEY> \
#   FAIRPLAY_PINS=<base64 sha256 of CA certs, comma-separated; see mac/pin.sh> \
#   bash ios/configure-release.sh
#
# Never commit the generated plist (it is in .gitignore). Delete it to go back to the dev
# defaults (BuildConfig in Config.swift).
set -euo pipefail
cd "$(dirname "$0")"

DEV_KEY="fairplay-dev-app-key-not-secret"
SERVER="${FAIRPLAY_SERVER:?set FAIRPLAY_SERVER=https://your-exam-server}"
KEY="${FAIRPLAY_APP_KEY:?set FAIRPLAY_APP_KEY (same as the server)}"
PINS="${FAIRPLAY_PINS:?release builds need FAIRPLAY_PINS (run: bash ../mac/pin.sh your-exam-server[:port])}"

[[ "$SERVER" =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9._~/-]*)?$ ]] || { echo "FAIRPLAY_SERVER must be https: $SERVER"; exit 1; }
[[ "$KEY" != "$DEV_KEY" && ${#KEY} -ge 32 ]] || { echo "FAIRPLAY_APP_KEY must be a private value of 32+ characters"; exit 1; }

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
echo "Wrote $OUT for $SERVER (${#P[@]} pin(s)). Archive the Release configuration now."
