# FairPlay for iPad

The student exam app for iPad. Same server, same web pages and the same native bridge
as the Mac and Windows apps (CONTRACT.md "Native bridge" + "App session"), so teachers
see iPads in the live room like any other computer.

```
FairPlay/            the app (UIKit + WKWebView)
  ExamViewController   web view, bridge, navigation rules, lock bar, invigilator exit
  AppSession           signed start proof, signed beats, offline exit key, signed hand-in
  ExternalExamController  website exams (SEB-style): allowlist, countdown, Finish exam
  Lockdown/            AssessmentMode, LockdownController, IntegrityChecks, CaptureShield
FairPlayCore/        pure logic as a Swift package (`swift test` on the Mac)
Config/              Info.plists (dev has ATS exceptions, release none) + entitlements
configure-release.sh writes the release server / app key / pins into the bundle
```

## Try it in the simulator

1. Start the server: double-click **Run Server.command** in the repo root.
2. Double-click **ios/Run in Simulator.command** (or open `FairPlay.xcodeproj` and Run on an iPad simulator).
3. Sign in `alice / alice123`, enter the start code from the teacher's exam.
4. Invigilator exit while locked: **press and hold the lock bar at the top for 3 s** (or Ctrl-Opt-Cmd-E
   on a hardware keyboard), code from the live room. Dev builds also accept `0000`.

## Run it on your own iPad (free Apple ID is enough)

1. Cable the iPad to the Mac, unlock it, tap **Trust This Computer**.
2. iPad: **Settings > Privacy & Security > Developer Mode** → on (it restarts).
3. Mac: **Xcode > Settings > Accounts** → add your Apple ID. Open `ios/FairPlay.xcodeproj`,
   target FairPlay > **Signing & Capabilities** → Team: your name (Personal Team). If Xcode says
   the bundle id is taken, change `ai.proctora.student.ipad` to something of your own.
4. Server reachable from the iPad: double-click **Run Server for iPad.command** (listens on the
   Wi-Fi and prints the address). Scheme > Run > Arguments: `-ServerURL http://<the Mac's Wi-Fi IP>:8000`
   (the app remembers it for home-screen launches; **Change server** on the connecting /
   offline screen edits or forgets it without Xcode). University Wi-Fi often blocks
   device-to-device traffic: then use a phone hotspot for both. Also check iPad **Settings >
   Privacy & Security > Local Network > FairPlay** is on, or the iPad can't reach the Mac.
5. Pick your iPad as the run destination and press Run. First launch: iPad **Settings > General >
   VPN & Device Management** → trust your developer certificate.

