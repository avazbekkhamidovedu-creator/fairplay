# FairPlay

## Download (students)

| Computer | Download |
|---|---|
| Mac (Apple Silicon or Intel) | [FairPlay-Mac.dmg](https://github.com/avazbekkhamidovedu-creator/fairplay-download/releases/latest/download/FairPlay-Mac.dmg) |
| Windows 10/11 | [FairPlay-Windows-Setup.exe](https://github.com/avazbekkhamidovedu-creator/fairplay-download/releases/latest/download/FairPlay-Windows-Setup.exe) |
| iPad (test build) | [FairPlay-iPad.ipa](https://github.com/avazbekkhamidovedu-creator/fairplay-download/releases/latest/download/FairPlay-iPad.ipa) (install with Sideloadly / AltStore, see RELEASES.md) |

Install steps (the apps are not signed yet, so macOS and Windows ask once): [RELEASES.md](RELEASES.md).
Releases are built by `.github/workflows/release-mac.yml`, `.github/workflows/ipad.yml` (and the Windows repo's `release.yml`)
when a `vX.Y.Z` tag is pushed; both need the repository secret `FAIRPLAY_APP_KEY`.


Secure exams for universities: teacher web dashboard, student apps that lock the device (Mac, iPad; Windows in fairplay-windows), and a scalable API.

```
server/   FastAPI API + serves the built web UI (teacher and student)
web/      React + TypeScript source of the web UI (npm run build -> server/static)
mac/      FairPlay.app: student exam app (WKWebView + lockdown)
ios/      FairPlay for iPad: student exam app (Apple Assessment Mode + lockdown), see ios/README.md
e2e/      Playwright end-to-end test (teacher creates, 2 students take, results)
CONTRACT.md  API and native-bridge contract all parts follow
```

## Try it on this Mac
1. Double-click **Run Server.command** (first run installs Python packages, about a minute). Leave it open.
2. Teacher: open http://127.0.0.1:8000/teacher in a browser, sign in `teacher / teacher123`, create an exam, press Open, note the 4-digit start code.
3. Student: double-click **mac/Build and Run.command**. Sign in `alice / alice123`, enter the code, Start exam. The Mac locks until you submit.
   - First time only: turn on "FairPlay" in Privacy & Security > Accessibility (the app shows the steps).
   - Invigilator emergency exit while locked: **Ctrl-Opt-Cmd-E**, code **1234**. Dev builds also unlock by themselves after 180 minutes.
4. Watch the teacher's Live room, then Results.

## Try it on an iPad (simulator)
1. Server running as above.
2. Double-click **ios/Run in Simulator.command** (needs Xcode with an iOS simulator). Sign in `alice / alice123`, enter the code.
   - Invigilator exit while locked: press and hold the lock bar at the top for 3 s, code from the live room (dev builds also take `0000`).
   - Real iPads need Apple's Assessment Mode entitlement for the full lock; see ios/README.md.

Other students on the same Wi-Fi: start the server with `HOST=0.0.0.0` and point their app at it:
`defaults write ai.proctora.student ServerURL http://<this-mac-ip>:8000`
