# Canvas mode end-to-end results (2026-10-26)

Last full run: 704 checks, 704 passed, 0 failed (`xvfb-run -a python3 e2e/canvas-mode/run_all.py`, about 6 minutes, Chromium and WebKitGTK).
Swift read at (sha256 prefixes): CanvasMode.swift 38e5a8112920, ExternalExamController.swift 719626691069. The Mac app reports the version in `mac/Sources/Proctora/Config.swift` (0.7.2).

Canvas mode has no access code any more: no code endpoint, no managed/manual code, no fill probe, no `canvas_code_*` events, no Canvas API call from the server. Students sign in to Canvas inside the app, move only on allowed pages, and after the quiz page loads they are locked in until the submission page, the server, the deadline or the emergency exit ends the exam.

## What runs

- `fake-canvas/server.py`: fake Canvas on https://127.0.0.1:443 (self-signed) with hosts canvas.fake.test (dashboard, course 1 and 2, quiz 1 with `/take`, `/submit`, `/submissions/5`, `/history`, quiz 2, assignments 9 and 10 with submission and confirmation pages, files, pages, modules, wiki, a download, an attachment, an Inbox, links and scripts that go to the exam server, a redirect to the exam server under the exam item), sso, go (redirect hop), saml (identity provider) and other (not allowed). Sign-in chain: canvas `/login` -> `/login/saml` -> go -> saml form -> POST back -> dashboard. Admin on http://127.0.0.1:9444 (`/mode`: require_login, warm_sso, skip_go).
- `run_server.py`: the real FairPlay server (uvicorn, sqlite, app mode on, `FAIRPLAY_CANVAS_SSO_HOSTS` set to the fake IdP). It never talks to Canvas.
- `canvas_nav.py`, `restricted.py`: Python port of CanvasNavigation (phases, path segments, refusals, download queries, sign-in and quiz-lti host lists from the scope, other hosts blocked in the quiz phase), CanvasQuizMarker (started and armed), the exam-server block, the armed finish rule, isLockedIn, popup plan, back and "Go to my quiz" rules, recovery gate, auto-open and warm-SSO landing.
- `tabs.py`, `wkhost.py`: one Tab interface over Chromium (CDP `Fetch` pauses every document request and redirect hop; `Page.frameNavigated`, same-document navigations) and WebKitGTK (`decide-policy` held until the controller answers).
- Student pages run in Chromium with an app stand-in that signs like the Mac app and sends the app version and platform headers.

## Sections

| Section | Pass / fail |
| --- | --- |
| 7a Swift test tables replayed (583 expectations parsed from CanvasModeTests.swift at run time) | 1 / 0 |
| 7b 7759 generated paths x 8 modes against an independent oracle (62072 decisions) | 1 / 0 |
| 7c look-alike paths (95), hosts (32), quiz-phase hosts (17), popups | 4 / 0 |
| 3 Teacher UI: class, Canvas exam, no access-code wording, open | 12 / 0 |
| 4 Student page + stand-in, Chromium and WebKit page behaviour (quiz opens without any code step, submit, finish regex in Python and in the engine, popups, opener) | 3 / 0, 14 / 0, 19 / 0 |
| 5b 150 students start the Canvas exam at once | 3 / 0 |
| 8a scope, open_book, API shape (no code fields; legacy fields ignored), odd and unclean links refused 422, scope-less link refused | 35 / 0 |
| 8b-8d R1 journey (open book off), Chromium 153 / 0, WebKit 149 / 0 | 4 / 0 |
| 8e-8g R2 journey (open book on), Chromium 107 / 0, WebKit 103 / 0 | 6 / 0 |
| 8h assignment (New Quizzes) journey, both engines | 74 / 0 |
| 8i exam link without a scope | 2 / 0 |
| 8j platform gate | 9 / 0 |
| 6 regression: plain website exam | 5 / 0 |

The journey (per engine): sign in through the redirect chain, land on the dashboard, one auto-open of the exam link; Back; other course, Inbox, calendar, profile, other quiz, course material (off), exam-server links (a link, a link on the dashboard, a redirect from a Canvas page, a script) all refused in LOBBY and QUIZ without any request reaching the exam server; pushState and replaceState to refused routes recovered; finish not armed on the history or a submission URL before the quiz is taken (lobby and QUIZ), armed by `/take`, disarmed by the item page; QUIZ phase on `/take` records `canvas_quiz_started` once, hides the finish control, stores the marker; every link, Back, `history.back()` and pushState tried in QUIZ phase stays on the item; popups to the exam server blocked; relaunch mid-quiz starts in QUIZ and never records the event again; relaunch with an expired Canvas session (sign-in host list in or out of the server's list); finish only by the submission URL (decoded path only, never query or fragment) after 2 s; teacher results hold the blocked_navigation events (details are `canvas page <path>`) and exactly one `canvas_quiz_started`.

Platform gate (8j): Windows and iPad get 409 "Canvas exams are not available on this device yet" (no Canvas minimum set), Mac below the minimum gets 426 with `min_app_version` 0.7.2, Mac 0.7.2 and 0.10.2 start, a start without an app platform header is refused, the code endpoint is gone (404/405), a plain website exam still starts on Windows and an old app. Not run: a server started with `FAIRPLAY_CANVAS_MIN_VERSION_WINDOWS` or `_IOS` set.

## Findings

- No server or web bug found in this round (the contract and the code agree; the harness follows the final tree).
- Swift: none open. Earlier findings (warm SSO without auto-open, QUIZ-phase hosts) were fixed in the tree and are now tested (warm SSO auto-opens; off-list sign-in hosts are refused by design, the exam's `sign_in_hosts` decides).
- Observed, not asserted: a sign-in popup served with `Cross-Origin-Opener-Policy: same-origin` has `window.opener` NULL in Chromium and WebKitGTK.

## Not covered

- The Swift app itself (nothing here compiles or runs it); the port is a reading of the Swift and the tables replayed from its tests.
- WKWebView specifics that WebKitGTK cannot prove: WKContentWorld isolation, COOP behaviour on macOS 14.4+, `createWebViewWith` and popup child views, `WKWebsiteDataStore` clearing, TLS, real navigation-policy ordering for form posts.
- Frame finish for New Quizzes (a confirmation inside a frame), sub-frame navigations.
- Real Canvas markup and SPA routing, real SSO and Duo, New Quizzes on the quiz-lti host, file uploads, the iPad and Windows apps, Redis and Postgres, a server restart mid-exam.

## Running it

Needs: Linux with root (port 443), `openssl`, Playwright Python with Chromium, `xvfb-run`, a built web (`npx vite build --outDir <dir>`, path in `CANVAS_E2E_WEB_DIST`, default `/tmp/canvas-mode-e2e/webdist`), a Python with the server requirements (`CANVAS_E2E_SERVER_PY`), and for WebKit `/usr/bin/python3.12` with `gir1.2-webkit2-4.1` plus a hosts line `127.0.0.1 canvas.fake.test sso.fake.test other.fake.test go.fake.test saml.fake.test`. Run the driver with the Python that has Playwright: `xvfb-run -a python3 e2e/canvas-mode/run_all.py`. `CANVAS_E2E_ENGINES=chromium` skips WebKit; `CANVAS_E2E_ONLY=restricted` runs only the restricted-browser sections.
