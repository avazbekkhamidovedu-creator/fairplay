# University browsing and trusted exam admission

Status: implementation design plus unused validation primitives, not a deployed feature.
Reviewed against `release/0.7.9` on 2026-11-03. Preserve all current Canvas Mode B workflows.

## The simple experience

- University IT connects its LMS once and completes a compatibility test. Students do not enter API keys, allowlists, regexes, quiz URLs, or per-exam access codes.
- A student chooses their university once, signs in normally, and browses courses in FairPlay without exam lockdown.
- A protected exam opens a question-free preparation screen. Starting it activates lockdown, checks that lockdown succeeded, and only then opens the questions.
- Submission is confirmed by the LMS server before ordinary browsing returns. Emergency exit revokes admission; it must never silently mark an unfinished exam submitted.

One-time setup does not mean every assessment is automatically protected. University policy or an instructor must select which assessments require FairPlay. This selection should happen in the LMS, not by copying each link into a second dashboard. A default policy can protect all assessments in designated courses.

## What the repository already supports

- `server/app/canvas_mode.py` stores one Canvas quiz/assignment scope, login hosts, New Quizzes hosts and completion hints. Current SHA: `64994486d3e6cf63ee3fea8fc62977fedd766c1e`.
- `server/app/routes_lms.py` connects teacher credentials, imports rosters and pushes grades. It does not provide a university launch service or enforce Canvas question delivery. Current SHA: `55021e74fa028092d98975baaf3c0ae5f2e5fb6f`.
- `server/app/browser_gate.py` explicitly documents that its header markers can be forged. Current SHA: `68f4c49cdb0dbbf0d30c5ef6ed21ce61e158e1d2`.
- `server/app/app_client.py` signs application challenges with a compiled HMAC key and binds device/session state. Its own threat model excludes a determined reverse engineer who extracts that key. Current SHA: `340e91351e03ff2bc3ae1113f42eb2aa2b3d7245`.
- Existing native `openExternal` is restricted to the FairPlay origin/main frame and locks before opening the external exam. Retain this trust boundary; never expose that bridge to arbitrary LMS JavaScript.

