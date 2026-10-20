#!/bin/bash
# Double-click: builds the iPad app (dev) and runs it in an iPad simulator against the
# local server (start "Run Server.command" first).
set -e
cd "$(dirname "$0")"
NAME="FairPlay iPad Pro 13"
UDID=$(xcrun simctl list devices available | grep "$NAME (" | head -1 | grep -oE '[0-9A-F-]{36}' || true)
if [ -z "$UDID" ]; then
  RUNTIME=$(xcrun simctl list runtimes | grep -oE 'com.apple.CoreSimulator.SimRuntime.iOS-[0-9-]+' | tail -1)
  [ -n "$RUNTIME" ] || { echo "No iOS simulator runtime. Install one in Xcode > Settings > Components."; read -n1; exit 1; }
  TYPE=$(xcrun simctl list devicetypes | grep -oE 'com.apple.CoreSimulator.SimDeviceType.iPad-Pro-13-inch[^)]*' | head -1)
  UDID=$(xcrun simctl create "$NAME" "$TYPE" "$RUNTIME")
fi
xcrun simctl boot "$UDID" 2>/dev/null || true
open -a Simulator
echo "==> Building FairPlay for iPad..."
xcodebuild -project FairPlay.xcodeproj -scheme FairPlay -configuration Debug \
  -destination "id=$UDID" -derivedDataPath .build/xcode build -quiet
xcrun simctl install "$UDID" .build/xcode/Build/Products/Debug-iphonesimulator/FairPlay.app
xcrun simctl launch "$UDID" ai.proctora.student.ipad "$@"
echo "==> Running. Sign in alice / alice123. Invigilator exit: hold the lock bar 3 s (dev code 0000)."
