## What's new in 0.8.9

- Duke Box now opens during open-book exams: FairPlay allows Box's last sign-in step (the page Box sends you back to after Duke sign-in), which was being blocked, so the sign-in could never finish. Only that one step is added; the rest of Box and your personal Box pages stay blocked.

## What's new in 0.8.8

- A wrong date or time on your computer no longer stops FairPlay or an exam: FairPlay uses the exam server's time.
- Updates come from the exam server first, so they work even when GitHub is slow or blocked. If a newer version is required, FairPlay shows a download link and an Update button.
- When an instructor unlocks a computer, FairPlay now recovers in more cases: after the attempt was deleted, and when the computer's clock is wrong. Blocked screens say clearly what they can and cannot do.
- Instructors: giving extra time, unlocking a device and getting an offline exit code no longer ask for a reason. The extra-time box shows the student's current total time and can be edited.
- Open-book exams start with DKU OJ, Duke sign-in and Duke Box allowed, and the Box sign-in works.
- Instructors see why a student is blocked (virtual machine, remote session, weaker lock, blocked screen, restart).
- Windows: connection, lock and update problems are saved in fairplay-diag.log for support; the installer includes WebView2.

## What's new in 0.8.7

- "Test my device" covers the whole screen on every monitor, never asks you to close other apps, and tells you clearly (and returns to the home screen) if it cannot cover the screen.
- The old 6-digit exam code is gone: FairPlay always opens on its home screen.
- When an instructor unlocks a student's computer, the app returns to its home screen on its next check (about 5 seconds). The check now also recovers if a security prompt appeared or the computer restarted unexpectedly.
- Windows: a log of the app's connection checks is kept on the computer so a problem can be diagnosed quickly.

## What's new in 0.8.6

- Turning the computer off and on during an exam no longer reopens FairPlay in locked mode: FairPlay opens on its home screen, and Wi-Fi, the university VPN and the usual start-up steps work. Quitting or a crash without a restart still locks again.
- The red "Emergency exit" button (top right) accepts the instructor's 6-digit offline exit code, with no internet needed. Instructors get the code from the Participation page in DKU OJ.
- Open-book exams: the allowed websites open automatically in their own tabs when the exam starts, Duke Box sign-in (Duke login and Duo) works inside the Box tab, and tabs look like Safari's.
- Windows: a two-finger touchpad swipe no longer leaves the exam page, FairPlay starts much faster, the device test covers the whole screen and Leave returns to the home screen, and dialogs have a cleaner design.

## What's new in 0.8.5

- Instructors can unlock a student with one button and one confirmation on the DKU OJ help page, with no codes or typing. The student's computer is released within about 25 seconds and the app returns to its first screen.
- DKU OJ pages are always shown exactly as DKU OJ sends them: no false "could not be loaded" notice and no VPN prompt. The app works the same with the university VPN on or off, and reloads once when the VPN connects or drops.
- The device certificate now renews by itself before it expires, so a long exam or one started near expiry keeps its connection.
- Math and icons from the OJ's own CDN files render in locked exams, and undo, redo, select all and find work in the code editor on the Mac.
- When the exam time is over, FairPlay unlocks the computer by itself using each student's own deadline, collects the answers in the background, and warns the instructor if a saved answer could not be submitted.
- Fewer start problems: a leftover session, signing in again on the same computer, or a refused exam no longer needs the instructor to step in, and the Windows toolbar matches the Mac.
- Open-book exams (allowed websites and a Duke Box folder) open in separate tabs only, with the same rules on Mac, Windows and the server. Nothing changes unless the instructor lists sites.
- The server handles a whole class registering at once, with 30-day certificates and campus-network limits.

## Download FairPlay