Sources: [Canvas mode](https://github.com/avazbekkhamidovedu-creator/fairplay/blob/release/0.7.9/server/app/canvas_mode.py), [LMS routes](https://github.com/avazbekkhamidovedu-creator/fairplay/blob/release/0.7.9/server/app/routes_lms.py), [application proof](https://github.com/avazbekkhamidovedu-creator/fairplay/blob/release/0.7.9/server/app/app_client.py).

## Minimum university setup

Store a server-owned university profile containing university ID, exact HTTPS portal origin, exact FairPlay origin, narrowly approved browsing/login origins, adapter identity and configuration revision. Separate browsing origins from exam resource permissions. Do not treat SSO/CDN domains as authorization issuers. The bundled validator intentionally accepts only ASCII DNS origins and the standard HTTPS port; support for private deployments, Unicode domains or custom ports requires an explicit reviewed extension, not silent URL normalization.

IT installs/registers a supported launch adapter, verifies its issuer/client/deployment and signing keys, grants only needed permissions, chooses protected course/exam policy, and runs the bypass tests below. A browser-only JavaScript installation is insufficient. Keep credentials server-side. Registration and new persistent permissions require the appropriate administrator approvals.

An LMS connection is ready for protected university browsing only when the **actual LMS delivery layer** refuses question pages, APIs, embedded resources, previews and attempts until a valid FairPlay admission exists. A stored `enabled=true` flag, a passing URL validator or an instructor checkbox is not evidence this gate exists.

## Proposed trusted start contract

These are future adapter requirements, not currently implemented endpoints:

1. The LMS authenticates the student and resolves a protected resource. It supplies a verified server-to-server/LTI launch to FairPlay, bound to university, subject, resource, deployment and a short-lived nonce. Validate signature, allowed algorithm/key, issuer, audience, login state, nonce and enrollment. Never infer a student from an unsigned query parameter or display name. LTI role/resource claims do not replace authorization checks.
2. FairPlay maps that external resource to server-owned exam configuration and creates a pending attempt. Its preflight page contains no questions or usable LMS admission. The native app obtains the existing challenge/device binding. It does not accept arbitrary return URLs, question URLs, allowlists or lock policies supplied by an LMS page.
3. The native app locks first. If any required lock check fails, the attempt stays pending and no question navigation is performed. It sends a fresh proof bound to university, student, exam, attempt, app session, device, profile revision and challenge. This must be a new domain-separated proof, not the legacy `start|nonce|device_id` proof repurposed as evidence of successful lockdown.
4. FairPlay checks the pending state, proof, revision, device/session and time window, and atomically consumes the nonce with the transition to active. Two workers must not issue two admissions from the same nonce. Retries use an idempotency key scoped to the authenticated context; they do not mint a new attempt or widen access.
5. The LMS gate receives a one-use admission through its authenticated adapter, bound to the same LMS user/resource/attempt and device-backed session. It must reject admission replay in another browser/session. Do not place a bearer token in a URL, browser history, localStorage or an arbitrary JavaScript bridge. A generic quiz password automatically typed into a page is not equivalent to device-bound admission.
6. Only after that gate succeeds does native code navigate to the server-resolved question location, preserving the existing Canvas scoping and native lock checks. Check navigation actions and final responses for main frames and subframes; handle SPA/XHR/prefetch at the LMS gate, not just WebView top-level navigation.
7. Continue short-lived admission with authenticated locked heartbeats. Expired, revoked, lost-lock and ended sessions cannot fetch new questions or submit unauthorized changes. The app must shield already-loaded content on lock loss; the server cannot retract information already delivered. Define an offline policy explicitly rather than failing open. Submission confirmation is server-authenticated. A URL containing `submitted` is only a UI hint, not proof that LMS submission succeeded.

`server/app/university_profile.py` implements only strict profile syntax and semantic binding checks for step 4. It deliberately does not verify signatures, consume nonces, attest device state, expose routes, configure a university, or issue admission. Its input is already-verified claims plus server-owned context. External LMS subjects must map to stable internal opaque identifiers rather than being normalized lossy into these identifiers.

## Canvas feasibility boundary

LTI 1.3 provides a signed resource launch. It is not by itself a restriction on an unrelated, directly accessible native Canvas quiz. The inference for FairPlay is that adding an LTI launch button cannot prove the native quiz is unavailable elsewhere. Canvas documents quiz access-code, IP and institution-provided lockdown options; compatibility with FairPlay must be implemented and demonstrated for Classic Quizzes, New Quizzes and assignments separately. Do not claim FairPlay can simply use another vendor's proprietary lockdown integration.

Primary references: [LTI 1.3 launch specification](https://www.imsglobal.org/spec/lti/v1p3), [Canvas quiz restriction options](https://community.instructure.com/en/kb/articles/660995-what-options-can-i-set-in-a-quiz), [Canvas question controller](https://github.com/instructure/canvas-lms/blob/master/app/controllers/quizzes/quiz_submission_questions_controller.rb).

If a university's Canvas deployment offers no enforceable admission extension for its chosen assessment type, keep the current explicit FairPlay-first Canvas workflow or deliver the assessment through a controlled tool. Do not enable unprotected native assessment browsing and describe it as secure automatic detection. Supporting another LMS requires its own gate/adapter, not another URL pattern.

## Required acceptance tests before enabling

- Direct exam/question/API URLs in Chrome, copied deep links, a second browser with the student's normal LMS login, preview routes, alternate/mobile APIs and cached/prefetched questions cannot bypass admission.
- Before native lock succeeds, no question bytes or reusable admission tokens arrive anywhere in the WebView, including subframes or background requests. Failed lock never opens questions.
- Expired, replayed, wrong-school, wrong-user, wrong-exam, wrong-device, wrong-session and stale-profile launches fail closed. Concurrent redemption has exactly one state transition. Invalid signatures/algorithms/keys and duplicate JSON claims are rejected.
- Foreign origins, popups, frames and redirects cannot invoke the native bridge or substitute exam configuration. SSO still works without acquiring exam privileges.
- Back/Forward, duplicate Start, crash/relaunch, app sleep, network loss, revoked permissions, emergency exit and late submission preserve attempt/session integrity.
- Existing explicit Canvas Mode B, authentication, roster sync, grades and non-university exams remain unchanged. Mac signed build plus live university staging tests are required; local pure-Python tests do not prove WebKit or LMS behavior.

## Local verification

Run `cd server && python -m unittest discover -s tests -p test_university_profile.py -v` in this isolated artifact. Fourteen tests pass, covering semantic bindings, time limits, strict origins, profile mismatch and browser-controlled overrides. This is focused validator coverage only. No server integration, live Canvas, macOS runtime, notarized release or end-to-end admission tests have run for this artifact.


## Dormant native prototype profile

An additive native policy prototype can consume `university_id`, `portal_origin`, `fairplay_origin`, `browse_origins`, `adapter_id`, `revision` and `protected_launches` (objects containing `launch_url` and internal `exam_id`). `validate_protected_launches` canonicalizes and checks this server-owned mapping. The native policy matches exact launch URLs and derives the existing FairPlay preflight route from the exam ID; it must not accept a general redirect URL. The producer is an authenticated FairPlay configuration service, never the university page's JavaScript. No such service or gate-readiness verification is implemented in this artifact. Keep the prototype disabled and unwired until actual gate verification exists. This permits testing browse-to-preflight-to-lock behavior without claiming arbitrary real LMS browsing is safe.


### Server/native pilot compatibility

The server emits a conservative subset accepted by the native pilot: 1–32 unique exact HTTPS DNS browse origins, never the FairPlay origin; 1–1,000 unique protected launches on the configured portal; literal `/courses/<1–20 ASCII digits>/(quizzes|assignments)/<1–20 ASCII digits>` paths without query, fragment, encoded characters or trailing slash; ASCII alphanumeric/underscore/hyphen internal exam IDs of 1–128 characters. Native consumers may accept additional harmless normalization, but server output never depends on it. Generic LMS route shapes are not supported by this Canvas pilot. Future adapters require a versioned contract extension and matching native tests.
