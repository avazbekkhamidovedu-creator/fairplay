# Test the iPad app against the local FairPlay + DKU OJ servers on your Mac

Everything runs on the Mac: DKU OJ in Docker (`http://127.0.0.1:8100`) and the FairPlay server
(`http://127.0.0.1:8000`, synced with DKU OJ every 15 s). The iPad app comes from your git
checkout of this repo (`ios/`); the `dkuoj-local` kit has no `ios/` folder.

| where the app runs | server address the app uses | server must listen on |
|---|---|---|
| iOS Simulator on the Mac | `http://127.0.0.1:8000` (the simulator shares the Mac's network) | `127.0.0.1` is enough |
| your real iPad | `http://<the Mac's Wi-Fi IP>:8000` | `0.0.0.0` (all interfaces) |

Listening on `0.0.0.0` changes nothing for the Mac itself: the teacher pages at
`http://127.0.0.1:8000/teacher`, the Mac app, and DKU OJ in Docker
(`host.docker.internal:8000`) keep working. Only do it on a network you trust (home Wi-Fi or a
phone hotspot): it is a dev server with demo accounts.

## 1. Start the servers

1. Start DKU OJ as usual (`1 Start DKU OJ.command`).
2. Simulator only: start FairPlay as usual (`2 Start FairPlay server.command` or
   `Restart all (bridge v2).command`). Go to step 2.
3. Real iPad: FairPlay must listen on all interfaces. Stop the running FairPlay server first
   (Ctrl-C in its window, otherwise port 8000 is taken). Then, in Terminal:

   ```bash
   cd ~/code/fairplay-classroom/dkuoj-local/fairplay/server
   export FAIRPLAY_DEV=1
   export DKUOJ_URL=http://127.0.0.1:8100
   export DKUOJ_TOKEN="$(cat ../../kit/token.txt)"
   export DKUOJ_SYNC_SECONDS=15
   export FAIRPLAY_PUBLIC_URL=http://127.0.0.1:8000
   echo "iPad server: http://$(ipconfig getifaddr en0 || ipconfig getifaddr en1):8000"
   .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

   If `Restart all (bridge v2).command` exports more variables than these, copy those
   `export` lines too (open the file in TextEdit). The only change from that file is
   `--host 0.0.0.0` instead of `--host 127.0.0.1`. To keep it, save a copy of
   `2 Start FairPlay server.command` as `2b Start FairPlay server (iPad).command` with only
   that one word changed.

   `FAIRPLAY_PUBLIC_URL` stays `127.0.0.1`: it is only used for the teacher links FairPlay
   sends to DKU OJ, which you open on the Mac.

   If macOS asks "Allow python3 to accept incoming network connections?", choose Allow.
4. Check from the iPad first: open Safari on the iPad at `http://<that IP>:8000/student/login`.
   If it doesn't load, the network blocks device-to-device traffic (university Wi-Fi often
   does): put the Mac and the iPad on a phone hotspot and print the IP again.

## 2. Make something to take

1. DKU OJ (`http://127.0.0.1:8100`, `teacher / teacher123`): in the class "CS 101 Section A"
   create a class login (for the first sign-in test) and an assignment that uses FairPlay
   (optionally allowed websites: they must be `https://` sites, and open-book materials such
   as a PDF).
2. Wait about 15 s for the sync, then open `http://127.0.0.1:8000/teacher` on the Mac
   (`teacher / teacher123`), open the exam and its live room: the start code and the
   invigilator code are there.

## 3a. iOS Simulator

