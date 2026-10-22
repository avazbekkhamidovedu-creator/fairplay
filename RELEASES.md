## Download FairPlay

| Your computer | File |
|---|---|
| Mac (Apple Silicon or Intel, macOS 13 or newer) | **FairPlay-Mac.zip** |
| Windows 10/11 (64-bit) | **FairPlay-Windows-Setup.exe** (or FairPlay-Windows.zip without installing) |
| iPad (iPadOS 17.5 or newer), **test build** | **FairPlay-iPad.ipa** (install with a Mac or PC, see below) |

The newest version is always at https://github.com/khamidov17/fairplay/releases/latest

### Install on a Mac
1. Download **FairPlay-Mac.zip** and double-click it. Move **FairPlay** into **Applications**.
2. Open FairPlay. macOS says it "cannot be opened" or "cannot be verified": click **Done**.
   (The app is not signed with an Apple Developer ID yet.)
3. Open **System Settings > Privacy & Security**, scroll down and click **Open Anyway** next to
   FairPlay, then **Open Anyway** again and enter your Mac password.
4. FairPlay asks for **Accessibility**: click **Open settings** and turn **FairPlay** on.
   After each FairPlay update this switch must be turned off and on again.

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
- In FairPlay, sign in with the username and password from your teacher.
- Use **Check this Mac / Check this PC** any day before the exam and fix anything red.
- Exam server: https://exam.speakupai.uz
