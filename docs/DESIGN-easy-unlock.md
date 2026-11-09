# Easy unlock for a locked student device (design B)

Standard kept: a graded lock ends only by the instructor's 6-digit code, an OJ-signed completed/released, or a signed rejected that matches the saved binding. Nothing below adds a new way to unlock. It only makes the instructor's code reach the device without anyone reading or typing it.

## Decision
One instructor click, "Release device", on the OJ FairPlay help page. The OJ already knows each session's Mac code from the `lock` audit row (`bind_lock`), so it fills the code in and sends the existing `native-exit` bridge call with `push: true`. The server issues the code exactly as it does today, records `released_at`, and returns the code to the OJ, which shows it as the offline fallback. While the Leave sheet is open, the device polls the server every 5 s (and every 60 s otherwise when `binding == nil`). Once the lock is released, the server returns the same code. The app checks it against its local verifier through the existing path and unlocks. No new signing key, envelope format, or trust root is added on the device. A pushed code is the instructor's code. Bound sessions also keep "Unlock student device" (OJ-signed released over renew/state). The new push covers the cases where that path cannot work: binding nil, or the OJ is down but the server is up.

## Flows ranked (ease, then security)
1. Push release by OJ click (recommended). The student does nothing, and the instructor makes one click with no typing. Needs device to server.
2. Existing OJ-signed "Unlock student device". One click. Needs a saved binding and device to OJ. It is already built, so it stays as is.
3. QR on the Leave sheet linking to `<OJ>/contest/<key>/fairplay-help?mac=<CODE>&netid=<user>`. The instructor scans it and lands on a confirm page with the code filled in. No URL change: it is a GET prefill on the existing view. The device shows the QR only when `contest` is known. Otherwise it shows the code as text.
4. Offline code read aloud (today's flow). This is the hard fallback when the device has no network. It only works if the lock was registered at some point.
Rejected: a server-signed release envelope (a new trust root on the device for no gain), and a free unlock for unregistered locks (it would weaken the standard).

## Threat check
- Self-unlock: the student cannot trigger a release, because it needs an editor's signed OJ bridge call. Before release, the poll returns 204 and never the code. The app-proof secret can be pulled out of the binary, so it gates nothing secret: the code goes out only after the instructor released that lock.
- Learning the code: after release, knowing it is harmless because the instructor already allowed the exit. The Mac code itself (lock id) is an identifier, not a secret, so it is safe to put in a QR or URL.
- Replay: the code is HMAC(K_lock, lock_id). The lock is deleted on accept, and a new lock gets a new id and key. The server returns the pushed code only for 2 h after `released_at`, and only to the `device` that registered the lock. A stale release for another exam is ignored: the response carries `exam_key`, and the device accepts only when its saved `contest` is nil or equal.
- Wrong or forged pushed code: it is checked with `NativeExit.verifies`. A failed check does not count as an attempt, so a MITM cannot lock the student out. It also cannot unlock, because the code must match the verifier.
- Binding nil: the OJ has no lock-to-student link, so today only a site admin may release (`_lock_problem`). An unbound lock never guarded a claimed OJ session, because claim and `bind_lock` run in one transaction. Recommended: also allow the exam editor when the netid is on the roster and the server row's `netid` is null. The issue pins netid and exam on the row, and the device-side `exam_key` pin covers the rest. Owner may keep admin-only (not blocking).
- Unregistered lock (verifier nil, the 2026-11-09 incident): no code exists anywhere until `registerMissingVerifier` / `RegisterSavedAsync` succeeds. The sheet must say "Connect to Wi-Fi; FairPlay registers this Mac automatically". Once registered, flows 1, 3 and 4 work. With no network ever, there is no way out without weakening the standard, and that is intended.

## Changes
Server (FairPlay DB only), about 0.5 day:
- `server/app/integrations/dkuoj/native_exit.py`: add nullable columns `released_at` and `release_exam_key` to `NativeExitLock`, plus their migration.
- Same file: `native_exit` accepts `push: true` and sets both columns in the same claimed UPDATE.
- Same file: new `POST /api/native-exit/locks/{lock_id}/release` with `{device, signature}`. The signature is an app proof over `nativerelease|lock|device`. It returns 200 `{code, exam_key}` or 204. Rate limits are per lock and per IP. `NO_STORE`, and the code is never logged.
- `server/tests/test_native_exit.py`: tests.
Mac, about 0.5 day:
- `mac/Sources/Proctora/NativeLock.swift`: a `releaseTransport` plus a poll loop with cancellation through `registrar`.
- `mac/Sources/Proctora/NativeExit.swift`: `acceptPushed(code:examKey:store:)`, which verifies without touching the counters and checks the contest pin.
- `mac/Sources/Proctora/ProtectedBrowserController.swift`: start the poll on `leavePressed` and in the background when `binding == nil`, and call `completeLeave` on accept.
- `mac/Sources/Proctora/NativeLeaveSheet.swift`: "Waiting for your instructor to release this Mac..." text and a CoreImage QR.
Windows (same logic), about 0.5 day:
- `src/FairPlay.Core/NativeExit.cs`: `AcceptPushed`.
- `src/FairPlay.Core/NativeLockSetup.cs`: the poll.
- `src/FairPlay.Shell/NativeLeaveDialog.cs`: the text. Show a QR only if a lib is already vendored. Otherwise use text.
OJ (FairPlay-named modules only, no migrations, no new URLs), about 0.5 day:
- `judge/views/fairplay.py`: put the bound Mac code (latest `lock` audit row) on each row. Add a `release-device-push` action that reuses the `offline-exit` checks with the code prefilled and `push=True`. Handle the GET prefill `?mac=&netid=`.
- `judge/utils/fairplay_client.py`: add a `push` arg to `native_exit`.
- `templates/contest/fairplay_help.html`: per-row "Release device" button and a Mac code column. Sort locked or on-hold students to the top. The existing table is the live list; add a meta refresh every 15 s.
- `templates/contest/fairplay_help_code.html`: "The device unlocks by itself within ~10 s if online; otherwise read this code."

## What to test
- Server pytest: 204 before release; 200 after release for the right device; 204 for another device, after 2 h, and after the issue cap; an editor of another exam gets 409; no code in the logs.
- Mac `swift test` and Windows unit tests: a pushed wrong code leaves `wrong` and `attempts` unchanged; a code with a mismatched `exam_key` is ignored; an accepted code deletes the dual store; the poll stops on close.
- OJ (`.tmpvenv-ojtest/v12`): prefill fills the Mac code from the audit row; an unbound lock follows the chosen rule; the GET prefill never issues anything.
- Headless simulation on the local stack only: start the server and OJ locally. A script uses `tools/native_exit_vector.py` to make K and seal it, registers the lock with an app proof, and runs the vector verifier with PBKDF2. It then calls the OJ help POST `release-device-push` as the instructor, polls `/release` as the device, and asserts the code matches the verifier. Repeat with the server down at registration, then up (background re-registration, then release). Do not test against the VM or a real Mac until this passes.
