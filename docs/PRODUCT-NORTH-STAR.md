# FairPlay: the finished product, and how far we are (2026-11-01)

Estimates below are honest judgement from the code, the 2026-10 security audit and the test runs. They are not measurements. Nothing marked "done" has been run on real Windows hardware or a real university LMS unless it says so.

## 1. What "finished" means

One FairPlay app a university buys, and students use for every exam:

1. Install FairPlay (Mac, iPad, Windows). Open it. Sign in with the class account (exams; there is no exam access code) or the university login (homework).
2. FairPlay shows ONE locked exam screen. Inside it runs either FairPlay's own question UI, or the university's ORIGINAL website (Canvas, Moodle, Blackboard, Brightspace, Schoology, DKU OJ, Chaoxing, Learning Mall, custom sites). No LMS integration, no plugin, no change on the university side.
3. The lock stays on from the first page to hand-in: no other apps, no new browser windows, no external links, no clipboard or screenshots leaving the exam (as far as each OS allows).
4. The student's work is never lost. If the internet drops, the exam keeps working, answers are kept on the device, and everything syncs or is handed in sealed when the connection comes back.
5. The teacher sees who is in the exam, what happened (events), and the result, with no per-LMS setup.
6. Nothing a student does by accident interrupts the exam: restarts, sleep, Wi-Fi flaps and school-network sharing all recover without teacher help.

### The unified exam screen (the target design)
- One native window per platform: WKWebView (Mac, iPad) and WebView2 (Windows), one shared behaviour spec and one shared test suite.
- A thin FairPlay bar (timer, connection state, "saved on this device" indicator, help/exit).
- Per-site compatibility profiles chosen by the university admin: allowed hosts (including the SSO and MFA hosts), whether uploads, popups and clipboard are allowed, and the finish rule.
- Child windows for popups with the same navigation rules; native handling of alert/confirm/prompt.
- Controlled file upload (allowed types and sizes, a FairPlay-owned picker), controlled clipboard (off / inside the answer area / allowed).
- Camera and microphone permission handling for proctoring-style sites, off by default.

### Saving answers locally, for any website
Be honest about what is possible:
- FairPlay's own questions: guaranteed. Answers are saved on the device first, then synced; sealed offline hand-in exists today.
- Any external website: we cannot make a third-party site work offline. What we can do is best effort in layers:
  1. Inject a recorder that keeps an encrypted local copy of what the student typed (inputs, textareas, rich-text and code editors such as Monaco and CodeMirror) keyed by exam and URL, and offers to restore it if the page reloads empty.
  2. Keep a local, encrypted event journal and periodic page snapshots as evidence for the teacher if the site loses the submission.
  3. Hold the app open in a "site unreachable" state with a clear message instead of closing, and keep the lock; let the teacher decide on a retake or extension.
  4. For sites that autosave on their own (Canvas quizzes, Moodle), do nothing that fights them.
  We must tell universities plainly: a guaranteed offline exam needs FairPlay's own question UI.

## 2. Where we are

| Area | Done | Rough completion |
|---|---|---|
| FairPlay own-question exam (server, web, Mac, iPad, Windows): sign-in, start code, lock, hand-in, resume, sealed offline hand-in, teacher monitoring | Built, audited (21 confirmed findings fixed), unit and local E2E tested. Not run on real Windows/iPad; stress test in progress | ~85% |
| DKU OJ bridge (exam sync, rescue, homework app login; access codes removed 2026-11) | Built, audited, patched. DKU OJ tests not run here; homework UI unfinished | ~75% |
| Canvas restricted browser (Mac, iPad, Windows) | Built; scope fixes just made; New Quizzes and real Canvas not tested | ~60% |
| Universal website mode (any LMS) | Opens and locks any https site with a host allow-list. Missing: uploads, popups, JS dialogs, clipboard policy, camera, SSO host sets, per-LMS scoping | ~40% |
| Local answer saving for external sites | Not started (own-question exams only) | ~10% |
| Sign-in compatibility (SSO, Duo, TOTP, WebAuthn, Google) | Password/SAML/OIDC redirect flows plausible; Google embedded sign-in impossible; passkeys not supported; never tested with a real IdP | ~30% |
| Device security (device key, signed verdicts, watchdog, update channel) | Device key done on server, Mac, iPad, Windows (not on real hardware). App key still shared; updates unsigned | ~60% |
| Windows client | Core logic tested; Shell/Guard/Watchdog never compiled or run on hardware | ~55% |
| B2B platform (tenancy, org admin, audit log, per-org config, grade delivery, backups, load proof, data residency) | Planned in FUTURE.md section 4, mostly not built | ~20% |
| Real-world test matrix (SSO + Duo, Moodle, Blackboard, CAS, Chinese LMSs, capture tools, network loss) | Designed, not run | ~5% |

**Overall:** about 85% of a DKU pilot using FairPlay's own question UI; about 40% of the universal "any website, any university" B2B product.

## 3. Next steps, in order

1. Finish what is in flight: the exam stress test, then commit, then an end-to-end exam click-through (selected students, sign-in, hand-in, teacher view).
2. Verify on real hardware: compile and run Windows in CI and on a PC; run the Mac and iPad apps on real devices; a small pilot exam with 3-5 volunteers.
3. Universal-mode blockers (see FAIRPLAY-UNIVERSAL-WEBVIEW.md): uploads, clipboard policy, popups/child windows, JS dialogs, camera permission, IdP host sets in site profiles.
4. Local answer safety for external sites (section 1 layers) plus the connection/"saved" indicator in the bar.
5. Sign-in: test Entra, Duo, Shibboleth/CAS, Google workarounds, with real accounts; decide the system-browser path.
6. LMS profiles and a test matrix: Canvas, Moodle, Blackboard, Brightspace, Schoology, DKU OJ, Chaoxing, Learning Mall (see the matrix in the universal-WebView document).
7. Remaining security work: signed updates and release gating, the Windows watchdog hardening, Hello-bound remember-me, the 45 needs-validation items once facts are known.
8. B2B platform: tenancy, org admin and audit log, per-org config, grade delivery, load test, backups and data-residency decisions.
9. Apple assessment mode and Sparkle auto-update (FUTURE.md sections 1 and 2) once the organization account exists.

Related documents: FUTURE.md, CONTRACT.md, docs/device-key.md, docs/REAL-DEVICE-TEST.md, and the universal-WebView and security-audit reports under ~/security-audit-skill/.
