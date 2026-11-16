# Install FairPlay on a Mac (students)

FairPlay is not signed with an Apple Developer ID, so macOS asks once before it opens the app.
This is expected and safe if you downloaded FairPlay from the course page or the official download page.

1. Download **FairPlay-Mac.dmg**, open it and drag **FairPlay** onto **Applications**.
   Keep **one** copy, in `/Applications`. Delete older copies (Downloads, Desktop, the disk image): macOS
   can mix them up and the Accessibility permission is then given to the wrong one.
2. Open FairPlay from Applications. If macOS says it "cannot be opened" or "cannot be verified", click
   **Done** (not "Move to Trash").
3. Open **System Settings > Privacy & Security**, scroll down, click **Open Anyway** next to FairPlay, then
   **Open Anyway** again and type your Mac password.
4. If there is no **Open Anyway** button, open **Terminal** and run exactly this, then open FairPlay again:

   ```
   xattr -dr com.apple.quarantine /Applications/FairPlay.app
   ```
5. FairPlay asks for **Accessibility**: click **Fix permission**, then **Open System Settings** and turn **FairPlay**
   on. Then click **Restart FairPlay** (the permission only counts after a restart of the app).

## Updates

FairPlay updates itself: when a newer version exists, the home screen shows **Update now** (a Mac below the
minimum version for an exam gets the update screen with the same button). The app downloads the new version,
checks its SHA-256 and its signature, replaces itself in Applications and opens again. You do not have to do
steps 2 to 4 again, and the Accessibility permission stays on, because every release is signed with the same
certificate.

Why there is no second Gatekeeper prompt: the "unidentified developer" question is only asked for files that
carry the `com.apple.quarantine` mark, which a web browser puts on a download. The update is downloaded by
FairPlay itself (not a browser), nothing in FairPlay sets that mark, and unpacking with `ditto` keeps whatever
the zip file had (none). You can check it yourself after an update:

```
xattr -l /Applications/FairPlay.app
```

It must not list `com.apple.quarantine`. (The app you first downloaded in a browser does list it until step 3 or 4.)

FairPlay never updates while an exam lock is on or during "Test my device".

## If you have to install by hand again

Download the new **FairPlay-Mac.dmg** and drag it over the old one in Applications (replace). Steps 2 to 4 are
needed again only if the Mac asks. If Accessibility shows FairPlay as on but the app says it is not allowed:
turn it off and on again, then **Restart FairPlay**.

## For the person who builds releases

Sign every release with the same self-signed identity (`FAIRPLAY_SIGN_SELF`, see RELEASES.md and
`mac/make-signing-cert.sh`). Changing the certificate, or going back to ad-hoc signing, makes macOS forget the
Accessibility permission and makes the in-app updater refuse the new copy: everybody has to install by hand once.