Debug builds on a device sign **without** the Assessment Mode / App Attest entitlements (a
Personal Team can't have them), so the app runs the dev "watched" lock there. The full lock
needs Apple's Assessment Mode entitlement on a paid team (see Release checklist). Free-team
installs expire after 7 days; reinstall from Xcode. Students get the app through TestFlight or
the App Store / Apple School Manager (paid Apple Developer Program).

Dev-build launch arguments (scheme > Run > Arguments, or `xcrun simctl launch … -Key value`):

| argument | default | meaning |
|---|---|---|
| `-ServerURL http://192.168.1.20:8000` | `http://127.0.0.1:8000` | exam server (a real iPad needs the Mac's LAN address; start the server with `HOST=0.0.0.0`) |
| `-CaptureShield NO` | `YES` | lets simulator screenshots show the exam (the shield blanks them, on purpose) |
| `-Assessment YES` | `NO` in the simulator | try Apple Assessment Mode in the simulator (it shows the consent prompt but can never finish there: its agent can't write its policy store) |
| `-SoftLock NO` | `YES` | refuse to lock without Assessment / Single App Mode, like a release build |
| `-DevExitCode 1234` | `0000` | fixed invigilator code (dev builds only) |
| `-AllowCapture YES` | `NO` | watch a test through QuickTime (cable) or AirPlay: recording / mirroring no longer puts up the red screen while locked (still reported, `cap=1`) |
| `-DevForceCapture YES` | `NO` | pretend the screen is recorded, to test the red screen (the simulator can't be recorded from inside iPadOS) |
| `-ShieldSelfTest YES` | `NO` | 3 s after locking, render the window the way iPadOS snapshots it and log `HIDDEN` / `VISIBLE` |

Dev builds never use a release key or pins (even if `FairPlayBuild.plist` exists), so they
can't talk to a production server at all; servers without `FAIRPLAY_DEV=1` also refuse starts
that say `build: "dev"` (that field isn't signed, so the key is the real protection).
`-ShieldForceRetry YES` skips the shield's first attach to test its window retry.

Tests: `cd ios/FairPlayCore && swift test` (signing formats and offline-exit vectors match
the server's own tests).

## Test build for anyone (GitHub release, no Mac needed for the app itself)

`.github/workflows/ipad.yml` builds the **Release** configuration pointed at
https://exam.speakupai.uz on every push that touches `ios/`, **unsigned**, with
`WatchedLock` in `FairPlayBuild.plist`, and attaches it to the current release as
**FairPlay-iPad.ipa**. Testers sign it with their own Apple ID (Sideloadly / AltStore, steps
in `RELEASES.md`). Such a copy can't have Assessment Mode (no entitlement), so it locks in
**watched** mode: the beat says `aac=0,sam=0,soft=1`, the server counts it as **not locked**
(`ipad_lock_held`), the lock bar says "Watched (test build, no Assessment Mode)". All other
release rules stay: https + pins + the real app key, jailbreak / debugger / recording checks,
signed hand-in, no `0000`. A release build without `WatchedLock` still refuses to lock
without Assessment Mode or Single App Mode.

## TestFlight (paid Apple Developer Program, not set up yet)

The `testflight` job in `ipad.yml` is ready but switched off. To use it:
1. Join the Apple Developer Program. In App Store Connect create the app with bundle id
   `ai.proctora.student.ipad` (Certificates, IDs & Profiles: enable App Attest for it).
2. App Store Connect > Users and Access > Integrations > App Store Connect API: create a key
   with the **Admin** role (it lets the runner create the distribution certificate).
3. GitHub > Settings > Secrets and variables > Actions:
   secrets `ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_KEY_P8` (the .p8 file's text), `APPLE_TEAM_ID`;
   variable `IPAD_TESTFLIGHT` = `true`.
4. Push anything under `ios/`: the build appears in TestFlight. Add testers there (a public
   link works for up to 10,000 people).
5. When Apple grants the Assessment Mode entitlement (Release checklist 1), set the variable
   `IPAD_ASSESSMENT_APPROVED` = `true`: TestFlight builds then use `Config/FairPlay.entitlements`
   and lock for real. Before that they are watched test builds, like the .ipa.

This job has not run yet (no account); expect to fix small signing details on its first run.

## How the lock works (strongest first)

1. **Apple Assessment Mode** (Automatic Assessment Configuration, the system mode Bluebook uses).
   iPadOS pins FairPlay: no Home, app switcher, Control Center, Spotlight, Siri, notifications,
   screenshots, screen recording, AirPlay or Handoff. FairPlay also turns off autocorrect,
   spell check, predictive text, keyboard shortcuts, dictation, Speak Selection, Password
   AutoFill, slide-to-type, typing feedback and the emoji keyboard. Teacher-allowed apps
   (e.g. Calculator) become Assessment Mode participants **without network access**.
2. **Single App Mode** on supervised school iPads (MDM Autonomous Single App Mode payload for
   `ai.proctora.student.ipad`). Used when Assessment Mode is unavailable, and it counts only
   when FairPlay turned it on itself: Guided Access that was already on (someone holds its
   passcode) is reported as `ga=1` and never counts as a lock. **Release builds refuse to lock
   without 1 or 2**, and the server treats an iPad beat without `aac=1` or `sam=1` as not locked.
3. **Capture shield**: the exam is drawn in the secure layer iPadOS uses for password fields,
   so screenshots, recordings, mirroring and the app-switcher snapshot show a blank screen,
   even if 1 is interrupted (checked with `-ShieldSelfTest`: blank in iPadOS snapshots on 26.5).
   External displays get a black scene. The beat says `shield=1/0`; release builds block the
   exam when neither the shield nor Assessment Mode protects it.
4. **Checks every 3 s and on every system notification**, reported in the signed beat
   (`plat=ipad,aac=,sam=,ga=,soft=,shield=,disp=,cap=,mw=,fg=,left=,shot=,jb=,dbg=,kbd=,vpn=,proxy=,att=,console=`):
   recording / mirroring / extra display, Split View / Slide Over / Stage Manager, leaving the
   app (count), screenshots (count), jailbreak signs, debugger, hardware keyboard, VPN, proxy.
   Problems put a red screen over the exam and reach the teacher at once; leaving the app
   or capturing sends `console=0`, which the server treats as **not locked** (switching to a
   teacher-allowed app under Assessment Mode is not "leaving").
5. **Input and content**: third-party keyboards are refused for the whole app, the clipboard is
   emptied, the edit menu has no Copy / Paste / Look Up / Translate / Share / Writing Tools,
   no link previews, no Safari Web Inspector in release, no Mac "Designed for iPad" or
   visionOS builds, the iPad doesn't auto-lock mid-exam.
6. **Server-side truth**: only the app's HMAC-signed beats say "locked"; unlock needs the
   server's signed hand-in; the invigilator code is checked by the server (signed reply); the
   offline exit key is per attempt + session, memory-only, and accepted only with the
   server's signature over it; the device id lives in the
   Keychain (ThisDeviceOnly, so copies of the app don't move the device). If FairPlay dies
   while locked, the next launch reports it and goes straight back into that exam.

Compared with Bluebook on iPad (Assessment Mode + its own checks), FairPlay adds the capture
shield, Single App Mode fallback, fail-closed resume with a red screen when iPadOS drops
Assessment Mode, signed live state for the invigilator, jailbreak / debugger / multitasking /
capture checks with teacher alerts, server-checked invigilator and offline exits, and
teacher-allowed apps without network.

Known trade-off: an iPad on a full-time MDM **App Lock** (non-autonomous Single App Mode) looks
exactly like Guided Access a student started, so when Assessment Mode can't start it is
refused rather than trusted. Use Assessment Mode (entitlement) or the Autonomous Single App
Mode payload.

## Works with S1–S6 (2026-10-20)

Device check rows in preflight, version gate (`X-FairPlay-Platform: ipad`; set
`MIN_APP_VERSION_IPAD` on the server), sealed offline hand-in at time-up ("Submit later from
this iPad"), and `sep=1` in beats: Assessment Mode is the strongest lock S6 asks for, so iPads
pass "Require the strongest lock". See CONTRACT.md "2026-10-20 iPad app".

## Release checklist

1. **Request the Automatic Assessment Configuration entitlement** from Apple for
   `ai.proctora.student.ipad` (developer.apple.com/contact/request/automatic-assessment-configuration).
   Without it, Assessment Mode can't start on a real iPad and release builds refuse to lock
   (except on supervised iPads with Single App Mode).
2. Enable App Attest for the App ID (entitlement already in `Config/FairPlay.entitlements`).
3. `FAIRPLAY_SERVER=https://… FAIRPLAY_APP_KEY=… FAIRPLAY_PINS=… bash ios/configure-release.sh`
4. Archive the **Release** configuration with your team's signing; distribute via the App Store,
   Apple School Manager / MDM (recommended: also push the Single App Mode payload).

## Not done yet

- **App Attest is not built yet, in the app or on the server.** The app only reports whether
  the device supports it (`att=0` / `att=?`); nothing is attested and the server checks
  nothing. Both halves (attestKey / assertions in the app, verification against Apple's
  root on the server) need the App Attest entitlement, so they come with the paid account.
  Until then an extracted app key is as much of a risk on iPad as on Mac and Windows (CONTRACT M-20).
- Assessment Mode can only be tested end to end on a real iPad with the entitlement.
- Security review and fixes: see `REVIEW-2026-10-20.md`.
