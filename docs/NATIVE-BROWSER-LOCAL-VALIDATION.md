# Native browser local validation — 2026-11-05

Local work only. Nothing was pushed, deployed, released, or copied to either VM.

## Observed Mac behavior

In the isolated FairPlay Test app, using the local DKU OJ fixture:

- Actual native lock, DKU login, and entry into the disposable `nativepractice` contest succeeded.
- The short-answer editor loaded with the website styles and editor controls.
- A typed answer survived a page reload. The website reported a saved draft and the native app reported a local draft save.
- Submit returned “Your submission has been received.” Submitting one question did not unlock the Mac.
- The website Leave action returned to the contest page. The signed completion response then caused automatic native unlock and returned to the app's home screen.
- This was an ordinary disposable contest, **not proof of admission into a FairPlay-tagged contest**. The site allows this ordinary contest to be rejoined; that must not be treated as terminal completion for a protected exam.

After rebuilding with the selected familiar-tabs header and restoring the released question page:

- Test my device displayed the sample questions and a countdown.
- A numeric answer persisted through Next and Back.
- Leave test ended the preview and restored the normal window and home screen.
- Both platforms now open the existing `/student/device-test` screen, preserving its released question layout and controls. The temporary duplicate native question views were removed.
- The Mac app displayed the released radio-button and numeric questions; a numeric answer survived Next and Back, and the released Leave button returned to the unlocked entry screen.
- The existing timed preview lifecycle is unchanged. Practice answers remain in memory and are not submitted or graded.

Temporary chat-app and screenshot exceptions were removed. An older running test copy was closed; the current student build remains open.

## Automated checks

- Mac build succeeded. Device-test selection: 16 tests in 4 suites passed.
- DKU instructor/editor, student pages/discovery, security, launch, and signed-context checks: 131 passed.
- Windows logic checks: 1543 passed. WPF cross-build succeeded on macOS; existing warnings remain.
- Released device-test page: 15 tests passed; web typecheck and production build passed. Run tests on the current Node runtime with `NODE_OPTIONS=--no-experimental-webstorage` so jsdom owns localStorage.
- Download/instruction follow-up: 49 DKU launch/page tests passed.

## Production destination and read-only connectivity

- Production DKU URL remains `http://10.200.31.64/` in both clients. The Mac loopback override is compiled only into isolated test builds.
- DKU Download FairPlay links to `https://github.com/avazbekkhamidovedu-creator/fairplay-download/releases/latest`, not a fixed release version.
- Both VM SSH connections succeeded with their already-configured deployment keys. 4228 runs Ubuntu 22.04.5; 4158 is the professor's Linux server.
- The live FairPlay health endpoint returned `ok: true`. All three live DKU login CSS files returned HTTP 200 with `text/css`.
- The public latest release is still v0.7.9 with both Mac and Windows downloads. No new release has been published.

## Explicitly unverified or unfinished

- Native-only admission now has a default-off DKU request gate and real-route server tests. Production TLS device enrollment/ingress and native client wiring remain unfinished. Do not enable this gate with user-agent flags, page scripts, or client-reported lock booleans.
- Protected exam completion needs a terminal participation transition, plus authenticated abort/recovery behavior. A normal contest Leave receipt is insufficient as a production exam policy.
- The Windows feature is development-only (`--dku-browser`). Its signing key must be supplied as a compiled resource before signed completion can function. No real Windows desktop test has been run.
- The owner VM 4228 is a Linux server, not a Windows desktop. No VM changes were made.
- Instructor-approved reference tabs now have Windows policy tests, but actual WebView2 popup, download/upload, crash, and recovery behavior still needs Windows runtime testing.
- The selected header was compiled; the previous real-lock lifecycle test predates that cosmetic header change.

These results are not a production readiness declaration.


## Protected admission follow-up (2026-11-05)

Implemented locally in the DKU worktree:

- Durable one-account ownership slot with a bound device fingerprint and browser session digest. Lease expiry denies access without allowing takeover.
- Request-bound proxy proofs, replay rejection, and a narrowly scoped content gate. Permissions reset after every response, including exceptions. Protected writes and revocation serialize on the same account lock.
- Claim, status, renew, and final-hand-in APIs. Final hand-in freezes new writes, harvests retained answers, and only signs completed after no pending substantive draft remains.
- Instructor Unlock device in the existing action menu, with permission checks, confirmation, reason, and audit record. Releasing access is distinct from submitting an exam.
- The draft route now checks protected admission even for an already-joined participation.
- Both native clients have signed v2 device-command parsers rejecting wrong session/device, replay nonce, and expiry. These parsers are not yet wired to the browser coordinators.

Verified server route sequence: authenticated test student -> device claim -> actual join route -> rendered problem -> saved answer -> final hand-in -> persisted submission -> rejected late write/re-entry. A simulated storage failure retains the answer and finishing state; retry completes. These are Django integration tests with signed proxy fixtures, not native-device or TLS-gateway E2E proof.

Final protected-admission regression selection: 133 tests passed. Migration drift check passed. The local preview database was backed up and its three new ownership/audit tables created; neither VM database was changed.

Mac signed-command test passed. Windows 1,548 logic checks passed and the WPF cross-build succeeded; all 194 bundled assets verified. Core lock engine files remain unchanged.

Remaining release gates: production device enrollment and authenticated ingress; Mac/Windows coordinator integration; instructor reopen/move semantics for native sessions; upload/media/alternate-route coverage; database concurrency validation on the deployment database; actual locked Mac protected exam and Windows desktop validation. No release version, remote deployment, or public download was changed.