| Your computer | Download |
|---|---|
| Mac (Apple Silicon or Intel, macOS 13 or newer) | [**FairPlay-Mac.dmg**](https://github.com/avazbekkhamidovedu-creator/fairplay-download/releases/latest/download/FairPlay-Mac.dmg) (or [FairPlay-Mac.zip](https://github.com/avazbekkhamidovedu-creator/fairplay-download/releases/latest/download/FairPlay-Mac.zip)) |
| Windows 10/11 (64-bit) | [**FairPlay-Windows-Setup.exe**](https://github.com/avazbekkhamidovedu-creator/fairplay-download/releases/latest/download/FairPlay-Windows-Setup.exe) (or [FairPlay-Windows.zip](https://github.com/avazbekkhamidovedu-creator/fairplay-download/releases/latest/download/FairPlay-Windows.zip) without installing) |
| iPad (iPadOS 17.5 or newer), **test build 0.7.9** | [**FairPlay-iPad.ipa**](https://github.com/avazbekkhamidovedu-creator/fairplay-download/releases/download/v0.7.9/FairPlay-iPad.ipa) (install with a Mac or PC, see below) |

The newest version is always at https://github.com/avazbekkhamidovedu-creator/fairplay-download/releases/latest

### Install on a Mac
1. Download **FairPlay-Mac.dmg** and open it. Drag **FairPlay** onto **Applications**.
2. Open FairPlay from Applications. If macOS says it "cannot be opened" or "cannot be verified":
   click **Done** (not "Move to Trash").
   (The app is not signed with an Apple Developer ID yet.)
3. Open **System Settings > Privacy & Security**, scroll down and click **Open Anyway** next to
   FairPlay, then **Open Anyway** again and enter your Mac password.
4. FairPlay asks for **Accessibility**: click **Fix permission**, then **Open System Settings**,
   and turn **FairPlay** on. If the screen stays, click **Restart FairPlay**.
   Keep only one copy of FairPlay (in Applications); delete older downloads, because macOS can
   mix them up.

If step 3 shows no button, open Terminal and run:
`xattr -dr com.apple.quarantine /Applications/FairPlay.app`

### Install on Windows
1. Download **FairPlay-Windows-Setup.exe** and run it.
2. If Windows says "Windows protected your PC": click **More info > Run anyway**
   (the app is not code-signed yet).
3. FairPlay starts and is in the Start menu from then on.

### Install on an iPad (test build)
Apple doesn't let iPad apps be installed from a download link. Until FairPlay is on
TestFlight, you sign this test build with your own (free) Apple ID from a Mac or PC:
1. On the Mac or PC, install **Sideloadly** (sideloadly.io; on Windows also iTunes from Apple's
   website, not the Microsoft Store) or **AltStore** (altstore.io).
2. Download **FairPlay-iPad.ipa**.
3. Connect the iPad with a cable, unlock it and tap **Trust**.
4. Drop the .ipa into Sideloadly, enter your Apple ID and click **Start**.
5. On the iPad: **Settings > Privacy & Security > Developer Mode** → on (it restarts), then
   **Settings > General > VPN & Device Management** → your Apple ID → **Trust**.
6. Open FairPlay and sign in.

Good to know:
- A free Apple ID signs apps for **7 days**: install it again after that (your sign-in stays).
- This test build has no Apple **Assessment Mode** (Apple grants that only to the paid
  developer account). It locks in **"watched" mode**: FairPlay still checks recording,
  mirroring, Split View, leaving the app and more and reports it, but the teacher's live
  room shows the iPad as **not locked**. Fine for trying FairPlay, not for a real exam.
- FairPlay only allows Apple's own keyboards (third-party keyboards are switched off).

### Before the exam
- On DKU OJ, open the exam and use **Start exam in FairPlay** after installing this version. If the browser does not open the app, open FairPlay yourself and sign in. There is no exam access code to type.
- Use **Check this Mac / Check this PC** any day before the exam and fix anything red.
- Exam server: https://dku.speakupai.uz


## Signing without an Apple Developer ID (Mac)

No Developer ID means no notarization: Gatekeeper asks once at the first install ("Open Anyway", or
`xattr -dr com.apple.quarantine /Applications/FairPlay.app`; student guide: docs/STUDENT-INSTALL-MAC.md).
What must stay the same is the signing identity:

- Make one self-signed code-signing certificate once with `mac/make-signing-cert.sh "FairPlay Code Signing"`.
  Store the `.p12` and its password in the CI secrets `MAC_SIGN_P12` (base64) and `MAC_SIGN_PASSWORD`
  (`.github/workflows/release-mac.yml` imports it and sets `FAIRPLAY_SIGN_SELF=FairPlay Code Signing`).
  Locally: import the `.p12` into the login keychain and `FAIRPLAY_SIGN_SELF="FairPlay Code Signing" ./build.sh release`.
- `mac/build.sh` then prints the designated requirement and stops if it does not name the certificate
  (`certificate leaf = H"..."`). With that requirement macOS keeps the Accessibility permission across
  versions and the in-app updater (`sameSigner`) accepts the next release. Ad-hoc builds have a requirement
  that changes every build: the permission is asked again and nothing is checked on update.
- Never replace the certificate between releases. If it is lost or changed, every student reinstalls by hand once
  (and grants Accessibility again). Switching a fleet from ad-hoc to the fixed identity costs that one reinstall.
- The in-app update does not trigger Gatekeeper again: it is downloaded by the app itself, which does not set the
  `com.apple.quarantine` attribute (no `LSFileQuarantineEnabled`, no `setxattr` on update files; the only
  quarantine call in the app is for files the exam browser downloads).
- Update source: the apps ask the exam server (`GET /api/app/update`, docs/APP-UPDATE-CONTRACT.md) and GitHub only
  when it does not answer. After publishing a release, the server picks it up within 10 minutes, or pin it by hand
  with `FAIRPLAY_LATEST_APP_VERSION_MAC`, `FAIRPLAY_APP_UPDATE_URL_MAC` and `FAIRPLAY_APP_UPDATE_SHA256_MAC`.
