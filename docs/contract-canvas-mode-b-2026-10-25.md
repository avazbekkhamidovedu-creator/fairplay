# Canvas mode (Mode B): contract, 2026-10-25 (rewritten 2026-10-26)

A Canvas exam is an existing website exam (`mode: "external"`) with `external_kind: "canvas"`. The teacher builds the quiz in Canvas with no access code. Students sign in to Canvas inside the FairPlay lock and can reach only that course and that quiz; once the quiz has started they cannot leave before submitting.

## Exam fields (teacher POST/PUT, external mode only)

```
"external_kind": null | "canvas"
"canvas": {
  "quiz_type": "classic" | "new" | "assignment",
  "removed_hosts": ["..."],       // auto-added sign-in hosts the teacher removed
  "open_book": false              // default false; omitted on PUT keeps the saved value
}
```
- `external_url` is the quiz or assignment link. Host must be https. It must open one quiz or assignment (`/courses/:c/quizzes/:q`, `/courses/:c/assignments/:a`, optionally followed by more path); anything else is 422. `quiz_type` defaults from the URL (`/courses/:c/quizzes/:q` is classic, `/courses/:c/assignments/:a` is new).
- `allowed_domains` is built on the server: the Canvas host (exact), the quiz-lti hosts for `new` (`*.quiz-lti-*.instructure.com` form is expanded to a plain list the apps already understand), and the configured sign-in hosts (`FAIRPLAY_CANVAS_SSO_HOSTS`, default Duo, Microsoft login, DKU SSO) except those in `removed_hosts`. Teacher additions are kept. Every host passes `site_policy`.
- `finish_url_regex` (server-filled when `external_kind` is canvas, teacher can override): classic `/quizzes/\d+/(submissions/\d+|history)`, new `submission-confirmation|submission-screen`, assignment `/assignments/\d+/submissions/\d+`. `finish_url_contains` is ignored for canvas exams. Max 300 chars, the plain regex subset Python and ICU read the same (details in CONTRACT.md), 422 otherwise.
- Student and teacher exam views add `external_kind`, `canvas: {quiz_type, scope, open_book}` and `finish_url_regex`; teacher views also add `canvas.removed_hosts` and `canvas.sign_in_hosts`.
- A saved exam whose link has no scope (older data) is never started or opened: start answers 409 "This Canvas exam link needs to be fixed by your teacher", open answers 409, and the teacher view says to fix the link.

## openExternal bridge action (page to app), additions

```
{"action":"openExternal", "attempt_id":..., "url":..., "allowed_domains":[...],
 "kind":"canvas", "finish_url_regex":"...", "canvas_scope": {...}, "session":...}
```
`canvas_scope` is described under "Restricted Canvas browser".

## Native behaviour for kind "canvas" (Mac, iPad, Windows)

1. Navigation: https only, exact host or listed subdomain match; scheme and host checked on every main-frame navigation and redirect; blocked ones record `blocked_navigation`.
2. Popups: `window.open` to an allowed host opens in a child web view that shares the cookie store with the main view (so sign-in popups that need `window.opener` still work); anything else is blocked.
3. Finish: when the decoded URL path (never the query or fragment) of the main frame or of a frame matches `finish_url_regex`, and the quiz page has been loaded since the student last saw the item landing page, wait 2 s, then run the existing finish flow (`external-done`). Sign-out of Canvas happens after finish: clear the web data store.
4. Downloads, file panels, DevTools and context menu stay off, as in existing website mode. Canvas file-upload questions use the existing early-upload / file rules; if the app cannot show a file panel, record `canvas_upload_unsupported` and tell the teacher in the exam view.
5. Heartbeats, events, hand-in, resume and device swap are unchanged.

## Events (integrity)

Client-reportable: `canvas_upload_unsupported`, `canvas_sso_popup`, `canvas_quiz_started` (when the app enters QUIZ phase; bookkeeping, not a flag; the teacher view also accepts the older stored form `client_canvas_quiz_started`).

## Not in scope

DKU OJ code, the DKU OJ bridge, LTI 1.3 launch (later), changing main on the repos.

## Restricted Canvas browser (2026-10-26)

The student signs in to Canvas inside the app and moves around Canvas, but only on pages the exam needs.

### Server and web

- Teacher and student exam views of a Canvas exam add `canvas.scope`: `{"course_id": "123", "item_kind": "quizzes" | "assignments", "item_id": "456"}`, or `null` when the exam link does not match `^/courses/(\d+)/(quizzes|assignments)/(\d+)` (ids digits only, at most 20). Derived from `external_url` on every save, never sent by the client.
- `canvas.open_book: bool` (default false): teacher toggle, writable on POST/PUT, omitted on PUT keeps the saved value. When true the student may also open the course's pages, modules and files.
- The `openExternal` bridge message adds `"canvas_scope"` for Canvas exams (absent when `scope` is null). The student attempt view carries the same object as `canvas_scope`:
  `{"course_id": "123", "item_kind": "quizzes"|"assignments", "item_id": "456", "open_book": false, "sign_in_hosts": ["login.microsoftonline.com", "*.duosecurity.com"], "quiz_lti_hosts": ["*.quiz-lti-iad-prod.instructure.com"]}`.
  `sign_in_hosts` is the configured sign-in list minus the teacher's removals and anything the server's website policy blocked (exact hosts or `*.host`, as stored in `allowed_domains`). `quiz_lti_hosts` is the New Quizzes host list for `new` exams and `[]` otherwise. The app uses these two lists, not name guesses, for the non-Canvas hosts it allows in QUIZ phase.