## Actual protected native flow — 2026-11-05, 06:18–06:36 UTC

This section supersedes the earlier statement that native coordinators were unwired and protected Mac E2E had not run. Everything remains local and unreleased.

### Observed on this Mac

- The isolated signed app connected to the local TLS gateway with its test client certificate, logged in as the disposable student, claimed a FairPlay-tagged exam, joined it through DKU's real route, and rendered the protected question and editor.
- The first run exposed a local-draft Keychain read blocking the main thread while locked. After that access completed, the app resumed. Draft-key preparation now runs asynchronously before locking, caches the key, and refuses interaction during answer saves. The rebuilt app passed that preflight and opened a second protected exam without the freeze.
- In `nativee2e1105b`, a typed answer survived Reload. The native draft indicator reported saved and the DKU server accepted the draft. This reload used both recovery mechanisms; it is not an isolated offline-recovery proof.
- Submit returned “Your submission has been received.” The Mac remained locked. Finish exam then received a signed v2 completed receipt and returned to the normal unlocked welcome window.
- Read-only database verification: the device session was completed, its account slot was empty, and submission 2 existed. The HTTP log records claim/join/draft/submit/finish success.
- On the rebuilt app, `nativeunlock1105b` entered the protected problem list. A teacher-authenticated request to the existing help endpoint revoked the device. Native polling received released and returned to the unlocked welcome screen. This checks the teacher HTTP action and native receipt, not a manual teacher-browser click-through.
- Temporary screenshot and chat exceptions were removed after both tests. The app is unlocked.

### Changes and current verification

- Mac and Windows browser coordinators now use client-certificate identity for claim/renew/finish/abort and bind signed receipts to device, account, exam, session, nonce and expiry.
- Normal signed v1 browser context cannot unlock an active v2 protected session. HTTP denials no longer erase an ambiguous claim.
- Protected responses now carry the instructor's reference-site policy. Clients apply its signed expiry and remove references outside active state.
- Ordinary requests no longer erase the globally active native participation. Admission is still request-scoped. The problem list and questions shared with another protected exam are accessible only through the admitted exam scope.
- SQLite local navigation uses Python regex matching rather than MySQL-only REGEXP BINARY.
- Mac: six focused coordinator/signature/encrypted-draft tests passed; signed isolated build succeeded.
- DKU: 61 admission, device ownership, hiding, browser context and proxy tests passed, including abort, signed reference policy, failed hand-in, teacher release and retained participation.
- Windows: 1,556 logic checks passed; WPF cross-build succeeded with the same three warnings. No Windows runtime proof.
- No lockdown algorithm, event tap or Windows Guard/Watchdog behavior changed in this work. The inherited Mac Watchdog diff only selects the isolated development support folder; it predates these coordinator edits.

### Release remains blocked

The local gateway and imported identity are disposable fixtures, not production enrollment. Production still points to the professor's HTTP endpoint and does not yet provide the authenticated TLS ingress/device provisioning required by this protected admission design. Do not distribute the local fixture certificate, CA, paths or secrets.

Still required: production identity provisioning and ingress, native interrupted-session recovery, definitive recovery for ambiguous claims, instructor reopen/move semantics, editing freeze during final draft flush, broader upload/media/alternate-route and deployment-database concurrency validation, and real Windows desktop validation. Windows browser entry is still development-only. The shipped signing-key/gateway configuration must be completed and verified before release.

GBrain lookup returned no results with embedding unavailable. No VM, release version or public download was changed.

## Current candidate follow-up — 2026-11-05

- Header correction on Mac and Windows: Leave exam is anchored at the right of the tab row, separate from navigation and draft status. Both builds compile. Visual and locked lifecycle validation of this exact candidate is pending.
- Closed-attempt correction: recovering a signed terminal receipt during a new start no longer closes the browser. The client clears only that resolved admission attempt and explains that instructor reopening is required. Existing bound attempts still require their matching signed terminal receipt.
- Expired-login recovery now retries the same-device recovery endpoint after renewal denial on both clients. The prior locked run required manual local-session recovery, so automatic recovery is NOT yet counted as passed.
- Five focused Mac session/receipt tests pass. Windows WPF cross-build passes with three existing warnings. Targeted DKU native service/API/ingress selection passes 30 tests before the final scheduling adjustment; rerun that selection before deployment.
- The local fixture contains only the Assignments navigation entry. This is fixture data, not a production menu change.
- Professor SSH succeeds, but sudo requires a password. Administrator setup for port 8443, TLS ingress and header stripping remains external work; no production ingress is active.
- Current app is waiting at device preflight because Code is open. Do not close it without renewed saved-work confirmation.
- No version bump, push, deployment or release was performed.

## Website-owned hand-in correction

The extra native Finish/Leave button is removed on both platforms. Native navigation no longer intercepts DKU's contest leave POST to display a second FairPlay confirmation. The existing DKU whole-exam action now finalizes the matching authenticated native session, retains drafts if hand-in fails, and clears the student's current contest only after completion. The existing native renewal API returns the signed completed receipt and triggers unlock. Individual question submissions do not complete the device session.

Verification: 17 native API tests pass, including an actual DKU leave-route hand-in followed by a signed completed poll and rejected late answer write. Five Mac binding/receipt tests pass. Windows WPF compiles with three existing warnings. Exact rebuilt Mac GUI flow and production deployment remain pending; these checks are not live-device proof.
