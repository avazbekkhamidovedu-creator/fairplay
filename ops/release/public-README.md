# FairPlay

Secure exam app for universities. This repository hosts the app downloads.

## What's new in 0.8.7

- "Test my device" covers the whole screen on every monitor, never asks you to close other apps, and tells you clearly (and returns to the home screen) if it cannot cover the screen.
- The old 6-digit exam code is gone: FairPlay always opens on its home screen.
- When an instructor unlocks a student's computer, the app returns to its home screen on its next check (about 5 seconds). The check now also recovers if a security prompt appeared or the computer restarted unexpectedly.
- Windows: a log of the app's connection checks is kept on the computer so a problem can be diagnosed quickly.

## What's new in 0.8.6

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
