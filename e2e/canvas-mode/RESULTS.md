# Canvas mode end-to-end results (2026-10-25)

Last full run: 156 checks, 156 passed, 0 failed (`xvfb-run -a python3 e2e/canvas-mode/run_all.py`).
Server suites after the fixes below: pytest 904 passed; vitest 725 passed (69 files); `tsc` (both configs) clean.

## What runs

- `fake-canvas/server.py`: fake Classic Quizzes on https://127.0.0.1:443 (self-signed), hosts canvas.fake.test (quiz, `/take` access-code form, submit, `/submissions/5`, decoy page, opener page, REST API with Bearer token), sso.fake.test (sign-in pages, popup, popup with COOP) and other.fake.test (not allowed). Admin on http://127.0.0.1:9444 reads the current code (never in server output).
- `run_server.py`: the real FairPlay server (uvicorn, sqlite, app mode on). The Canvas client is wired to the fake through `lms.set_transport_factory` (same hook as tests/test_lms.py); no production check is weakened.
- Chromium (Playwright, `--host-resolver-rules`, ignoreHTTPSErrors) drives the teacher UI and the student web page (with an app stand-in that signs like the Mac app).
- `standin.py` ports CanvasPolicy.allows / fillOrigin / isQuizPath, SiteRule, CanvasFinish.matches / isSafe, CanvasFillBudget, CanvasRetry, CanvasPopup.plan and the code request loop of ExternalExamController. The probe, fill and popup-guard JavaScript is read out of `mac/Sources/Proctora/CanvasMode.swift` at run time and run unchanged.

Per engine (the same suite): decoy and sign-in pages (no fetch, no fill), wrong-code path (3 fills then help), landing page, fill, quiz opens, submit, finish URL by the server's regex (Python re and the engine's JS RegExp), popup plans, popup opener and postMessage.

| Engine | Result |
| --- | --- |
| Chromium (Playwright, headless) | 26/26 pass |
| WebKit: WebKitGTK 2.52 (`MiniBrowser` via WebKitWebDriver, Xvfb) | 32/32 pass (includes the hand-in step) |
| Firefox | not run: Playwright's Firefox/WebKit downloads are blocked by the sandbox proxy (403) and Ubuntu's Firefox is a snap. Playwright's own WebKit build could not be installed either; WebKitGTK from apt is used instead. |

Observed, not asserted: a sign-in popup served with `Cross-Origin-Opener-Policy: same-origin` has `window.opener` NULL in both Chromium and WebKitGTK.

| Section | Pass / fail |
| --- | --- |
| 3 Teacher UI (link Canvas, class, Canvas exam, open, code absent from UI, API bodies) | 15 / 0 |
| 4 Student page + app stand-in (lock before openExternal, message contents, hand-in via external-done) | 3 / 0 (+5 hand-in checks counted in the engine sections) |
| 4 Chromium and WebKit page behaviour | 26 / 0 and 32 / 0 |
| 4b Manual code through the UI (typed, encrypted, not rotated, not cleared in Canvas, forgotten on close) | 12 / 0 |
| 5a Negative and abuse (no/wrong/non-ASCII/replayed/stale/future proof, other student, other session, closed exam, pending resume, non-mac platform, 7th request) | 39 / 0 |
| 5b 150 students at once (2.9 s, all 200, one shared code, 150 `canvas_code_issued`) | 6 / 0 |
| 5e Per-exam limit (456 a minute for 155 students: 301 of 450 extra requests served, 149 refused 429 with Retry-After, recovers next minute) | 5 / 0 |
| 5c Close: code cleared in the fake Canvas, stored code gone, reopen rotates to a new code | 7 / 0 |
| 5d Leak scan (server log, teacher results/events, student views) for every code seen | 3 / 0 |
| 6 Regression: plain website exam (no canvas fields, no Canvas call, non-mac start ok, code endpoint 409, external-done hand-in) | 8 / 0 |

## Bugs found and fixed (server and web)

1. Server accepted finish patterns the Mac app refuses. `CanvasFinish.isSafe` rejects any group followed by `*`, `+` or `{` that holds `|`, a repeat or another group; the server only checked the first two and only for repeats above one. A pattern such as `[ab](\d(?:\.)\d){2,}\.`, `((/))*` or `(a|b){1}` was saved, the app could not compile it, and a Canvas exam would never finish by URL. Fixed in `server/app/canvas_mode.py::validate_regex` (also CONTRACT.md); tests added to `test_regex_rejected` / `test_regex_accepted_and_kept`. Found by fuzzing 6000 patterns through the server validator and a port of isSafe.
2. Web editor refused a leading `(?i)` that the server and the contract accept, and its test asserted the refusal. Fixed in `web/src/components/teacher/externalValidation.ts` (leading `(?i)`, `(?ims)`); the web validator also got the same nested-group rule as bug 1. Tests in `web/src/test/canvasMode.test.tsx`.

## Findings in Swift (not edited)

- `mac/Sources/Proctora/CanvasMode.swift` `CanvasPolicy.fillOrigin` (line 17 onward) requires the page host to equal the start URL host. New Quizzes and assignment quizzes put the access-code field on the quiz-lti host, which is allowed to load but never gets the code, so for `new` / `assignment` the student sees the code form and the app does nothing (the contract says manual codes work for these types). Unverified without a real New Quizzes page; fix is to allow the quiz-lti hosts the server added to allowed_domains for the fill, with their own path check.
- `CanvasFinish.isSafe` (lines 56-100) is stricter than the server on nested groups; fixed on the server side (above). If the app should accept them, drop the `nested` flag instead.
- `ExternalExamController.observeURL` (line 366) watches only the main web view; a finish URL reached inside a sign-in popup child view is never seen.

## Not covered

- The Swift app itself (nothing here compiles or runs it). The port in `standin.py` is a reading of the Swift; if the Swift differs from that reading the results do not transfer.
- WKWebView specifics that WebKitGTK cannot prove: WKContentWorld `.defaultClient` isolation of the injected scripts (the probe/fill run in the page world here), COOP-nulled `window.opener` behaviour on macOS 14.4+ (only observed that engines null it), `createWebViewWith` / `webViewDidClose` delegate behaviour (popups are plain window.open here, the guard script is injected after load, not at document start), SSO cookies and clearing in `WKWebsiteDataStore`, TLS pinning, and the real navigation-policy callbacks (blocked hosts are tested through the ported plan, not by the engine refusing them).
- Real Canvas markup and behaviour (the fake follows the described form; a real instance may differ), real SSO and Duo, New Quizzes (quiz-lti), assignments, file uploads, the iPad and Windows apps, Redis and Postgres, a server restart mid-exam.

## Running it

Needs: Linux with root (port 443), `openssl`, Playwright Python with Chromium, `xvfb-run`, `webkit2gtk-driver` and `selenium` (path in `CANVAS_E2E_SELENIUM`), a built web (`npx vite build --outDir <dir>`, path in `CANVAS_E2E_WEB_DIST`), a Python with the server requirements (`CANVAS_E2E_SERVER_PY`), and for WebKit a hosts line `127.0.0.1 canvas.fake.test sso.fake.test other.fake.test`. Then `xvfb-run -a python3 e2e/canvas-mode/run_all.py` (about 4 minutes; `CANVAS_E2E_ENGINES=chromium` skips WebKit).
