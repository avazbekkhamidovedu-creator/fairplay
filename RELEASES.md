## Download FairPlay

| Your computer | File |
|---|---|
| Mac (Apple Silicon or Intel, macOS 13 or newer) | **FairPlay-Mac.zip** |
| Windows 10/11 (64-bit) | **FairPlay-Windows-Setup.exe** (or FairPlay-Windows.zip without installing) |

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

### Before the exam
- In FairPlay, sign in with the username and password from your teacher.
- Use **Check this Mac / Check this PC** any day before the exam and fix anything red.
- Exam server: https://exam.speakupai.uz