- Teacher exam setup shows the open-book checkbox for Canvas exams (off by default) and a plain sentence listing what students will be able to open.

### Native app (Mac): main-frame navigation on the exam's Canvas host

`path` below is the URL path without query. `C` is `course_id`, `K` is `item_kind`, `I` is `item_id`. Sub-frames and sub-resources keep the existing behaviour. All other hosts keep the existing host rules (sign-in hosts etc.).

Phase LOBBY (before the quiz page `/courses/C/K/I/take` or, for assignments, before any `/courses/C/assignments/I` page has loaded after a successful sign-in):
- allowed: `/`, `/login`, `/login/**`, `/courses/C`, `/courses/C/quizzes`, `/courses/C/assignments`, `/courses/C/K/I`, `/courses/C/K/I/**`
- everything else blocked.

Phase QUIZ (after the first main-frame load of `/courses/C/K/I/take` (classic) or of any `/courses/C/assignments/I...` page (new quizzes/assignments)):
- allowed: `/courses/C/K/I`, `/courses/C/K/I/**`, `/login` and `/login/**` on the exam host (session renewal), plus sign-in hosts and the quiz-lti hosts.
- if `open_book`: also `/courses/C/pages/**`, `/courses/C/modules/**`, `/courses/C/files/**`, `/courses/C/wiki/**`, `/files/**` (downloads stay blocked).
- everything else blocked.

Always blocked on the Canvas host, in every phase and with open_book on: `/conversations/**`, `/inbox/**`, `/profile/**`, `/groups/**`, `/calendar/**`, `/courses/<other id>/**`, `/accounts/**`, `/api/**`, `/users/**`.

A blocked main-frame navigation is cancelled, records `blocked_navigation`, and shows a short in-app message ("This page is not available during the exam."). A popup obeys the same rules.

Other behaviour:
- After a successful sign-in the app lands on the dashboard `/` or `/courses/C`: open the exam link once automatically (one auto-open per attempt). The strip gets a "Go to my quiz" button that opens the exam link, and a "Back" button (back only within allowed pages).
- If `canvas_scope` is absent the app keeps the earlier behaviour (any quiz or assignment path on the exam's Canvas host).
- Exact-host rules, https only, popup handling, finish detection and data clearing are unchanged.

### Tests

Pure policy function (host, path, phase, open_book) with a table of allowed and blocked URLs, including look-alike paths (`/courses/12`, `/courses/123/quizzes/4567`, `/courses/123/quizzes/456x`, trailing slashes, encoded slashes, `..` segments, uppercase, query/fragment tricks).

## No access code, lock-in, platform gate (2026-10-26)

Owner decision: Canvas mode has no access code. The teacher builds the quiz in Canvas with no code. FairPlay guarantees only that the student can reach only that course and that quiz, and once in the quiz cannot leave before submitting.

### Lock-in (apps implement it)

After the quiz page has loaded (phase QUIZ above), the student has no way to end the exam session except:

1. `finish_url_regex` matches (the submission is done), or
2. the teacher or proctor unlocks or hands the attempt in from the server side, or
3. the existing emergency/safety unlock.

There is no student-facing exit or "I'm done" button in QUIZ phase of a Canvas exam. "Back" and "Go to my quiz" only move within the exam's item. Closing the window, quitting the app and sign-out are not exits: they follow the existing resume rules.

When the app enters QUIZ phase it reports the integrity event `canvas_quiz_started` (stored as `client_canvas_quiz_started`, shown to the teacher, not a flag).

Teacher page wording: "Students sign in to Canvas inside FairPlay and can only open this course and this quiz. Once the quiz is started they cannot leave until they submit."

### Platform gate

A Canvas exam starts only from an app that has the restricted Canvas browser and the lock-in. Per platform (`mac`, `windows`, `ipad`; `ios` is read as `ipad`) the server has a minimum Canvas app version: `FAIRPLAY_CANVAS_MIN_VERSION_MAC` (default `0.7.2`), `FAIRPLAY_CANVAS_MIN_VERSION_WINDOWS` and `FAIRPLAY_CANVAS_MIN_VERSION_IOS` (default empty = not supported yet). A value must look like `0.7.2` (a leading `v` is dropped); anything else stops the server at startup.

- Platform with a minimum and an app below it (or with no readable version): 426 with `min_app_version`, like the normal update gate.
- Platform with no minimum, a browser, or an unknown platform: 409 "Canvas exams are not available on this device yet".

Apps 0.7.0 and 0.7.1 still carry the removed code typer and no lock-in, so the Mac minimum is the release that ships this contract.

## Appendix: removed 2026-10-26 (access code)

The first design held a Canvas access code on the server and had the app type it. It was dropped by owner decision and nothing in the product uses it now:

- Fields `canvas.access_code_source` and `canvas.access_code`, and `canvas_access_code` on `POST /api/exams/{id}/open`; old clients may still send them and the server ignores them.
- Managed set and clear of the code in Canvas on open and close, the 409 `canvas_code_failed`, `canvas.code_status`, `canvas.remove_code_in_canvas`. Opening an exam no longer calls Canvas or needs a linked Canvas account.
- `POST /api/student/attempts/{aid}/canvas-code`, its HMAC proof, replay and rate limits, and the events `canvas_code_issued`, `canvas_code_refused`, `canvas_code_failed`.
- The native probe for `input#quiz_access_code, input[name=access_code]` and the code fill.
- At startup the server clears any stored code (`canvas_code_enc`) without calling Canvas. A code that an earlier test build left on a Canvas quiz has to be cleared by the teacher in Canvas.
