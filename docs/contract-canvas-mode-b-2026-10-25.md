# Canvas mode (Mode B): contract, 2026-10-25

A Canvas exam is an existing website exam (`mode: "external"`) with `external_kind: "canvas"`. The teacher builds the quiz in Canvas; students can only start it inside the FairPlay lock because the Canvas access code is held by the server and typed by the app.

## Exam fields (teacher POST/PUT, external mode only)

```
"external_kind": null | "canvas"
"canvas": {
  "quiz_type": "classic" | "new" | "assignment",
  "access_code_source": "managed" | "manual",
  "access_code": "..."            // write-only, manual only, 4-64 chars, never returned
}
```
- `external_url` is the quiz or assignment link. Host must be https. `quiz_type` defaults from the URL (`/courses/:c/quizzes/:q` is classic, `/courses/:c/assignments/:a` is new).
- `allowed_domains` is built on the server: the Canvas host (exact), the quiz-lti hosts for `new` (`*.quiz-lti-*.instructure.com` form is expanded to a plain list the apps already understand), and the configured sign-in hosts (`FAIRPLAY_CANVAS_SSO_HOSTS`, default Duo, Microsoft login, DKU SSO). Teacher additions are kept. Every host passes `site_policy`.
- `finish_url_regex` (new, server-filled when `external_kind` is canvas, teacher can override): classic `/quizzes/\d+/(submissions/\d+|history)`, new `submission-confirmation|submission-screen`, assignment `/assignments/\d+/submissions/\d+`. `finish_url_contains` is ignored for canvas exams. Max 300 chars, the plain regex subset Python and ICU read the same (details in CONTRACT.md), 422 otherwise.
- `managed`: the server sets a random 24-char code (A-Za-z0-9, no look-alikes) on the Canvas quiz through the teacher's linked Canvas token (Classic: `PUT /api/v1/courses/:c/quizzes/:q` with `quiz[access_code]`), when the exam opens, and clears it when the exam closes. If the token is missing or Canvas refuses, the exam cannot open (409 `{"code":"canvas_code_failed","message":...}`) and the teacher is told to switch to `manual`. New Quizzes has no verified API for this, so `new` + `managed` falls back to `manual` with a clear message.
- `manual`: the teacher types the code they set in Canvas. Stored encrypted (`lms/secretbox`), never returned by any endpoint.

Student and teacher exam views add `external_kind`, `canvas: {quiz_type, access_code_source}` and `finish_url_regex`. Never the code.

## Code delivery (native app only)

`POST /api/student/attempts/{aid}/canvas-code` body `{"proof": "<hmac>", "ts": 123}`.
- Requires the normal app session headers and `proof = HMAC(app_key, "canvascode|<aid>|<session>|<device_id>|<ts>")`, same scheme as `beat`. A web page without the app key gets 403 `APP_PROOF_DETAIL`.
- Only while the attempt is open, the exam is live, the attempt is bound to this device and session, and the exam is a canvas exam.
- Returns `{"code": "...", "valid_for_s": 60}`. At most 6 issues per attempt per 10 minutes, and max(300, 3 x students) per exam per minute, counted after the proof verified. Every call writes an integrity event `canvas_code_issued`, and a refused one `canvas_code_refused`.
- The exam page's JavaScript never receives the code: only native calls this endpoint and types the code into the Canvas page.

## openExternal bridge action (page to app), additions

```
{"action":"openExternal", "attempt_id":..., "url":..., "allowed_domains":[...],
 "kind":"canvas", "finish_url_regex":"...", "session":...}
```

## Native behaviour for kind "canvas" (Mac, iPad, Windows)

1. Navigation: https only, exact host or listed subdomain match; scheme and host checked on every main-frame navigation and redirect; blocked ones record `blocked_navigation`.
2. Popups: `window.open` to an allowed host opens in a child web view that shares the cookie store with the main view (so sign-in popups that need `window.opener` still work); anything else is blocked.
3. Access code: after every main-frame load, run a probe for `input#quiz_access_code, input[name=access_code]`. If found, call the code endpoint, set the value, fire `input` and `change`, enable and click the form's submit button, and never log the code. At most 3 fills per attempt, then show "Ask your teacher for help" and record `canvas_code_failed`.
4. Finish: when the page URL matches `finish_url_regex` (or contains `finish_url_contains`), wait 2 s, then run the existing finish flow (`external-done`). Sign-out of Canvas happens after finish: clear the web data store.
5. Downloads, file panels, DevTools and context menu stay off, as in existing website mode. Canvas file-upload questions use the existing early-upload / file rules; if the app cannot show a file panel, record `canvas_upload_unsupported` and tell the teacher in the exam view.
6. Heartbeats, events, hand-in, resume and device swap are unchanged.

## Events (integrity)

`canvas_code_issued`, `canvas_code_refused`, `canvas_code_failed`, `canvas_upload_unsupported`, `canvas_sso_popup`. They are client-reportable as `client_<type>` where the app writes them.

## Not in scope

DKU OJ code, the DKU OJ bridge, LTI 1.3 launch (later), changing main on the repos.