From Terminal (double-clicking the file can't pass the server address):

```bash
cd <your fairplay checkout>/ios
./"Run in Simulator.command" -ServerURL http://127.0.0.1:8000
```

`-ServerURL` matters if you ever saved another address in the dev build (it is remembered).
Add `-CaptureShield NO` if you want screenshots of the exam. In the simulator the lock is the
dev "watched" lock (no Assessment Mode); the lock bar says so.

Logs: `xcrun simctl spawn booted log stream --predicate 'subsystem == "ai.proctora.student.ipad"'`
(lines like `[bridge] page -> app: prelock`, `[lock] ...`, `[web] navigation BLOCKED ...`).

Keyboard in the simulator: I/O > Keyboard > Connect Hardware Keyboard on/off; add a second
keyboard language in the simulated iPad (Settings > General > Keyboard > Keyboards), switch
with Ctrl-Space or the globe key on the on-screen keyboard.

## 3b. Real iPad (free Apple ID is enough)

One-time set-up as in `ios/README.md` ("Run it on your own iPad"): cable, Trust, Developer
Mode, Xcode account, Team = your Personal Team.

1. Open `ios/FairPlay.xcodeproj`. Product > Scheme > Edit Scheme > Run > Arguments >
   Arguments Passed On Launch: `-ServerURL http://<the Mac's IP>:8000` (one line).
2. Pick the iPad as run destination, Run. First launch: iPad Settings > General > VPN & Device
   Management > trust your certificate; allow "FairPlay would like to find devices on your local
   network" (or Settings > Privacy & Security > Local Network > FairPlay on).
3. The address is remembered for home-screen launches. To change it without Xcode: "Change
   server (dev build)" on the Connecting / Cannot reach screens, then close and reopen FairPlay.
4. Device debug builds have no Assessment Mode entitlement, so this is the watched lock too
   (`soft=1`, counted as locked only because the server runs with `FAIRPLAY_DEV=1`).

Logs: Xcode's console while it runs from Xcode, or Console.app > the iPad > filter
`ai.proctora.student.ipad`.

## 4. What to check (both)

1. **First sign-in with the class login**: "Who are you?" (first / last name, NetID) then
   "Choose your own login" (password checklist, strength bar, eye button). With the on-screen
   keyboard, in portrait and landscape: each field scrolls into view above the keyboard; NetID
   and username start lower-case. Note whether the QuickType bar offers autocorrect on NetID /
   username / a shown password (the web fields don't set `autocorrect="off"` yet).
2. **Home and course page**: course cards, `/student/courses/<id>`, Active / Past tabs, grade
   rings; the sticky top bar stays below the iPad status bar while scrolling.
3. **Pre-exam lock**: Before you start > Lock and continue > the lock bar appears at once
   (watched lock) > centred start-code boxes. While pre-locked, links to the home page are
   sent back to the start page (log: `pre-exam lock: in-page route ... sent back to`); "Not ready? Leave and unlock" unlocks; holding the lock bar
   3 s also unlocks at once (no code).
4. **Leaving the app** (the iPad version of the Mac "takes the front back" fix): tap Lock and
   continue and swipe home straight away; come back: the exam shows again (no dark "exam in
   progress" cover left over). During the exam: swipe home / open the app switcher > the live
   room shows the iPad as not locked (`console=0`, `left=1`) and an `app_backgrounded` event;
   back in FairPlay it continues. Open an app in Slide Over from the Dock: a dark cover while
   you use it, a `focus_lost` event; the red "FairPlay must fill the whole screen" only if
   FairPlay's own window gets smaller.
5. **Keyboard language switch** (the iPad version of the Mac Caps Lock crash): in an essay
   answer, type, switch keyboard (globe key; on a hardware keyboard Caps Lock, when the Caps Lock
   language-switch option is on in Settings > General > Keyboard > Hardware Keyboard; Ctrl-Space in the
   simulator) and keep typing and tapping. Expect no crash and no lost text. Hardware keyboard
   shows as `kbd=1` in the beat (reported, not blocking).
6. **Exam screen**: More > text size − / +; materials tabs (PDF viewer pages, search, zoom);
   allowed websites in the side pane (only the listed https sites load; links that open new
   windows do nothing).
7. **Hand in**: the iPad unlocks only after the server confirms (log `unlockPending` until then),
   then the done page. Invigilator exit during the exam: hold the lock bar 3 s, code from the
   live room (dev builds also accept `0000`).
