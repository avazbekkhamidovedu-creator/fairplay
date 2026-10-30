# Proctora slice 1 — shared contract

Everything in this file is binding for `server/`, `web/` and `mac/`. Do not invent
endpoints or fields outside it; if something is missing, add it here first.

## Layout

```
proctora/
  CONTRACT.md
  server/            FastAPI app (Python 3.11+, SQLAlchemy 2 async, asyncpg/aiosqlite, redis)
    app/main.py      `uvicorn app.main:app` from server/
    app/...
    static/          built web UI goes here (copied from web/dist)
    tests/           pytest, uses fastapi TestClient (httpx)
    requirements.txt
  web/               React 18 + TypeScript + Vite, react-router
  mac/               Swift app (built on the Mac)
```

## Scale requirements (binding)

Target: many universities at once — 10,000+ students writing concurrently across
hundreds of live exams. Every student sends a heartbeat every 5 s and answer
saves every few seconds, so the hot path is ~3,000+ writes/s at peak.

- **Stateless API servers.** No in-process state that matters (sessions, rate
  limits, live state) — any request can hit any instance. Run N uvicorn workers /
  N containers behind a load balancer.
- **Database: PostgreSQL in production** via SQLAlchemy 2.0 async (asyncpg).
  The SAME code must also run on SQLite (aiosqlite) for local single-laptop dev.
  Select by env `DATABASE_URL` (default `sqlite+aiosqlite:///./data/proctora.db`).
  Proper indexes on every foreign key and on (exam_id, student_id).
  Connection pool sized via env.
- **Hot path off the database.** Heartbeats and live-room state go to **Redis**
  (env `REDIS_URL`); if `REDIS_URL` is unset, fall back to an in-memory store
  (dev only, single process). Postgres only gets durable writes: answers,
  submits, integrity events (batched inserts).
- **Answer saves are idempotent upserts** keyed by (attempt_id) with client_seq
  compare — one row update per save, never read-modify-write races.
- **Rate limits and wrong-code counters** in Redis (memory fallback in dev).
- **Exam content cached** (questions without keys) per exam in Redis/memory,
  invalidated on edit/open.
- **Live room reads** come from Redis, never a full DB scan per poll.
- **All endpoints async**, no blocking calls.
- **Load test** (`server/loadtest/`): async script simulating N students
  (default 2,000) across M exams (default 50): login, start, heartbeat every 5 s,
  answer save every 3 s, submit. Reports p50/p95/p99 latency and error rate.
- **Deployment files**: `docker-compose.yml` with postgres, redis and the API
  (4 workers) + a 2-replica example; `Dockerfile`.

## Serving

- Server listens on `http://127.0.0.1:8000`.
- `GET /api/...` = JSON API.
- Web UI is ONE Vite app served by the server from `server/static`:
  - `/teacher/*` → teacher UI, `/student/*` → student UI (SPA fallback: any non-API, non-file path returns `static/index.html`).
  - Vite `base: "/"`. Client routes: `/teacher/login`, `/teacher`, `/teacher/exams/new`, `/teacher/exams/:id`, `/student/login`, `/student`, `/student/exam/:attemptId`, `/student/done/:attemptId`. `/` redirects to `/teacher`.
- CORS: allow all origins (dev).

## Auth

- `POST /api/auth/login` body `{"username": str, "password": str}` → `200 {"token": str, "role": "teacher"|"student", "name": str}`; wrong → `401 {"detail": "Invalid username or password"}`.
- All other endpoints need header `Authorization: Bearer <token>`. Missing/invalid → 401. Wrong role → 403.
- Tokens: signed and stateless (HMAC-SHA256 over user id + role + expiry, secret from env `PROCTORA_SECRET`, 12 h expiry) so no DB lookup per request.
- Seed users (created on startup if absent; passwords hashed with hashlib.pbkdf2_hmac sha256 + salt):
  - teacher `teacher` / `teacher123`, name "Demo Teacher"
  - students `alice` / `alice123` (name "Alice Chen"), `bob` / `bob123` (name "Bob Karimov"), `carol` / `carol123` (name "Carol Wang")
- `GET /api/me` → `{"username","name","role"}`.

## Data shapes

Question (teacher view):
```json
{"id": "q_xxx", "type": "mcq" | "numeric" | "short",
 "prompt": "text", "points": 1,
 "options": ["A text", "B text"],       // mcq only, 2-8 items, else []
 "correct_index": 1,                      // mcq only, else null
 "answer_number": 3.14, "tolerance": 0.01,// numeric only, else null
 "accepted_answers": ["paris"]            // short only, else []
}
```
Question (student view) = same minus `correct_index`, `answer_number`, `tolerance`, `accepted_answers`.

Exam (teacher view):
```json
{"id": "e_xxx", "title": "str", "duration_min": 30,
 "status": "draft" | "live" | "closed",
 "start_code": "4729" | null,             // set while live
 "questions": [Question...], "created_at": "ISO8601Z",
 "question_count": 5, "total_points": 10}
```

Attempt status: `"in_progress" | "submitted"`.

Answer values: mcq → integer option index; numeric → number; short → string. Missing = not answered.

## Teacher API (role teacher)

- `GET /api/exams` → `[Exam...]` newest first (questions included).
- `POST /api/exams` body `{"title","duration_min","questions":[Question without id]}` → `201 Exam` (status draft, ids generated). Validation → 422.
- `GET /api/exams/{id}` → Exam. 404 if missing.
- `PUT /api/exams/{id}` same body as POST → Exam. Only allowed when status draft, else `409 {"detail":"Exam is not a draft"}`.
- `DELETE /api/exams/{id}` → 204 (draft or closed only, else 409).
- `POST /api/exams/{id}/open` → Exam with status live and a new random 4-digit `start_code` (no leading-zero issues: string). Allowed from draft or closed.
- `POST /api/exams/{id}/close` → Exam status closed, start_code null. All in_progress attempts become submitted (auto) with `auto_submitted: true`.
- `GET /api/exams/{id}/live` →
```json
{"exam_id","status","start_code","duration_min",
 "students":[{"attempt_id","username","name","status",
   "started_at","submitted_at","deadline_at",
   "answered":3,"question_count":5,"current_question":2,
   "locked":true,"last_heartbeat_at":"ISO"|null,"online":true,
   "event_count":4,"last_event":"blocked_shortcut Cmd-key48"|null}]}
```
  `online` = last heartbeat within 15 s. Students listed = those with an attempt.
- `GET /api/exams/{id}/results` →
```json
{"exam_id","title","total_points","average_percent": 72.5,
 "attempts":[{"attempt_id","username","name","status","auto_submitted",
   "score":7,"total_points":10,"percent":70.0,"submitted_at",
   "receipt":"K7Q2-M9XD",
   "answers":[{"question_id","given":<value|null>,"correct":true,"points_awarded":1}],
   "events":[{"type","detail","client_time","server_time"}]}],
 "questions":[{"question_id","prompt","type","percent_correct": 50.0}]}
```

## Student API (role student)

- `GET /api/student/exams` → live exams plus exams this student has an attempt in:
```json
[{"id","title","duration_min","question_count","status",
  "attempt": null | {"attempt_id","status","submitted_at","receipt"}}]
```
- `POST /api/student/exams/{id}/start` body `{"start_code": "4729"}`:
  - exam not live → 409 `{"detail":"Exam is not open"}`; wrong code → 403 `{"detail":"Wrong start code"}` (count failures per student, after 5 wrong in a row → 429 `{"detail":"Too many wrong codes, wait 60 seconds"}` for 60 s).
  - Already has in_progress attempt → return it (resume). Already submitted → 409 `{"detail":"Already submitted"}`.
  - → `200 {"attempt_id","exam_id","title","duration_min","started_at","deadline_at","server_time","questions":[Question student view], "answers":{"q_id": value}, "status"}`
- `GET /api/student/attempts/{aid}` → same shape as start response (resume after reload). 404 if not this student's.
- `PUT /api/student/attempts/{aid}/answers` body `{"answers":{"q_id":value}, "client_seq": int}` → `{"saved": true, "client_seq": int, "server_time"}`. Ignore (but still 200, `saved:false`) if client_seq <= stored seq. After deadline or submitted → 409.
- `POST /api/student/attempts/{aid}/heartbeat` body `{"current_question": int, "locked": bool}` → `{"ok": true, "status", "server_time", "deadline_at"}`. (If exam was closed, status comes back submitted so the client finishes.)
- `POST /api/student/attempts/{aid}/events` body `{"events":[{"type": str, "detail": str, "client_time": "ISO"}]}` → `{"ok": true, "stored": n}`. Allowed even after submit.
- `POST /api/student/attempts/{aid}/submit` body `{"answers":{...}, "client_seq": int}` → `{"status":"submitted","submitted_at","receipt": "XXXX-XXXX"}`. Idempotent: second call returns same receipt. Late (after deadline + 60 s grace) still accepted but marked `late` internally (no field needed in slice 1).
- Scores are NOT returned to students in slice 1.

## Grading

- mcq: given == correct_index → full points.
- numeric: abs(given - answer_number) <= tolerance (tolerance default 0) → full points.
- short: normalize (strip, lowercase, collapse spaces) and compare with each accepted answer normalized → full points.
- Otherwise 0. Graded at submit time and recomputed on results read.

## Native bridge (student UI inside the Mac app)

The Mac app loads `http://127.0.0.1:8000/student/login` in a WKWebView.

- Detect: `window.webkit?.messageHandlers?.proctora` exists → running inside the Mac app.
- JS → native: `window.webkit.messageHandlers.proctora.postMessage(msg)` with:
  - `{"action":"lock"}` — call right after a successful start, before showing questions.
  - `{"action":"unlock"}` — call after submit succeeded (and when heartbeat says submitted).
  - `{"action":"hello"}` — on page load; native answers with its state.
- Native → JS: `window.dispatchEvent(new CustomEvent("proctora-native", {detail}))` with detail:
  - `{"type":"state","locked":bool,"trusted":bool,"version":"0.3"}`
  - `{"type":"locked"}` / `{"type":"lockFailed","reason":"accessibility"}` / `{"type":"unlocked"}`
  - `{"type":"event","event":{"type":"blocked_shortcut","detail":"Cmd-key48","client_time":"ISO"}}` — JS forwards these to `/events` and includes `locked` in heartbeats.
- Outside the Mac app (plain browser), the student UI still works but shows a yellow banner "Not in locked mode — use the Proctora Mac app for real exams" and sends `locked:false`.

## Student UI behaviour (offline-first)

- Answers saved to `localStorage["proctora:attempt:u<account id>:<aid>"]` = `{answers, client_seq, synced, owner}` (audit 2026-10-13: one account never loads another's copy; sign-out drops copies the server already has and old unowned offline copies) on every change (client_seq++), synced via PUT `/answers` debounced 1 s and retried every 3 s while unsynced. Header shows "All answers saved" (synced) / "Saved on this computer" (pending).
- Heartbeat every 5 s.
- Timer counts down to `deadline_at` using server_time offset. At 0 → auto submit.
- Submit: confirm screen listing unanswered count → POST submit → native unlock → `/student/done/:aid` shows receipt.

---

# Slice 2 — design look, essays, grading, gradebook, student grades

Everything above stays valid unless changed here. Slice 2 additions are binding.

## Exam fields (added)

Exam (teacher view) gains:
```json
{"course": "MATH 201",            // str, default "", max 64
 "room": "AB 1087",               // str, default "", max 64
 "scheduled_at": "ISO8601Z"|null, // planned start, informational only
 "shuffle_questions": false,      // per-student order, deterministic per attempt
 "grades_released": false,
 "released_at": "ISO8601Z"|null,
 "closed_at": "ISO8601Z"|null,
 "pending_grading": 0}            // essay answers in submitted attempts not yet graded
```
POST/PUT body accepts `course`, `room`, `scheduled_at`, `shuffle_questions` (all optional).
Existing rows: defaults above (add columns; create_all on a fresh DB is fine, dev DB may be wiped).

## Question types (added)

- `"essay"`: `{"type":"essay","prompt","points","rubric":[{"label":"Two clear reasons","points":10}],"min_words":null|int,"max_words":null|int}`.
  `rubric` may be `[]`; if non-empty its points must sum exactly to `points` (else 422). Essays are never auto-graded.
  All other type-specific fields null/[] for essay; `rubric`/`min_words`/`max_words` are `[]`/null for non-essay types.
- True/false is NOT a new type: the UI creates an `mcq` with options `["True","False"]`.
- Student view of essay includes `rubric`, `min_words`, `max_words` (students see how it is graded).
- Essay answer value: string (plain text, max 50,000 chars — raise the slice-1 10,000 limit for essays only, or globally to 50,000).

## Grading model

Each submitted attempt has teacher grades per question: `{"points": num, "comment": str, "rubric_scores": [num]|null, "graded_at": ISO}` (stored as JSON on the attempt, column `grades`), plus `overall_comment` (str).

Per question:
- If a teacher grade exists → its points (an override; allowed on ANY question type).
- Else essay → if answer missing/blank: 0 and counts as graded; else **pending** (0 for now, not graded).
- Else auto grade (slice 1 rules).

Attempt: `score` = sum; `needs_grading` = number of pending essay answers; `fully_graded` = needs_grading == 0.
Percent = score / total_points * 100 rounded to 1 decimal (0 if total 0).

## Teacher API (added / changed)

- `GET /api/exams` items and `GET /api/exams/{id}` include the new fields.
- `POST /api/exams/{id}/extend` body `{"minutes": 1..120}` → Exam. Only when live (else 409). Adds minutes to `duration_min` AND to `deadline_at` of every in_progress attempt; heartbeats then return the new deadline (invalidate any cached attempt metadata).
- `GET /api/exams/{id}/live` gains:
  - each student: `"needs_look": bool` (has open flags)
  - `"flags": [{"event_id": int, "attempt_id", "username", "name", "type", "detail", "server_time"}]` — newest first, max 50, only `review == "open"` and type NOT in {"lock_started","lock_ended"}.
  - `"counts": {"writing": n, "submitted": n, "offline": n, "needs_look": n}` (offline = in_progress and not online).
- `POST /api/exams/{id}/events/{event_id}/review` body `{"status": "dismissed"|"noted", "note": "str (optional, max 500)"}` → `{"event_id","review","note"}`. 404 if event not in this exam.
- `GET /api/exams/{id}/results` changes:
  - top level adds `"course","grades_released","released_at","median_percent","pending_grading","submitted_count","attempt_count","flags_total","flags_open"`, and `"score_buckets": [{"label":"0–9","count":n}, … 10 buckets of percent, last "90–100"]` over submitted attempts.
  - each attempt adds `"needs_grading": n, "overall_comment": str, "flag_count": n`.
  - each answer adds `"max_points", "manual": bool` (teacher grade exists), `"pending": bool`, `"comment": str`, `"rubric_scores": [num]|null`. For pending essays `correct` is false and `points_awarded` 0. `correct` = points_awarded == max_points.
  - each event adds `"event_id","review","note"`.
  - questions add `"points"` and `"avg_points"`; `percent_correct` for essays = average awarded / points * 100 over graded essay answers (null if none graded).
- `GET /api/exams/{id}/grading` → grading queue for one exam:
```json
{"exam_id","title","course","status","grades_released",
 "questions":[{"question_id","index":0,"type","prompt","points","rubric":[...],
   "to_grade": n,     // essay: pending count; short: wrong auto answers without a teacher grade; others 0
   "graded": n}],     // answers of this question with a teacher grade
 "items":[{"attempt_id","anon_id":"#A7F2","username","name","question_id",
   "answer": <value|null>, "word_count": n|null, "auto_points": num|null,
   "grade": null|{"points","comment","rubric_scores","graded_at"},
   "flag_count": n}]}
```
  Items = for every submitted attempt: each **essay** answer (answered or not) and each **short** answer that was auto-graded wrong. Ordered by question index, then attempt started_at. `anon_id` = "#" + first 4 chars of sha256(attempt_id) uppercased.
- `PUT /api/exams/{id}/attempts/{aid}/grades/{qid}` body `{"points": num, "comment": "str", "rubric_scores": [num]|null}` → `{"attempt_id","question_id","grade":{...},"score","total_points","percent","needs_grading"}`.
  Rules: attempt must be submitted (409 otherwise); 0 <= points <= question points (422); if rubric_scores given: same length as rubric, each 0..criterion points, and `points` must equal their sum (422).
- `DELETE /api/exams/{id}/attempts/{aid}/grades/{qid}` → same response shape with `"grade": null` (back to automatic).
- `PUT /api/exams/{id}/attempts/{aid}/comment` body `{"comment": "str max 2000"}` → `{"attempt_id","overall_comment"}`.
- `POST /api/exams/{id}/release` body `{"force": false}` → Exam. Exam must be closed (409 `{"detail":"Close the exam first"}`). If pending_grading > 0 and not force → 409 `{"detail":"<n> answers still need grading"}`.
- `POST /api/exams/{id}/unrelease` → Exam with grades_released false.
- `GET /api/grading` → `[{"exam_id","title","course","status","pending": n,"submitted": n,"grades_released"}]` for this teacher's exams that have submitted attempts, newest first.
- `GET /api/gradebook?course=` →
```json
{"courses":["MATH 201", ...],
 "exams":[{"exam_id","title","course","total_points","status","grades_released"}],   // oldest first
 "students":[{"username","name","average_percent": num|null,
   "cells":{"<exam_id>":{"state":"graded"|"needs_grading"|"in_progress"|"missing"|"not_taken",
                         "score": num|null,"percent": num|null}}}]}
```
  Students = all users with role student (sorted by name). State: submitted & fully graded → graded; submitted & pending → needs_grading (score = current partial); in_progress → in_progress; no attempt & exam closed → missing; no attempt & exam not closed → not_taken. average_percent over graded cells only.
- `GET /api/gradebook.csv?course=` → `text/csv` with header `Student,Username,<exam titles...>,Average %`, cells = percent or state word. Auth via Bearer like everything else.

## Student API (added / changed)

- `GET /api/student/exams` items add `"course","room","scheduled_at"`; `attempt` adds `"released": bool, "percent": num|null` (percent only when released).
- Start/attempt view: questions in per-student shuffled order when `shuffle_questions` (seeded by attempt_id, stable across reloads); essay questions include `rubric`, `min_words`, `max_words`. Response adds `"course"`.
- `GET /api/student/grades` → submitted attempts of this student, newest first:
```json
[{"exam_id","title","course","submitted_at","released": bool,
  "score": num|null,"total_points","percent": num|null}]   // score/percent null unless released
```
- `GET /api/student/grades/{exam_id}` → 403 `{"detail":"Grades not released yet"}` unless released; 404 if no submitted attempt. Else:
```json
{"exam_id","title","course","submitted_at","receipt","score","total_points","percent",
 "class_average_percent","overall_comment",
 "questions":[{"question_id","index","type","prompt","options":[...],"points","points_awarded",
   "status":"correct"|"partial"|"wrong"|"unanswered",
   "given": <value|null>,
   "correct_answer": <mcq index | number | first accepted string | null for essay>,
   "tolerance": num|null,
   "comment": str, "rubric":[...], "rubric_scores":[num]|null}]}
```
  Questions in the exam's original order.

## Web UI (slice 2)

Look follows the design canvas "Exam App Screens": font **Plus Jakarta Sans** (self-hosted via `@fontsource/plus-jakarta-sans`, NO Google Fonts — must work in China) and **JetBrains Mono** (`@fontsource/jetbrains-mono`) for codes/timers. Tokens (in `web/src/styles/tokens.css`): ground #F5F6F9, surface #FFFFFF, border #E3E6EE, divider #F0F2F6, text #14161F, text-2 #3A3F4E, muted #565B6B, primary #2F4BD8 (hover #1F35A8, tint #E9EDFC, tint-2 #F2F4FE), success #12704A (tint #E6F4EC), warn #B4470B (dark #8A3508, tint #FDEEE3, warn-border #F3C9AC, warn-bg #FFF8F3), dark #14161F / #262938 / #4A4E60, radius 10 (controls) / 14–16 (cards), touch targets ≥ 44 px.

Teacher routes: `/teacher` (home), `/teacher/exams` (list), `/teacher/exams/new`, `/teacher/exams/:id` (tabs: Live room / Results / Questions; edit when draft), `/teacher/exams/:id/edit`, `/teacher/exams/:id/grading`, `/teacher/grading`, `/teacher/gradebook`. Teacher layout = left sidebar nav (Home, Exams, Grading, Gradebook) like the design; nothing that does not work is shown (no Canvas/Homework/Word upload links yet).

Student routes: `/student` (home), `/student/exams`, `/student/exams/:examId/checkin` (check-in + start code), `/student/exam/:attemptId`, `/student/done/:attemptId`, `/student/grades`, `/student/grades/:examId`. Student layout = top nav (Home, Exams, Grades) except during an exam (locked layout like design "Taking an exam").

---

# Slice 3 — external-website exams (SEB-style) + lighter visual style

## Exam fields (added)

```json
{"mode": "questions" | "external",       // default "questions"
 "external_url": "https://..." | null,     // required when external; http(s) only, max 2000
 "allowed_domains": ["canvas.dku.edu"],    // hostnames, max 30, lowercase, no scheme/path; the external_url host is always allowed
 "finish_url_contains": "formResponse" | null}  // optional, max 200: when the external page URL contains it, the app finishes automatically
```
- POST/PUT accept these. When mode is "external": `external_url` required (422 otherwise), `questions` must be `[]` (422 otherwise), question_count 0, total_points 0. When mode is "questions": at least one question, and the three external fields are stored as null/[].
- A domain entry allows that host and all its subdomains (`dku.edu` allows `canvas.dku.edu`). Entries are validated: letters, digits, dots, hyphens; at least one dot or "localhost".

## Behaviour of external exams

- Student exam list items add `"mode"`.
- Start/attempt view adds `"mode"`, `"external_url"`, `"allowed_domains"` (always including the external_url host), `"finish_url_contains"`; `questions: []`, `answers: {}`.
- Submit works as usual with `{"answers": {}, "client_seq": 0}` (receipt issued). Heartbeat/events unchanged.
- Results: attempts of external exams have `"score": null, "percent": null`, `answers: []`; top-level `average_percent`/`median_percent` null, `score_buckets` all zero. `needs_grading` 0.
- Release/unrelease → 409 `{"detail":"This exam is graded on the other website"}`.
- Gradebook: external exams are listed; a submitted attempt's cell state is `"submitted"` (new state) with score/percent null; excluded from averages. CSV shows "submitted".
- Student grades list: external exams excluded. Feedback → 404.

## Native bridge (added)

JS → native:
```json
{"action": "openExternal", "attempt_id": "a_x", "url": "https://...", "allowed_domains": ["..."],
 "finish_url_contains": "..." | null, "deadline_at": "ISO", "server_time": "ISO",
 "title": "MATH 201 Midterm", "token": "<student bearer token>"}
```
Native (must already be locked; if not, it locks first and fails with `lockFailed` if it can't):
- Shows a native top strip above the web view: exam title, countdown to deadline_at (corrected by server_time offset), and a "Finish exam" button. The strip is light (white, hairline bottom border), not black.
- Navigates the web view to `url`. Navigation policy while external: allow the exam server, `allowed_domains` (and subdomains); block everything else and record `blocked_navigation` events. Popups/new windows: open in the same view if allowed, else block.
- While external, the JS page is gone, so native itself sends `POST /api/student/attempts/{aid}/heartbeat` `{"current_question":0,"locked":true}` every 5 s and forwards integrity events to `/api/student/attempts/{aid}/events` (batched), using `Authorization: Bearer <token>`. If a heartbeat returns `status: "submitted"` (teacher closed the exam), finish.
- Finish happens on: "Finish exam" pressed and confirmed (native sheet: "Finish the exam? Make sure you submitted on the website first."), page URL containing `finish_url_contains` (after a 2 s delay so the site's confirmation is shown), or deadline reached. Finishing = hide the strip, restore the normal navigation policy, and load `<server>/student/external-done/<attempt_id>`; that web page submits, calls `unlock`, and shows the receipt page. Native stays locked until that `unlock` arrives.

## Web routes (added)

- `/student/external/:attemptId` — ExternalExamPage: loads the attempt; outside the Mac app shows "This exam opens only in the Proctora app"; inside: requestLock (lockFailed screen with Retry as in ExamTakingPage), then `openExternal`.
- `/student/external-done/:attemptId` — submits (idempotent), `bridge.unlock()`, navigates to `/student/done/:attemptId`.
- Check-in/resume navigate to `/student/external/:aid` when the attempt's mode is external.
- Teacher exam setup: "Where do students answer?" — "Questions in Proctora" or "On another website" (URL, allowed websites list, optional "Finish automatically when the page address contains").

## Visual style (slice 3) — replaces the dark look

- No black/near-black surfaces anywhere: no dark top bars, banners, live-room header or check-in page. Ground `#F3F5F7`, surfaces white, text ink `#1B2533` (dark slate, used for text only), secondary `#4A5568`, muted `#6B7686`, hairlines `#E2E6EC`. Primary blue stays `#2F4BD8` with tint `#EEF1FD`. Attention/amber `#B4570B` (tint `#FDF1E6`). Success `#16794C` (tint `#E8F5EE`).
- Type: UI in Plus Jakarta Sans; **question text, essay text and the student's written answers in Source Serif 4** (self-hosted `@fontsource/source-serif-4`) for an exam-paper feel. Timers and start codes use Plus Jakarta Sans with `font-variant-numeric: tabular-nums` (no monospace).
- Student exam screen: a white "paper" sheet (max ~760 px) centered on the ground; slim white top bar (lock state pill, course + title, save status, timer pill that turns amber under 5 min); bottom bar with Previous · "Question n of N" (opens the question navigator popover/panel) · Next / Review. No permanent right rail.

---

# Slice 5 — LMS sync (Canvas first; Moodle, Blackboard, Brightspace via the same interface)

Goal: a teacher connects their LMS course, imports the student roster into FairPlay, links a FairPlay exam to the course, and pushes released grades back to the LMS gradebook.

## Model
- `LmsConnection` (per teacher): id, teacher_id, provider ("canvas"|"moodle"|"blackboard"|"brightspace"), base_url, credential (API token; stored server-side only, never returned), label, created_at, last_sync_at, last_error.
- `CourseLink`: id, connection_id, lms_course_id, name, code; roster synced into users (role student; username = LMS login id or email local part; name from LMS) + `CourseMember` (course_link_id, user_id, lms_user_id).
- Exam gains `course_link_id` (nullable) and `lms_assignment_id` (nullable, set on first grade push).

## API (role teacher)
- `GET /api/lms/providers` → `[{"id":"canvas","name":"Canvas","auth":"token","status":"ready"}, ...]` (others "beta").
- `GET/POST /api/lms/connections`, `DELETE /api/lms/connections/{id}`; POST body `{"provider","base_url","token","label"}`; the server verifies the token by calling the LMS ("who am I") and returns 422 `{"detail":"..."}` with a clear reason if it fails.
- `GET /api/lms/connections/{id}/courses` → courses the teacher teaches in the LMS.
- `POST /api/lms/connections/{id}/courses/{lms_course_id}/link` → CourseLink + roster sync (idempotent). `POST /api/lms/courses/{link_id}/sync` re-syncs the roster. `GET /api/lms/courses` lists linked courses with member counts.
- `PUT /api/exams/{id}/lms` body `{"course_link_id": id|null}` links an exam; only linked-course students can start it when set (others 403 "This exam is for <course> students").
- `POST /api/exams/{id}/lms/push` → pushes released grades (creates the LMS assignment named after the exam with points_possible = total_points on first push, then posts each student's score; comments included). Returns `{"pushed": n, "skipped": n, "errors": [{"student","detail"}]}`. 409 if grades not released or no course link.
- All outbound LMS calls: httpx async, timeouts 15 s, retries on 429/5xx with backoff, pagination (Canvas Link headers), never block the student hot path.

## Web (teacher)
- Sidebar item "LMS sync" → `/teacher/lms`: connect (provider cards, base URL, token with "How to get a token" help), connection list, courses list with "Link" / "Re-sync roster", linked courses with member counts, last sync time / error.
- Exam setup: "Course in your LMS" select (linked courses). Results header: "Send grades to Canvas" button when linked + released, showing the push result.

## Slice 5 — response shapes (as implemented)
- Provider: `{"id","name","auth":"token"|"oauth_token","status":"ready"|"beta","token_help","base_url_hint"}`.
- Connection: `{"id","provider","provider_name","status","base_url","label","lms_user_name","created_at","last_sync_at","last_error","course_count"}` — never a token. POST with the same provider+base_url replaces the stored token (same id). base_url must be https (http / private IP literals only with env `PROCTORA_LMS_ALLOW_PRIVATE=1`).
- `GET /api/lms/connections/{id}/courses` → `[{"lms_course_id","name","code","link_id": id|null}]`.
- CourseLink: `{"id","connection_id","provider","provider_name","lms_course_id","name","code","member_count","exam_count","last_sync_at","last_error"}`; link/sync responses add `"sync": {"added","updated","removed","total"}`.
- Exam (teacher view) adds `"course_link_id": id|null, "lms_assignment_id": str|null`.
- Push adds `"lms_assignment_id"`. `skipped` = attempts not submitted or whose student is not a member of the linked course.
- LMS failures (unreachable, rejected token, LMS error) → `502 {"detail"}` (never 401) and are stored as `last_error`; concurrent link/sync/push of the same thing → 409.
- Imported students get no usable password (password_hash `!lms-import`); they need SSO or a password reset flow (not built). An LMS user whose login id equals an existing FairPlay *student* username is mapped to that student.

---

# Slice 6 — offline packages, question images, Scan & Grade, topics, insights, profiles, practice

Delivery modes per exam (`delivery`): `"computer"` (FairPlay app, live room + lockdown — unchanged, optional),
`"paper"` (printed, scanned, AI-assisted grading), `"website"` (= existing mode "external"). Field `delivery`
is derived for old exams: mode external → "website", else "computer". New exams send it.

## A. Offline exam packages + resume offline
- `GET /api/student/exams/{id}/package` (student, exam live OR scheduled within 2 h): `{"exam_id","version","alg":"AES-GCM","salt","iv","ciphertext"}`.
  Plaintext = JSON of the student exam view (questions without keys, title, duration, images as data URLs ≤ 2 MB total, else URLs).
  Key = PBKDF2-SHA256(start_code + exam_id (+ package key from v2), salt, `iterations`, 32 bytes): version 3 (audit 2026-10-13) writes `"iterations": 600000` into the package; packages without the field use 100 000. Clients refuse counts outside 1 000–10 000 000. The server only serves packages while
  `start_code` is set; the client downloads on the check-in page and stores in IndexedDB/localStorage (`fairplay:pkg:<exam_id>`).
- Start stays online when possible (`POST start`). If the network is down at start, the client decrypts the package with the
  typed code (wrong code = decrypt fails = "Wrong start code"), creates a local attempt (`offline:<exam_id>`), lets the student
  work, and on reconnect calls `POST /api/student/exams/{id}/start` with `{"start_code", "offline_started_at"}` then syncs answers.
  Server accepts `offline_started_at` up to 30 min in the past and sets deadline from it.
- Resume: the exam page caches the last AttemptView in localStorage (`fairplay:attempt-view:<aid>`) and uses it when
  `GET attempt` fails with a network error.

## B. Question images
- `POST /api/uploads` (teacher, multipart `file`, png/jpeg/gif/webp/svg→rejected, ≤ 5 MB) → `{"id","url":"/api/uploads/<id>","width","height"}`.
  Stored under `server/data/uploads/` (or S3-compatible later); `GET /api/uploads/{id}` serves with long cache, auth not required
  (ids are 128-bit random).
- Question gains `"images": [{"url","alt"}]` (max 6). Shown above the prompt for students, in review, feedback, grading, print.
  Options may contain images later (not now).

## C. Topic tags (required)
- Question gains `"topics": ["integration by parts", ...]` (1–5, each 1–60 chars, trimmed, case-insensitive dedupe, stored lower-case).
  POST/PUT exam: every question needs ≥ 1 topic → 422 "Each question needs at least one topic" — EXCEPT old exams already stored
  (no backfill) and the hidden website-exam item (topic = exam course or "general").
- `GET /api/topics` (teacher) → `[{"topic","question_count"}]` for autocomplete.

## D. Paper exams — Scan & Grade
- Exam with `delivery: "paper"`: no start code / lockdown. Teacher assigns students via LMS course link or picks students
  (`PUT /api/exams/{id}/roster {"usernames": [...]}`; default = all students).
- Print: `GET /api/exams/{id}/print.pdf` → one booklet per rostered student (A4), each page has a QR code
  `FP1|<exam_id>|<student_id>|<page_no>` plus human-readable name/page, and per question a bordered answer box sized by type
  (mcq: bubbles A–H; numeric/short: one line; essay/work: big box; teacher can set `answer_lines` per question). The layout
  (box rectangles per page in PDF points) is stored as `print_layout` so scans can be cropped. `?blank=1` gives one unnamed
  copy (QR has student_id 0 → matched later by a "Name" box read by AI or picked by the teacher).
- Upload: `POST /api/exams/{id}/scans` (multipart PDF or images, ≤ 200 MB) → `{"batch_id"}`; processing runs in the background
  (asyncio task / thread pool; never blocks the event loop): render pages (PyMuPDF, 150–200 dpi), detect QR (OpenCV
  QRCodeDetector, try rotations), deskew/scale using QR position, crop answer boxes, store crops as uploads.
  `GET /api/exams/{id}/scans` → batches with status counts; `GET /api/exams/{id}/scans/unmatched` → pages without a usable QR
  with a preview; `POST .../pages/{page_id}/assign {"student_id","page_no"}` fixes them by hand.
- Matched answers create/merge a submitted attempt per student (`source: "paper"`); answers hold the crop image URL
  (`{"image": url}`), MCQ bubbles are read by pixel darkness (OpenCV) as an automatic answer.
- AI grading queue: for each paper answer the AI reads the crop (vision) against prompt + rubric/correct answer and drafts
  `{"points","comment","confidence","transcript","mistake_tags":[...]}` stored as `ai_draft` on the answer (not a grade).
  Teacher: Approve (draft → grade), Edit, Regrade (re-run AI), and **bulk approve** similar answers (group by identical
  normalized transcript or by same draft points+mistake tag, with a "approve these N" action). Low confidence (< 0.7) is flagged.
- Release / feedback / gradebook / LMS push: unchanged (paper attempts are normal attempts with grades).

### D — as implemented (server/app/routes_scan.py, server/app/scan/)
- Teacher: `GET /api/scan/exams` (paper exams + any exam with printed booklets/scans: `{exam_id,title,course,status,delivery,printed,batches,unmatched,paper_attempts,to_review,grades_released,...}`);
  `GET|PUT /api/exams/{id}/roster` → `{source:"picked"|"course"|"all", students:[{id,username,name}], all_students:[...]}` (PUT `[]` = default);
  `GET /api/exams/{id}/print` (pages_per_student, printed_at, layout_changed, questions with answer_lines) and
  `PUT /api/exams/{id}/print-settings {"answer_lines": {qid: 1..40|null}}`; `GET /api/exams/{id}/print.pdf[?blank=1]`.
  Question gains optional `answer_lines` (1–40, null = default: essay 12, numeric/short 1 line).
- `POST /api/exams/{id}/scans` → 202 `{"batch_id"}`; needs a printed layout (409) — the layout is stored when print.pdf is generated.
  The first upload sets the exam to `closed` (papers collected) so release works. `GET /api/exams/{id}/scans` →
  `{ai_enabled, pages_per_student, students_expected, students_scanned, unmatched, batches:[{batch_id,status:"queued"|"reading"|"grading"|"done"|"failed"|"stalled",page_count,processed,matched,unmatched,ai_total,ai_done,error,...}]}`;
  `GET .../scans/unmatched` → `[{page_id,seq,reason,preview,name_crop,qr_found,page_no,suggested_name,suggested_student_id}]`;
  `POST .../scans/pages/{page_id}/assign {"student_id","page_no","follow_on":true}` (follow_on: the next unnamed pages 2..n of a blank booklet
  in the same upload go to the same student); `POST .../scans/pages/{page_id}/ignore`; `POST .../scans/{batch_id}/rerun-ai`.
- Paper answer value: `{"image": "/api/uploads/<id>", "page", "mcq": idx|null, "marks": "one"|"none"|"multiple"|"unclear", "blank", "fill"|"ink"}`.
  Grading: mcq with marks "one" → automatic; "none" → 0 (unanswered); everything else on paper is **pending until a teacher grade exists**.
  Results/feedback rows of paper answers add `"image"`; `given` = mcq index, the approved transcript, or "(written on paper)". Attempt gains `source` ("computer"|"paper") and `ai_drafts`.
- Queue: `GET /api/exams/{id}/scan/queue` → `{questions:[{...,total,to_review,approved,auto,low_confidence}], items:[{attempt_id,username,name,question_id,image,mcq,marks,blank,draft,grade,status:"needs_review"|"approved"|"auto",auto_points,low_confidence,group}], groups:[{question_id,key,label,points,count,items}], ai_enabled}`.
  Draft = `{points,comment,confidence,transcript,mistake_tags,source:"ai"|"key"|"blank"|"bubbles"|"fallback",low_confidence}`.
  `POST .../scan/answers/{aid}/{qid}/approve` body `{}` (draft as is) or `{"points","comment"}` (edit); a "fallback" draft (AI off) cannot be approved as is (409).
  `POST .../scan/answers/{aid}/{qid}/regrade {"hint"}` → `{draft}`; `POST .../scan/approve-bulk {"items":[{attempt_id,question_id}]}` → `{approved,skipped}`.
- Web: `/teacher/scan`, `/teacher/exams/:id/scan?step=print|upload|grade|release`; paper exams show a "Scan & Grade" tab instead of the Live room.

## E. AI provider (shared)
- `server/app/ai/` — `async def complete_json(system, prompt, images: list[bytes]=[], schema_hint=str) -> dict`.
  Env: `FAIRPLAY_AI_PROVIDER` = `anthropic` | `openai` (any OpenAI-compatible: OpenAI, DeepSeek, Qwen/DashScope, Moonshot —
  needed for China) | `none` (default). `FAIRPLAY_AI_KEY`, `FAIRPLAY_AI_BASE_URL`, `FAIRPLAY_AI_MODEL`.
  With `none`: deterministic fallbacks (grading: 0 points, confidence 0, comment "AI is off — grade by hand"; variations: numeric
  perturbation only). Every AI output is a draft a teacher approves; nothing reaches students unapproved.

## F. Class insights (new page `/teacher/insights`)
- `GET /api/insights?course=&exam_ids=` → `{"topics":[{"topic","avg_percent","n_answers","n_students","trend"}],
  "hardest":[{"exam_id","question_id","prompt","avg_percent","lost_points"}], "mistakes":[{"label","count","examples":[...],
  "topics":[...]}]}`. Topic score = sum awarded / sum max over graded answers (pending excluded). Mistakes = grouped AI
  `mistake_tags` + teacher comments (normalized text clustering, top 20).

## G. Student profile (new page `/teacher/students/:username`, also `/teacher/students` list)
- `GET /api/students/{username}/profile` → `{"student","topics":[{"topic","avg_percent","n","trend":[{"date","percent"}]}],
  "strengths":[topic...], "weak":[topic...], "exams":[{"exam_id","title","date","percent","time_on_task_min"}]}`.
  Strength ≥ 80 %, weak < 60 % with ≥ 2 answers. Time-on-task = submitted_at − started_at (computer) — shown as context
  only, labelled "not a cheating signal", never flagged.

## H. Practice assignments (new page `/teacher/practice`; student `/student/practice`)
- `POST /api/practice/generate {"username"|"usernames", "topics"?, "count": 1–20}` → draft set per student from their weak
  topics: variations of the teacher's own questions in those topics (AI rewrite + numeric perturbation). Every numeric/math
  answer verified with SymPy (`server/app/mathcheck.py`: parse expression/number answers, check equivalence, numeric
  answers recomputed from a stored sympy formula when the variation is generated by perturbation); unverifiable items are
  dropped or marked "needs teacher answer". Sets status `draft` → teacher previews/edits/removes items → `approved` → `sent`.
- Student: `GET /api/student/practice` lists sent sets; answering gives instant correctness (checked server-side), never
  counts toward grades (`formative: true`, excluded from gradebook, results, averages; included in profile as "practice").

## Slice 6 A/B/C + delivery — as implemented
- `delivery`: stored column (NULL on old rows → derived). POST/PUT: omitted → derived from `mode`; only `delivery` sent →
  sets `mode` ("website" → "external", else "questions"); both sent and disagreeing → 422. Returned in teacher Exam and in
  `GET /api/student/exams` items. Paper exams: `POST /open` → 409 "Paper exams are printed, not opened in the app";
  `POST /close` also works from draft (so grades can be released); never listed in `GET /api/student/exams`;
  start / package → 404.
- Question `images`: `[{"url","alt"}]`, max 6, url `/api/uploads/<16–64 [A-Za-z0-9_-]>` or `https://…`, alt ≤ 300 (trimmed).
  Included in teacher view, student exam view, grading queue questions and student feedback questions.
  Upload ids are 32 hex chars; files in `FAIRPLAY_UPLOADS_DIR` (default `server/data/uploads`). Type checked by bytes
  (png/jpeg/gif/webp; svg and anything else 422), > 5 MB → 413, Pillow must open it (else 422).
  `app.uploads.save_image_bytes(bytes)` / `read_upload(id)` are reusable helpers (e.g. scan crops).
- Question `topics`: in teacher views only (NOT in the student exam view or package: tags can hint at the method).
  Validation error text: "Each question needs at least one topic" (422). Hidden website item topic = lower-cased course or "general".
- Package: `version` = 1 (format version). Plaintext `{"exam_id","title","course","duration_min","mode","shuffle_questions",
  "questions","generated_at"}`; questions in original order. Served while `start_code` is set (i.e. live); website exams → 409.
  Cached per exam+code+duration for 5 min (one PBKDF2 per room, built in a worker thread).
- `offline_started_at` older than 30 min is clamped to 30 min ago (not rejected, so offline work is never lost; the
  deadline may then already be past → answers 409, submit still accepted and marked late); future values → now.
- Web: local attempt id `offline:<exam_id>`; record `fairplay:offline:<exam_id>` = {exam_id, start_code, started_at, view,
  real_attempt_id}. The exam page keeps running under the offline URL after reconnect and routes every call to the real id.


## App session and lockdown hardening (audit 2026-10-12)

Binding for `server/`, `web/` and `mac/`. Background: `project-notes/lockdown-audit-2026-10-12.md`.

### Server settings

| env | meaning |
|---|---|
| `PROCTORA_SECRET` (or `FAIRPLAY_SECRET`) | required, 16+ chars. The old public default is refused. Without it the server **does not start** unless `FAIRPLAY_DEV=1` (then a random secret is kept in `data/secret.key`). |
| `FAIRPLAY_APP_KEY` | required when `FAIRPLAY_REQUIRE_APP=1`: HMAC key compiled into the Mac app (`mac/build.sh`). Comma-separated list during key rotation. The public dev key is accepted only with `FAIRPLAY_DEV=1`. |
| `FAIRPLAY_REQUIRE_APP` | default `1`: exams start only from the app. `0` = browser demo mode (e2e scripts, demos); browser attempts show as client "browser". |
| `FAIRPLAY_DEV` | `1` for a local laptop demo only (`Run Server.command` sets it). |
| `FAIRPLAY_CORS_ORIGINS` | extra origins (comma-separated). Default: none (the UI is same-origin). Dev adds the Vite dev server. |

Start codes have **6 digits**. Each live exam also has a 6-digit `invigilator_code` (teacher views only: exam, live room).

### Browser gate

The student side (`/student/*` pages, `/api/student/*`, `/api/setup/*` endpoints) is for the FairPlay apps only. Any request to these routes from a browser gets the same `404 {"detail": "Not Found"}` as an unknown API path — no FairPlay text, no login page, nothing that identifies the endpoint. A student login attempt from a browser counts as a failed attempt (401, then 429 once the sign-in limits are reached) exactly like a wrong password, with no way to distinguish.

**FAIRPLAY_BROWSER_GATE** env var (default `headers`):
- `off`: no gating, all requests allowed (dev only; requires `FAIRPLAY_REQUIRE_APP=0` together).
- `headers`: allows User-Agent with `FairPlay/<version>`, or `X-FairPlay-Platform` (mac|windows|ipad) + `X-FairPlay-Version` headers, or empty user agent, or Edge/WebView2 on Windows (for 0.7.x), or `X-FairPlay-Session` header on POST `/api/student/attempts/{aid}/events` only (Mac watchdog).
- `strict`: only allows `FairPlay/` user agent, `X-FairPlay-Platform` + `X-FairPlay-Version`, or `X-FairPlay-Session` for the watchdog endpoint; rejects empty user agent and Windows Edge until the app sends `FairPlay-Windows` marker.

What released apps send: Mac and iPad set `User-Agent` to include `FairPlay/<version>` on every page load and fetch; on their own API calls they send `X-FairPlay-Platform` + `X-FairPlay-Version`. Windows 0.7.x sends neither (stock Edge user agent on the web view, no headers on API calls); A later Windows build will send the `FairPlay/<version> FairPlay-Windows` user agent and the platform and version headers.

Production recommendation: deploy with `headers` now (accepts Windows 0.7.x). Move to `strict` once the first Windows build that sends these markers is the minimum Windows version (`MIN_APP_VERSION_WINDOWS`).

Exempt routes (no gating): `/api/health`, `/api/app/version`, `/` and `/teacher/*` pages, `/api` teacher endpoints, `/assets/*`, signed DKU OJ routes (`/api/integrations/dkuoj/v1/exams/{id}`), `/api/student/attempts/*/sealed-submit`.

**Limits:** headers can be copied by anyone with network tools; they stop a browser and curl, not a determined user. Real proof (Apple App Attest or a signed-build check) is a later step. The student JavaScript is planned to be split into chunks under `/student/assets`, so the same gate covers it.

For a local browser-test setup (e2e, demo, or Vite dev), set both `FAIRPLAY_BROWSER_GATE=off` and `FAIRPLAY_REQUIRE_APP=0`. Never use this on any real server.

### Start (app proof)

1. `GET /api/student/exams/{id}/challenge` → `{"nonce", "expires_in": 120}` (single use, bound to student + exam).
2. Page → app bridge `{"action":"attest","nonce"}` → app replies event `{"type":"attestation","nonce","device_id","signature","version","build"}` (or `attestationFailed`).
   `signature = hex(HMAC-SHA256(app key, "start|<nonce>|<device_id>"))`.
3. `POST /api/student/exams/{id}/start` body adds `"client": {"device_id","nonce","signature","version","build"}`.
   - no `client` and `FAIRPLAY_REQUIRE_APP=1` → `403 {"detail": "Open this exam in the FairPlay app. ..."}`; bad/reused proof → `403`.
   - Response = attempt view + `"session"` (string). The page stores it per attempt and sends `X-FairPlay-Session: <session>` on **every** `/api/student/attempts/{aid}/...` request (GET included).
   - Existing attempt, other device → `409 "This exam is already open on another computer..."` + event `second_device`.
   - Existing attempt, same device → new session (old one stops working) + event `re_entry`, except a repeat within 15 s (double click).
   - After `POST /api/exams/{id}/attempts/{aid}/release-device` (teacher) the next start from any app binds the attempt (event `device_bound`).
4. Wrong/missing session on an attempt request → `409 "This exam was opened somewhere else..."` + event `foreign_session` (max 1/min). In browser demo mode a request without the header is still accepted.

### App beats (the only source of "locked")

`POST /api/student/attempts/{aid}/app-beat` every 5 s, sent by the **app** (not the page), with the student token and `X-FairPlay-Session`:
`{"session","device_id","ts","locked","checks","signature"}`, `signature = hex(HMAC(app key, "beat|<aid>|<session>|<device_id>|<ts>|<0|1>|<checks>"))`, `ts` = client ms, strictly increasing.
`checks` e.g. `vm=0,disp=1,mir=0,tap=1,inp=1,proc=0,bg=2`: `proc` = apps that must still close (only remote control / screen sharing / AI overlays; `?` when the process list could not be read, audit M-13), `bg` = other windowed apps left running behind the exam. Response `{"ok","status","deadline_at","server_time"}`.

**Apps during the lock (2026-10-13).** Only the denied list (remote control, screen sharing, AI assistants and overlays) is closed and blocks the exam. Every other app may keep running in the background (a VPN client such as AnyConnect, a sync tool, an open document): each is reported once per lock as `app_in_background` with its name, hidden, and kept behind the exam by the focus guard. Leftover shell scripts are ended only if they started within 10 minutes before the lock or later; older ones are reported (audit M-12). Same on Windows.

**Device id (audit M-07).** The Mac sends `hw-` + 32 hex of SHA-256(`fairplay-device|<IOPlatformUUID>|<app key>`), so copying FairPlay's files to another Mac does not move the device; a random id in Application Support is the fallback only when IOKit has no UUID. The server flags `network_changed` (once per 5 min) when one app session's signed beats come from a new network address.

**VM check (audit M-10).** Any of: `kern.hv_vmm_present`, a VM word in `hw.model`, the Intel `VMM` CPU flag, or a VM manufacturer/model on the IOKit platform entry. The one-screen message names the screens found.
Server events: `app_gap` (> 20 s since the last beat), `unlocked_in_exam` (locked → unlocked while writing), `no_app_signal` (page heartbeats but no app beat for 45 s; max 1 per 5 min).
`POST .../heartbeat` still updates "online"/current question; its `locked` is ignored.

Live room student fields added: `app_online`, `last_app_signal_at`, `app_checks`, `client` ("app"|"browser"), `entries`; `locked` = fresh signed beat says locked. Live room adds `invigilator_code`.

### Invigilator exit

App (Ctrl-Opt-Cmd-E) → `POST /api/student/attempts/{aid}/exit-code {"code", "nonce"}` → `{"ok": true, "nonce", "proofs": [HMAC(app key, "exit|aid|session|ok|nonce") per key], "proof": HMAC(first app key, "exit-verdict|aid|session|1|nonce")}` (Mac checks `proofs`; Windows checks `proof` when `RequireSignedVerdicts` is on) or `403`; 5 wrong → `429` for 5 min. Each try is reserved (atomic INCR) before the compare, so parallel guesses can't exceed 5 (audit 2026-10-13); the start code works the same way. Events `emergency_exit`, `emergency_exit_rejected`. No code is stored in the app.

**Offline exit (2026-10-12, reworked after review)** — when the server cannot answer (no network, 5xx, session moved) or the invigilator picks "Offline code…" in the same dialog. The locked Mac shows a random **Mac code** (8 chars from `ABCDEFGHJKMNPQRSTUVWXYZ23456789`, shown as `XXXX-XXXX`, new per lock). Exit code = first 4 bytes of `HMAC-SHA256(K, "offline-exit|<MAC CODE>")` big-endian, mod 10^8, 8 digits zero-padded. Checked in the app; 5 wrong → 5 min wait.

`K` is the attempt's **offline key**: `HMAC-SHA256(server secret, "offline-exit-key|<attempt>|<session>")`. It is not the app key and is not in the binary. The app fetches it itself right after the exam session is known:

- `POST /api/student/attempts/{aid}/offline-key {"session","device_id","ts","signature"}`, signature = HMAC(app key, `offkey|<aid>|<session>|<device_id>|<ts>`) → `{"key": "<64 hex>", "proof": HMAC(first app key, "offkey-resp|aid|session|key|ts")}`. **Once per session**: a second request → `409` + event `offline_key_requested_twice`. The page never sees the key; the app keeps it in memory only.
- Teacher: `POST /api/exams/{id}/attempts/{aid}/offline-exit {"mac_code"}` → `{"code"}` for that student's current session; event `offline_exit_code_issued` (not a flag). Live room: "Mac offline or stuck? · offline exit" → pick the student, type the Mac code.
- App events `offline_exit` / `offline_exit_rejected`, forwarded when the page is online again.
- No key (the app never reached the server for this exam): **release builds have no offline exit** — restart the Mac, answers are saved. Dev builds fall back to the app key (`server/tools/offline_exit_code.py`) and also accept `0000` (`defaults write ai.proctora.student DevExitCode <code>`).
- Limit: someone who extracted the app key AND holds the student's token can request the key once per session — that request is either the app's own (then theirs is refused and flagged) or it makes the app's refused (no offline exit, flagged). App Attest (M-20) closes this.
- Test vectors: key bytes `00 01 … 1f`, Mac code `K7QM3XPA` → `06710479`; dev key (fallback) → `37496243`.

### Dev trace log

Dev builds write `logs/events.log` in the repo (path compiled in by `mac/build.sh`, override with `FAIRPLAY_DEBUG_LOG`; rotates at 5 MB to `events.log.1`): clicks in the app and on the page (element only), lock/unlock, check results, every app-quit attempt and its result, bridge messages, app↔server requests that fail (and all non-beat requests), page fetch errors, JavaScript errors/console.error, page crashes, uncaught exceptions, sleep/wake. Never typed text, codes, tokens or answers. Release builds write nothing there.

### Teacher

- `POST /api/exams/{id}/rotate-code` → exam with a new start code (live only). Offline packages need the new code.
- `POST /api/exams/{id}/attempts/{aid}/release-device` → `{"ok": true}`.

### Native bridge (changed)

Page → app: `lock` may carry `attempt_id`, `token`, `session`; new `session` (same fields) and `attest`; `openExternal` adds `session`.
App → page: new `attestation`, `attestationFailed`, `unlockPending {"reason": "offline"|"not_submitted"}`.
`unlock` no longer unlocks by itself: the app unlocks only when `GET /api/student/attempts/{aid}` says `submitted` (or 404/409), retrying every 3 s. The app only accepts bridge messages from the main frame of the exam server (scheme + host + port).

### Website mode

`allowed_domains` entries: exact host (`canvas.dku.edu`), `*.host` for the host and all subdomains, optional path prefix (`docs.google.com/forms/d/e/1FAIpQL/`). Old bare entries now mean the exact host only. The exam address always adds its exact host, or host + folder on shared hosts (docs.google.com, drive.google.com, ...). Release apps open https only.

### Mac app builds

`mac/build.sh` (dev, default) vs `FAIRPLAY_BUILD=release` with `FAIRPLAY_SERVER` (https), `FAIRPLAY_APP_KEY`, optional `FAIRPLAY_PINS` (`mac/pin.sh`), `FAIRPLAY_SIGN_ID`, `FAIRPLAY_NOTARY_PROFILE`. Release: no UserDefaults/env overrides, no safety unlock, no ATS exceptions, hardened runtime.

### Audit 2026-10-13 additions (bridge and app)

- **Preflight (M-11):** page → app `{"action":"preflight"}`; app → page `{"type":"preflight","problems":[...]}` (plain sentences; `[]` = ready). The page shows the problems and doesn't ask to lock until the list is empty. Hosts that don't answer within 3 s count as `[]` (the lock's own checks still run).
- **Bridge nonce (W-10):** the host may set `window.__fairplayNonce` (≥16 chars) at document start; the page reads it once, deletes it, and from then on ignores `proctora-native` events whose `detail.bridge_nonce` differs (its own field: `attestation` messages already use `nonce` for the server challenge). The Mac app sets one per launch. The Windows shell should do the same.
- **Signed hand-in (M-04):** `GET /api/student/attempts/{aid}/handin?nonce=&device=<app device id>` → `{"status": "submitted"|"in_progress"|"gone"|"moved", "proofs": [HMAC(app key, "handin|aid|session|status|nonce") per key]}`. The app unlocks only on a valid proof for submitted/gone/moved. `moved` = the attempt is now bound to ANOTHER device: from the bound device itself a stale or made-up session is `in_progress` (audit 2026-10-13). While locked, both apps refuse a `session`/`lock` message naming a different attempt, and a token-only update keeps the offline key.
- **Beat checks (M-02):** `console=0` → the server treats the beat as not locked and flags `off_console`; `secinput=1` → `secure_input_other_app`.
- **Lock without a session (M-06):** `{"action":"lock"}` must carry `attempt_id`/`token`/`session`, or `offline: true` (offline start). Otherwise `{"type":"lockFailed","reason":"bad_session"}` and the page asks for the start code again.
- **Relaunch (M-17):** after ending while locked, the watchdog reopens the app with `--resume-attempt <aid>`; it opens `/student/exam/<aid>` directly.

## 2026-10-13 simplification
- **Optional parts:** `FAIRPLAY_FEATURES` (comma list of `scan`, `lms`, `practice`; default empty). Parts not listed have no API routes (404) and the teacher UI hides them; `GET /api/health` returns `features`. Paper exams (`delivery: "paper"`) are refused (400) unless `scan` is on. Insights is hidden in the UI; its `/api/students…` profile routes stay.
- **Classes:** `term` (free text, ≤80, e.g. "Fall 2026 · Section 2") on create/`PUT`; responses add `term` and `teacher_name`. Student `GET /api/student/classes` adds `term`. Invite link: `/student/join/<code>` (signed out: code kept for this tab, joined right after sign-in).
- **Grades:** `GET /api/gradebook` and `/api/gradebook.csv` take `class_id` (the class's members are listed even before any exam); `GET /api/grading` rows add `class_id`.
- **Exams:** saving needs no class; opening still does (the editor asks). Question `topics` are optional; a question without one is stored under the course (lower-case) or `general`.

## S1–S6 (2026-10-20) — as implemented (server + web)

Spec: `docs/contract-s1-s6-2026-10-20.md`. Everything below is live on the server and the web page; the Mac side is M2–M5.

### Headers and version gate (S2)
- Every `/api/*` response (errors too) carries `X-Server-Time: 2026-10-20T10:00:00.000Z` (UTC, ms, `Z`).
- Requests from the apps carry `X-FairPlay-Version: 0.5` and `X-FairPlay-Platform: mac|windows`. The page adds both to every `/api` request once the bridge `state` message has reported `app_version` and `platform`.
- Env `MIN_APP_VERSION_MAC`, `MIN_APP_VERSION_WINDOWS` (default `""` = no minimum). Numeric compare by dot parts: `0.10 > 0.9`, `1.0 == 1.0.0`; a non-numeric part counts by its leading digits.
- `POST /api/student/exams/{id}/start` and `POST /api/student/attempts/{aid}/app-beat`: with the platform header and a version below that platform's minimum (or no version header) → `426 {"detail": "Update FairPlay to continue", "min_app_version": "<min>"}`. No platform header = no gate. Both responses add `server_time` and `min_app_version` (string or null, for the request's platform).
- `GET /api/app/version` (no auth) → `{"mac": "<min>"|null, "windows": "<min>"|null, "server_time"}`. `/api/health` is never gated.
- Web: a 426 anywhere shows a full-page **Update FairPlay** screen with the minimum version.

### Device check (S1)
- Bridge: page `{action:"preflight"}` → app `{type:"preflight", problems:[...], checks:[{id, ok, message, info?}], platform, app_version, os_version}`. `problems` stays (older pages, Windows). `info: true` rows never block. The page reads at most 40 checks, strings cut at 300 chars.
- `POST /api/student/device-check` (student) `{platform, app_version, os_version, checks:[{id, ok, message, info?}], exam_id?}` → `{ok: true, saved_at}`. At most 40 checks, strings at most 300 chars (422 otherwise), 30 per minute. Keeps the latest report per (student, platform).
- `GET /api/exams/{exam_id}/readiness` (owner teacher) → one row per student of the exam's class (no class: students with attempts): `{student_id, name, platform, app_version, os_version, ok, failed: [ids], checked_at}`; `ok` = every non-info check ok; all of those null when never checked. The newest report of the student wins when there are several platforms.
- Web: Readiness card "Run check" (inside the app only) shows every row and posts it; the text names the Mac or Windows app; the live room has a **Device readiness** list.

### Resume after FairPlay closed (S3)
- Exam `resume_needs_approval` (default **true**; editor: "Teacher approves a student coming back after FairPlay closed"). Attempt `pending_resume`, `pending_since`.
- Which start asks: the start that resumes an in-progress attempt on its **bound computer** more than 15 s (`REENTRY_QUIET_S`) after the previous start — the `re_entry` branch of `_resume`, where the session rotates (a new app run). Not: a page reload (it only GETs the attempt with its kept session), a double click within 15 s, a browser start, the first bind on another computer (after release-device / approve-swap).
- Then: event `resume_requested`, start response `"needs_approval": true` (always present, false otherwise). `GET /attempts/{aid}`, `POST .../heartbeat` and `POST .../app-beat` return `"approval": "pending"` (later `"approved"`; `null` if it never had to wait). Answer PUTs still work. The page shows "Wait for your teacher" and does not auto-submit while waiting.
- `POST /api/exams/{id}/attempts/{aid}/approve-resume` (teacher) → `{ok, minutes_added, deadline_at}`: clears pending, adds the time waited to `deadline_at` (max 15 min), event `resume_approved` (not a flag). 409 when nothing is pending.
- Live room student: `approval`, `pending_since`; badge "Waiting for approval" + **Approve**.

### Device swap (S4)
- Start on a second computer while bound elsewhere → `409 {"detail": {"code": "swap_needed", "message": "This exam is already open on another computer. Ask the invigilator to move it to this one."}}` (+ `second_device`, max 1/min). The web client reads any object detail as its `message` (`ApiError.detail`) plus `ApiError.code`.
- `POST /api/student/attempts/{aid}/swap-request {"device_id"}` (owner) → `202 {"ok": true}`, event `swap_requested` (max 1/min per device). 10 per minute.
- `POST /api/exams/{id}/attempts/{aid}/approve-swap` (teacher) → `{"ok": true}`: like release-device (device unbound, new session nobody holds, caches cleared) and keeps `swap_old_session`, `swap_approved_at`; event `swap_approved` (not a flag). The new computer keeps calling start every 5 s (the page does this from the start form) and binds normally (`device_bound`).
- Old computer: beats / saves / events get the session error. `GET .../handin` answers `"moved"` signed with the session that request sent (`handin|aid|<its session>|moved|nonce`) — also after release-device.
- For 120 s after approval ONE `PUT .../answers` with the old session is accepted: it only fills questions the new computer has not changed since the approval; the reply adds `"merged": n`. A second one, or after 120 s, gets the session error.
- After `deadline_at + 60 s` both swap-request and approve-swap → `409 "Finish on the computer you started on"`.
- Live room student: `swap_requested`; badge "Wants to continue on another computer" + **Approve swap** ("move computer" = release-device stays).

### Sealed offline submit (S5)
- Bridge page → app `{action:"seal", attempt_id, answers_json, client_seq}` when the submit at time-up fails; app → page `{type:"sealed", attempt_id, ok}`. `answers_json` is `JSON.stringify(answers)` exactly as sent.
- Sealed file (app): `{payload, answers_json, proof}`, `payload = attempt_id|session|device_id|client_seq|sealed_at|answers_sha256`, `sealed_at` ISO UTC with ms and `Z`, `answers_sha256` lowercase hex SHA-256 of the UTF-8 bytes, `proof` = lowercase hex HMAC-SHA256(offline key = `HMAC-SHA256(server secret, "offline-exit-key|<aid>|<session>")`, payload).
- `POST /api/student/attempts/{aid}/sealed-submit` (Bearer only; no `X-FairPlay-Session` needed and a stale one is ignored) body `{payload, answers_json, proof}` → `{ok: true, receipt, late_sealed}`:
  - 422: payload not 6 parts / bad fields / other attempt id / SHA-256 mismatch / answers not a valid answer map / `sealed_at` not between `started_at` and now + 2 min. 403: proof does not verify. 404: not the student's attempt. 429: more than 20 a minute.
  - In progress: `sealed_at <= deadline_at + 60 s` → the sealed answers are graded; later → graded stays what was saved in time, the sealed answers that differ go to `late_answers`, `late` and `late_sealed` set. `submitted_at = min(sealed_at, now)`. Then submitted.
  - Same seal again → same receipt. Already submitted otherwise: answers equal → 200 with the existing receipt; different → `409 {"detail": {"code": "already_submitted"}}`, the differing sealed answers are added to `late_answers` (never graded) and a `sealed_submit` event says so.
  - Event `sealed_submit` (device id, sealed_at, in time or not) for every seal, once per seal: always in the teacher's review list (the key is on the student's computer).
- Beat checks `sealed=1` → live room `sealed_pending` ("submission pending on this student's computer"). Results attempts add `late_sealed`.
- Token for the upload: state `sealed_pending: true` and a signed-in student → the page posts `{action:"sealedToken", token}` once per page load (also right after sign-in).
- Page at time-up when the submit fails (offline / 5xx): **Try again now**, **Submit later from this computer** (seal → "Your answers are sealed on this computer. Ask the invigilator to end the exam" + offline exit steps), **Ask the teacher for help** (offline exit steps: the app shows its Mac code / PC code; the page never sees it). Automatic retries continue meanwhile.

### Strongest lock (S6)
- Exam `require_strong_lock` (default false; editor "Require the strongest lock (Windows: separate exam desktop. Mac: needs Apple assessment mode, not available yet)").
- Beat checks may carry `sep=0|1`. Setting on and a beat without `sep=1` → event `weak_lock` once per attempt; live room student `weak_lock` (latest beat) and a badge. The Mac sends no `sep` yet: Mac students show as weak.

### New event types (server-written, refused from clients as `client_*`)
`sealed_submit`, `resume_requested`, `resume_approved`*, `swap_requested`, `swap_approved`*, `weak_lock` (* bookkeeping, not flags).

### S3 addendum (2026-10-20): relaunch after a kill
After a kill or crash, the Mac and Windows apps reopen the exam page, which keeps its saved
session, so start is never called. A `previous_run_killed` client event on an in-progress
attempt therefore also sets `pending_resume` (when the exam's `resume_needs_approval` is on)
and records `resume_requested` once. The page sees `approval: "pending"` on its next heartbeat.

## 2026-10-20 iPad app (`ios/`)
Same bridge, app session, beats, hand-in, exits and website mode as the Mac app; differences only:
- **Detect:** same `window.webkit.messageHandlers.proctora`; the user agent contains `FairPlay-iPad` (`bridge.nativePlatform()` → `"ipad"`). `state` adds `"platform": "ipad"`; `trusted` is always true (no permission to grant).
- **App → page `lockPending`** `{"type":"lockPending","reason":"assessment_prompt"}`, sent when locking starts and every 4 s while iPadOS asks the student to allow Assessment Mode (its prompt times out after 60 s). `requestLock` restarts its timeout on each one. Older pages ignore it.
- **`lockFailed` reason `"assessment"`:** neither Assessment Mode nor Single App Mode could start (release builds never lock without one of them).
- **Device id:** `ipad-` + 32 hex of SHA-256(`fairplay-device|<random secret in this iPad's Keychain, ThisDeviceOnly>|<app key>`).
- **Beat `checks`:** `plat=ipad,aac=,sam=,ga=,soft=,shield=,disp=,cap=,mw=,fg=,left=,shot=,jb=,dbg=,sim=,kbd=,vpn=,proxy=,att=,console=,secinput=0`. `console=0` when the app is not in front (except in a teacher-allowed app while Assessment Mode holds), the screen is captured / mirrored, or a second display is attached (server: not locked + `off_console`, iPad wording). The beat's `locked` is true only while Assessment Mode or Single App Mode holds (or a dev soft lock). `sam=1` only for Single App Mode FairPlay turned on itself; pre-existing Guided Access is `ga=1` and never a lock.
- **Server (review 2026-10-20):** an iPad beat counts as locked only with `aac=1` or `sam=1` (`soft=1` only on `FAIRPLAY_DEV=1` servers). Starts whose `client.build` is `"dev"` are refused (403) unless `FAIRPLAY_DEV=1`.
- **Offline key reply:** adds `"proofs"` (HMAC over `offkey-resp|aid|session|key|ts`, one per app key) next to `"proof"`. The iPad and Mac apps accept the key only with a valid proof.
- **Allowed apps:** the page's `allowed_apps` bundle ids become Assessment Mode participants without network (browsers refused).
- **Client event types (added):** `app_backgrounded`, `screenshot_taken`, `screen_captured`, `multitasking`, `jailbreak_detected`, `debugger_attached`, `assessment_interrupted`, `assessment_resumed`, `assessment_unavailable`, `keyboard_connected`, `screen_locked`, `single_app_on`, `single_app_off`, `vpn_on`, `proxy_on`.
- **Invigilator exit:** hold the lock bar 3 s (or Ctrl-Opt-Cmd-E); the offline dialog shows an "iPad code" (same Mac-code format and rule).
- **Relaunch:** a lock that never ended is remembered on the iPad; the next launch records `previous_run_killed` and opens `/student/exam/<aid>`.
- **S1–S6 on iPad:** requests carry `X-FairPlay-Platform: ipad` + `X-FairPlay-Version`; server env `MIN_APP_VERSION_IPAD`, `GET /api/app/version` adds `"ipad"`. `state` adds `app_version`, `sealed_pending`. `preflight` replies with `checks` rows (`ipados_version`, `app_version`, `server`, `clock`, `assessment`, lock problems, `battery`, `vpn`), `platform: "ipad"`, `os_version`. `seal` / `sealedToken` / `sealed` as on the Mac (file in Application Support, proof with the offline key, `sealed_at` from the server clock). Beats add `sep=1` while Assessment Mode or Single App Mode holds (S6 strongest lock) and `sealed=1`. Live room shows the platform as "iPad".

### Before the lock: apps to close (2026-10-22, Mac and Windows)
(iPad 0.5.1: `apps_to_close` is always `[]` and `quitApps` answers `still_running: []` at once:
iPadOS lets no app list or close others; Assessment Mode does that itself.)
- page → app `{action:"preflight", allowed_apps?: string[]}`: the app's preflight reply gains
  `apps_to_close: string[]`. These are the windowed apps the lock would close with that
  allowed list: not the system, VPN clients, teacher-allowed apps or FairPlay itself. Names are
  display names, distinct and sorted. They are not `problems`, so they never block on their own.
- page → app `{action:"quitApps", allowed_apps?: string[]}`: the app asks those apps to quit,
  force-quits what is left after about 3 s, and answers `{type:"appsQuit", still_running: string[]}`.
- The page shows the list with "Close them and start" (so the student can save first), then
  locks. The lock still closes whatever is left. While locked, the blocking "close these apps"
  screen re-checks as soon as an app quits.
- Mac permission screen: shows which FairPlay copy runs and lists other copies. "Fix permission"
  runs `tccutil reset Accessibility ai.proctora.student`, then asks macOS again. There is also
  "Restart FairPlay".
- page → app (Mac only, 0.5.3+) `{action:"openSettings", pane}` with pane one of `sharing`,
  `datetime`, `displays`, `keyboard`, `accessibility`: opens that System Settings pane (never
  while locked; unknown panes are ignored). The pre-exam problems screen shows an "Open …
  settings" button per problem and checks again by itself every 3 s, so it moves on as soon as
  the setting is fixed. Services such as Remote Login need the user's admin password to turn
  off, so the app can't switch them off itself.

## Sample exam (trial) (2026-10-22)
One built-in sample exam every student can take any time ("Try a sample exam" card on the
student dashboard) to feel a real exam: the same app proof, preflight, apps-to-close step,
lock, heartbeats and app beats. Not related to practice assignments (`/api/practice`).
- **Exam:** `Exam.is_trial = true`, id `e_sample_exam`, seeded idempotently at every startup
  (`app/trial.py`, column added by `db._add_missing_columns`). Owner: system account
  `__fairplay_sample__` (role `system`, cannot sign in), no class, no start code, 10 minutes,
  4 questions (mcq, numeric, short, essay), `resume_needs_approval = false`.
- `GET /api/student/trial` → `{exam_id, title, duration_min, question_count, attempt: null | {attempt_id, status}}`.
- `GET /api/student/trial/challenge` → `{nonce, expires_in}` (as a real exam's challenge).
- `POST /api/student/trial/start` body `{client?: {device_id, nonce, signature, version, build}}` →
  attempt view + `session` (same shape as a real start; `needs_approval` always false). Same app
  rules as a real start (`FAIRPLAY_REQUIRE_APP`, dev builds, `426`). One attempt per student: an
  attempt still running on the same device continues (same id; a start within 15 s keeps the
  session, later ones get a new session); a finished, timed-out or other-device one is deleted
  with its events and a new one starts (unlimited retakes). The real
  `/exams/e_sample_exam/{challenge,start,package,package-key}` answer `404`.
- Attempt views add `"trial": bool`.
- **Stop practice:** `POST /api/student/attempts/{aid}/trial-stop` (with `X-FairPlay-Session`) →
  `{status: "submitted", submitted_at}`; commits before replying, drops the answers, no score,
  no grading. `403` for any non-sample attempt, `404` for another student's. The page then sends
  bridge `unlock`; the app unlocks only on the signed `GET .../handin` (`submitted`, or `gone`
  for a replaced attempt) like after any hand-in, and app beats answer `status: "submitted"`.
  The page goes to `/student/done/<aid>` (allowed while locked), which waits for the native
  `unlocked` and then shows the dashboard with "That's how an exam works". Normal submit /
  time-up ends the same way (not graded, no score page). Stop is disabled while a lock request
  is on its way, and the page never locks after a stop.
- **No invigilator, sample only:** `GET /api/student/attempts/{aid}/trial-key` → `{key}` = the
  attempt's offline exit key for the current session (`403` for real exams, `409` once ended).
  When Stop can't reach the server, the page turns the Mac / PC / iPad code the app shows into
  the 8-digit offline exit code itself (same rule as "Offline exit"). No seal / "ask the
  invigilator" screens in a sample exam. `lockFailed bad_session` offers "Start again".
- **Invisible to teachers:** no teacher endpoint lists or opens the sample exam, its attempts or
  events (exam lists, live room, results, grading queue and overview, gradebook + CSV, insights,
  student lists / profiles, topics, practice bank, scan, LMS push, readiness, class counts), even
  if it were owned by a teacher (`_own_exam` refuses `is_trial`). Students: not in
  `/api/student/exams` or `/api/student/grades` (`/grades/e_sample_exam` → 404).
- **Native apps need no changes:** the lock, beats and unlock are the existing ones; only the
  signed hand-in ends the lock.

## In-app update (Mac app) (2026-10-22)
Release builds of the Mac app update themselves from the public download repo
(`avazbekkhamidovedu-creator/fairplay-download`, GitHub Releases, tag `vX.Y.Z`). Dev builds never check.
- **Check:** a few seconds after launch, then every 6 h (1 h after a failed check; skipped while
  locked): `GET https://api.github.com/repos/avazbekkhamidovedu-creator/fairplay-download/releases/latest`
  (`Accept: application/vnd.github+json`, a `FairPlay/<version> (Mac)` User-Agent, 10 s timeout,
  `If-None-Match` with the last ETag). An update is offered only when: not a draft/prerelease;
  `tag_name` is `vX.Y.Z` (or `X.Y.Z`) and newer than the app (`AppVersion.compare`); exactly one
  asset is named `FairPlay-Mac.zip`; its `browser_download_url` is exactly
  `https://github.com/avazbekkhamidovedu-creator/fairplay-download/releases/download/<tag>/FairPlay-Mac.zip`
  (GitHub's redirect to its file host is followed); its `digest` is `sha256:<64 hex>`. No digest
  means no update (never an unverified install).
- **State:** the `state` message adds `"update": {"version": "X.Y.Z"}` while such a release is
  known and the app is not locked (omitted otherwise). The app re-sends `state` when a check finds
  one. The page accepts only `{version}` matching `^\d+\.\d+\.\d+$`.
- **Page → app:** `{"action":"installUpdate"}`; `{"action":"openDownloadPage"}` opens the fixed
  page `https://github.com/avazbekkhamidovedu-creator/fairplay-download/releases/latest` in the browser (not while
  locked; the exam window itself only shows the server).
- **App → page:** `{"type":"updateProgress","stage":"downloading"|"installing"}`;
  `{"type":"updateFailed","reason":"download"|"verify"|"not_writable"|"locked"|"busy"}`. On success
  there is no message: the app quits and the new copy opens.
- **Install steps (app):** refused (`locked`) while locked or while an exam session is bound
  (checked again right before the swap); `busy` while one runs; `not_writable` (and the download
  page opens in the browser) when the running bundle or its folder is not writable or the copy
  runs translocated. Then: download the zip (URLSession, so no quarantine attribute) → SHA-256 must
  equal the digest → `ditto -x -k` into a fresh folder that must hold exactly `FairPlay.app`
  (a `__MACOSX` folder is ignored; no links) → its Info.plist `CFBundleIdentifier` = the running
  app's, `CFBundleShortVersionString` = the release version → `codesign --verify --deep --strict` →
  if the running app is signed with a certificate (not ad hoc), the new copy must satisfy the
  running app's designated requirement (an update can't change the signer) → the running bundle
  moves to the Trash, the new one takes its path (the old one is put back on failure) →
  `open -n <path>`, then the app quits. Not locked, so no watchdog marker exists and the quit is
  not reported as a killed exam. Ad-hoc builds lose the Accessibility permission with every new
  signature; the permission screen ("Fix permission") handles that after the restart.
- **Web:** the "Test my device" card shows "FairPlay X.Y.Z is available" + "Update now"
  (`stu-btn sm`) above the checks; while installing "Updating… FairPlay restarts by itself.";
  failures: `download` → "Couldn't download the update. Check the connection and try again."
  (button stays), `locked` → "Can't update during an exam.", others → "Download the new version
  from the download page." with a link to the download page.
- **Releases:** the `FairPlay-Mac.zip` must contain `FairPlay.app` at its top level (CI:
  `ditto -c -k --sequesterRsrc --keepParent`) with `CFBundleShortVersionString` equal to the tag
  (both come from `Config.version` / `RELEASE_VERSION`; keep them the same).

## Exam tools (2026-10-23): calculator, open book, allowed websites
Outside apps are gone: the apps close every windowed app when they lock. What students may
use is built into the FairPlay exam page.
- **Exam fields** (teacher `ExamIn` / exam out, columns with `server_default`, so older
  databases and exams read as off): `calculator: "off"|"scientific"|"graphing"` (default
  `"off"`), `open_book: bool` (default false), `websites: string[]` (plain host names, lowercase,
  no scheme / path / port / `*.`, at most 20; `https://Docs.Python.org/` is stored as
  `docs.python.org`). Website exams always have `off` / false / `[]`. On PUT an omitted field
  keeps its value. `allowed_apps` is still accepted from older editors but always stored and sent
  as `[]` (attempt views, offline packages, exam out).
- **Student:** attempt views (start, GET attempt) and `/api/student/exams` rows add
  `"tools": {"calculator": ..., "open_book": bool, "websites": [...]}`; offline packages built
  since add the same `tools`.
- **Bridge:** the exam page adds `allowed_sites: string[]` (= `tools.websites`, `[]` when none) to
  `{action:"lock", ...}` (online and offline) and `{action:"session", ...}`. The app lets only
  those hosts and their subdomains load in sub-frames of the exam page (the websites pane:
  `<iframe sandbox="allow-scripts allow-same-origin allow-forms">`, no popups, no top
  navigation). The field is absent from messages of older pages: treat a missing field as `[]`.
- **CSP:** `frame-src 'self' https:` (the pane) and `worker-src 'self' blob:` (pdf.js) are added;
  everything else unchanged. The pdf.js worker is `/assets/pdf.worker.min-*.mjs` (served as
  `text/javascript`), its fonts and character maps under `/pdfjs/`.
- **Open-book materials** (`app/materials.py`, table `exam_materials`, bytes in the database):
  - `GET /api/student/exams/{id}/materials` → `{open_book, can_change, reason, files, used_bytes,
    max_files: 5, max_bytes: 52428800, types}`; `POST` (multipart `file`) → 201 file;
    `DELETE .../materials/{mid}` → 204. Changes only while the exam is open book, not closed, and
    the student has no attempt yet (else 409). Limits 5 files / 20 MB in all (409 / 413); type
    from the bytes: PDF (`%PDF-`), PNG, JPEG, or UTF-8 text named `.txt` / `.md` (else 422).
  - During the attempt (`in_progress`, with `X-FairPlay-Session`):
    `GET /api/student/attempts/{aid}/materials` → `{files}`; `.../materials/{mid}` → the bytes as
    `application/pdf` / `image/png` / `image/jpeg` / `text/plain; charset=utf-8`, `inline`,
    `nosniff`, `no-store`, sandbox CSP.
  - Teacher (read only): `GET /api/exams/{id}/materials` → `[{username, name, files}]`;
    `GET /api/exams/{id}/attempts/{aid}/materials` → `{files}`; `GET /api/exams/{id}/materials/{mid}`.
  - File: `{id, name, kind: "pdf"|"png"|"jpg"|"txt"|"md", media_type, size, created_at}`.
  - The exam page fetches the files when the exam opens and keeps them in memory; after a reload
    without the connection it says "Materials need the connection" (the exam keeps working).

### Early upload window, website links, companions (2026-10-24)
- **Exam fields:** `upload_opens_days: 0..7` (default 0; open book only, needs `scheduled_at`);
  `websites` lines may be full links (`https://duke.app.box.com/folder/0`, optionally after a
  title: `Course folder https://...`). The server stores the HOST in `websites` (unchanged format
  for the apps: lowercase hosts, `*.x.com`, at most 20) and the link in
  `website_links: [{host, url, title}]` (≤ 20, `url` always https; anything sent in
  `website_links` is ignored: it comes from `websites`). Exam out adds `website_links`,
  `companions: [{for, name, sites}]` (`app/site_companions.py`) and `upload_opens_days`.
  Website exams: 0 / `[]`. `GET /api/tools/site-check?host=` takes a link too and adds
  `companions: {name, sites} | null`.
- **Student:** `tools` adds `website_links` and `allowed_sites` (= `websites` + companions, at
  most 64): the exam page hands `allowed_sites` to the app (`lock` / `session`), older servers
  without it → `websites`. `/api/student/exams` rows add `upload_window: {opens_at, closes_at} | null`.
  A draft exam is listed while its window is open (`scheduled_at − N days` ≤ now < `scheduled_at`),
  with `question_count: null`, `package_id: null`; start / package / package-key refuse it (409).
  `GET .../materials` adds `upload_opens_at` / `upload_closes_at`. Uploads: a draft only inside
  its window; a live exam whose window had begun takes none (fixed when the window closed, at
  `scheduled_at` or when it was opened); a live exam opened before its window began, or without a
  window, takes them until the student starts (as before). 409 reasons: "Uploads for this exam
  have not opened yet" / "Uploads for this exam have closed" / "You can add your files when your
  teacher opens the exam". File-upload ANSWERS are made in the attempt only.
- **Web:** student pages list it as "Upcoming — upload your files · Uploads close <time>"
  (Home / Exams / course), `/student/exams/<id>/upload` has only the upload area (time left, file
  list with Remove). The websites tab lists each start page (title + link) and opens it first.
- **Site policy (external exams):** `FAIRPLAY_SITE_POLICY_EXCEPTIONS` (exact hosts; unset =
  `duke.app.box.com,duke.box.com`, empty = none) are not refused by the deny-list. See
  `docs/dkuoj-bridge-v2.md` §10 for DKU OJ's side (`POST /exams/<key>/settings`), and §11: DKU OJ
  keeps no FairPlay settings (no schema change there); all of them are set through that endpoint,
  also before the first sync (pending), and its exam list carries `active_keys` instead of `removed` rows.

## Integrations + DKU OJ — FairPlay server as implemented (2026-10-23)
Binding: `docs/dkuoj-bridge.md` plus the product owner's integration rule (core is
source-agnostic). Code: `server/app/integrations/` (`base.py` = `ExternalSource` hooks,
`registry.py` = `integration_for(exam)`, None for FairPlay exams; `dkuoj/` = the DKU OJ source),
`server/app/question_types.py` (the extended question types), `server/app/answer_files.py`.
DKU OJ settings are read only by `integrations/dkuoj/config.py`: `DKUOJ_URL`, `DKUOJ_TOKEN` (both
set = connected; the token may be `new,old` while rotating), `DKUOJ_SYNC_SECONDS` (120),
`DKUOJ_DELIVERY_RPS` (10), `DKUOJ_ALLOW_INSECURE` (plain http to another machine; refused
otherwise). `FAIRPLAY_EXTERNAL_SITE_ALLOWLIST` limits the websites an external exam may open
(`integrations/site_policy.py`). `FAIRPLAY_PUBLIC_URL`
(FairPlay's own address) makes report links absolute (`<it>/teacher/exams/<id>/submissions/<aid>`);
DKU OJ drops links that are not http(s).
A switched-off integration keeps its exams readable as read-only archives (they cannot start).

### Extended question types (FairPlay types, usable by FairPlay's own editor later)
Plain questions keep their exact shapes. A question with the extended fields adds:
`prompt_format` (`"markdown"`: FairPlay markdown with tables, fenced code, math `~…~` inline and
`$$…$$` display, images `fp-asset:<name>`; or `"html"`: sanitized HTML), `title`, `label`,
`partial`, and by type:
- `mcq`: `multiple`, `true_false`, `shuffle` (the page may shuffle the display; answers stay
  indexes into `options`). Answer: int index; with `multiple`: list of indexes (stored sorted).
- `fill_blank` (new type): `blanks` (count), `blank_points`, `case_sensitive`. Answer: list of
  strings (at most `blanks`).
- `short`: answer string.
- `file_upload` (new type): `allowed_extensions`, `max_file_size`, `max_files` (DKU OJ: 1),
  `instructions_md`. Answer: list of answer-file ids (at most `max_files`).
- `code`: `languages: [{key, name, runner}]` (runner `python|javascript|c|cpp|java|null`; null =
  "runs after hand-in"), `templates: {key: code}`, `memory_limit_kb`; also `language` (first
  runner), `starter_code` (first template), `tests` (samples `{input, expected}`), `time_limit_ms`.
  Answer: `{"language": "<key>", "source": "…"}` (a bare string = first language).
Wrong shapes → 422 `"<label>: <reason>"`. Plain questions take only numbers, strings or null.
Teacher views add `external_ref` (the external system's ids); students never see it. Question
ids of external exams are opaque (`q_` + 16 hex).

### API fields
- **Teacher exam**: `"external": null` for FairPlay exams, else `{"source", "name", "url",
  "read_only", "graded_externally", "package_ready", "start_time", "end_time", "key", "version",
  "frozen_version", "synced_at", "removed", "connected"}`. The external system's editors see the
  exam (list, live room, open/close/extend/codes) like its owner. Read-only exams: `PUT` → 409
  `"This exam comes from <name>. Edit this exam in <name>."`; delete → 409 until the source removed
  it; grades / comment / release / unrelease → 409 `"This exam is graded in <name>. …"`; `open` →
  409 until the package is here. Assets: `GET /api/exams/{id}/assets/{name}`.
- **Results** (`GET /api/exams/{id}/results`): top-level `"external"` (same block or null); per
  attempt of an external exam `"external_result"` (`{released, total, total_points, updated_at,
  questions: [{question_id, points, feedback_md}]}` or null) and `"external_delivery"`
  (`{revision: 1..n, status: pending|sent|failed|dead, tries, last_error, receipt, next_at, sent_at}` or null, 2026-10-30).
  Revisions: a hand-in starts with revision 1; each reopen increments it. DKU OJ must keep earlier hand-ins (by revision) labelled and count only the latest for any scoring or grade. Scores are the released external totals (0 before). A dead delivery adds the event
  `external_delivery_failed` to the attempt.
- **Grading queue** (`GET /api/exams/{id}/grading`) gains `"external"` (null for FairPlay); graded
  externally → `{exam_id, title, course, status, grades_released: false, external: {...},
  questions: [], items: []}`.
- **Student**: `/api/student/exams` rows, attempt views (start / GET attempt), `/api/student/grades`
  rows and `/api/student/grades/{id}` carry `"source"` (`"fairplay"` or the integration id) and,
  when external, `"external_name"`. External attempt views add `"total_points"`. Attempt views of
  exams with file_upload questions add `"answer_files": [{id, question_id, name, size, sha256,
  created_at}]`. External grade detail (403 until released by the source): the usual shape plus
  `result_updated_at`; each question = the student question + `question_id`, `index`,
  `points_awarded`, `status` (`correct|partial|wrong|unanswered|pending`), `given`, `comment`
  (markdown), `comment_format: "markdown"`, `correct_answer: null`.
- **Answer files**: `POST /api/student/attempts/{aid}/files?question_id=<qid>` (multipart `file`,
  `X-FairPlay-Session`) → 201 `{id, question_id, name, size, sha256, created_at}` (extension / size
  checked); `GET .../files/{id}` → the bytes.
- **Assets** `fp-asset:<name>` → `GET /api/student/attempts/{aid}/assets/<name>` (Bearer +
  `X-FairPlay-Session`; attempt owner, exam live or attempt in progress; ETag = sha256), and for
  the student's released grade page `GET /api/student/grades/{exam_id}/assets/<name>` (Bearer
  only; 404 unless the source released that student's result).
- **Live-room link**: `GET /integrations/<source>/live?…` (DKU OJ: contract §1 parameters) → 303
  `/teacher/external-live?code=<one-time, 60 s>`; `POST /api/integrations/live/exchange {code}` →
  `{token, role: "teacher", name, username, exam_id}`. Each signed link works once.
- `GET /api/integrations/<source>/status` (teacher) → `{enabled, source, name, url, last_run,
  last_error, exams, deliveries: {pending, failed, sent, dead}, problems: [...]}` (`{enabled:
  false, source, name}` when not connected).

### Professor rescue actions (2026-10-30)
Five endpoints let a professor help a student in an emergency without restarting them. Each is called with the student's exam key and NetID, requires an editor of the exam, answers `Cache-Control: no-store`, and audits the acting user and reason.
- **Release device** (POST `/api/integrations/dkuoj/v1/exams/<key>/students/<netid>/release-device`) body `{"reason": "..."}` → frees the device, unbinds the access code, and keeps answers and timer. Rate limit: 3 per student per exam, counted over 30 days.
- **Reissue code** (POST `/api/integrations/dkuoj/v1/exams/<key>/access-codes/reissue`) body `{"netid": "...", "reason": "..."}` → issues a new code, returned once in the response. Rate limits: 3 per student per exam (counted over 30 days), 30 per exam per hour.
- **Reopen** (POST `/api/integrations/dkuoj/v1/exams/<key>/students/<netid>/reopen`) body `{"minutes": 5..60, "reason": "..."}` → restores submitted answers, frees device, sets a new deadline. Allowed once per attempt, while live and not graded.
- **Extra time** (POST `/api/integrations/dkuoj/v1/exams/<key>/students/<netid>/extra-time`) body `{"minutes": 1..120, "reason": "..."}` → adds time, capped at 240 total per student per exam.
- **Offline exit code** (POST `/api/integrations/dkuoj/v1/exams/<key>/students/<netid>/offline-exit`) body `{"mac_code": "...", "reason": "..." | null}` → unlocks a locked Mac. Rate limit: 20 per student per hour.
See `docs/dkuoj-bridge-v2.md` §3 for full details.

### DKU OJ client behaviour (aligned with the DKU OJ patch)
404 = bridge off / unknown; 401, 403, 409, 413, 422, 400 on a hand-in → dead + alert, never
retried; 5xx / 429 / timeout → retried (30 s doubling to 15 min, 14 days). A package refused
with 4xx (e.g. 422 over 5 MB) is not asked for again until the listing shows a new version; 425
waits for `Retry-After`. Exam cursor = newest `updated_at` seen (`changed_since` is strictly
later); results cursor = the response's `server_time`. Removed exams (incremental polls only)
are marked removed and keep their editors. One upload per file_upload answer; a blank code or
short answer is not sent.

## Open to selected students + automatic close (2026-10-23)
Generic for every exam (FairPlay and external). Code: `server/app/access.py` (open_to),
`server/app/closing.py` (the one close path, the automatic close loop), hooks in
`server/app/integrations/base.py`.

### Open to everyone or to selected students
- Exam column `open_to` (JSON, null = everyone the exam is for). API value: `"all"` or the sorted
  list of student user ids. Teacher exam view (`GET/PUT /api/exams/{id}`, open, close, …) adds
  `"open_to"`.
- `POST /api/exams/{id}/open` takes an optional body `{"open_to": "all" | [ids]}`; no body =
  `"all"` (as before; a re-open without a body opens to everyone again). Every id must be a student
  of the exam, else 422 `"Not a student of this exam: <ids>"`.
- `GET /api/exams/{id}/students` → `[{id, username, name, started}]`: the class members, the
  linked LMS course's members, or the external source's roster (`ExternalSource.roster_user_ids`).
- `PUT /api/exams/{id}/open-to` `{"open_to": "all" | [ids]}` replaces the selection, or
  `{"add": [ids], "remove": [ids]}` changes it (removing from "all" = everyone else). Any status
  except paper; returns the teacher exam. Removing a student who already started never stops
  their attempt; it only blocks a new start.
- `GET /api/exams/{id}/live` adds `"open_to"`, `"selected"` (`[{id, username, name, started}]`, or
  null when open to all) and `"closes_at"`.
- Student start: a student who is not selected and has no attempt gets 403
  `{"detail": "This exam isn't open for you yet"}` before the app proof and the start code are
  checked (not a wrong code, no lockout). Checked again in the start's locked transaction (a
  removal that commits first wins). A resume of an existing attempt is never blocked. The offline
  package and `package-key` give the same 403.
- `/api/student/exams` rows add `"open_for_me"` (false = live but not open for this student yet;
  `package_id` is then null). Rows of exams the student already started are always true.

### Automatic close
- Exam columns `closes_at` (naive UTC, nullable) and `auto_closed` (bool). Effective close time:
  FairPlay exams their own `closes_at` (exam editor "Close automatically at", optional, `ExamIn.
  closes_at` with a time zone, like `scheduled_at`); external exams `ExternalSource.closes_at(exam)`
  (DKU OJ: the synced `end_time`, no extra call), never the column. Teacher exam view adds
  `"closes_at"` (effective, ISO or null) and `"auto_closed"`.
- New attempts: `deadline_at = min(start + duration + extra, closes_at + extra)` (extra = the
  student's extra time from the source, 0 for FairPlay exams). A new start with no time left →
  409 `"This exam has ended"`. The attempt view's `deadline_at` is this capped value.
- The offline package plaintext adds `"closes_at"`; an offline start counts down to
  min(start + duration, closes_at).
- Background loop (lifespan, every 15 s; one worker via the `leases` row `"autoclose"`): a live
  exam closes once now ≥ closes_at + the largest extra time (`ExternalSource.max_extra_time`) +
  LATE_GRACE (60 s) and every attempt still writing is past its own deadline + LATE_GRACE (so
  "Add 10 min" is honoured). It uses the teacher's Close path (`closing.close_exam_now`):
  conditional status flip (two runners, or a runner and the teacher, close once), auto-submit of
  in-progress attempts, outbox rows for external exams, `auto_closed = true`, and the bookkeeping
  event `exam_closed_automatically` on each attempt it handed in (not a flag).
- Opening stays manual. Re-opening a FairPlay exam after its close time clears `closes_at` (the
  teacher closes it by hand); an external exam whose source end time has passed can't be opened
  (409 `"The end time set in <name> has passed. …"`).
- Review fixes (2026-10-23): a never-opened (draft) FairPlay exam whose close time has passed is
  not opened (409 `"The automatic close time has passed. …"`); `ExamIn.closes_at` must be after
  `scheduled_at` when both are sent (422). `PUT /open-to` with nothing to add or remove keeps
  `"all"`. A close that stopped between the status flip and the hand-in (crash) is finished by
  the loop: exams closed in the last 2 days (and over 30 s ago) with attempts still writing get
  them handed in (outbox rows for external exams). DKU OJ moving its end time later while the
  exam is live moves the capped deadlines of running attempts with it (never shorter).


# Canvas mode (Mode B), server, 2026-10-25

Full design: `docs/contract-canvas-mode-b-2026-10-25.md`. This section is what the server does.

## Exam fields (teacher POST/PUT, mode "external" only; 422 otherwise)

```
"external_kind": null | "canvas"
"canvas": {"quiz_type": "classic"|"new"|"assignment",
           "removed_hosts": ["..."],      // auto-added sign-in hosts the teacher removed
           "open_book": false}             // default false; omitted on PUT keeps the saved value
"finish_url_regex": null | "..."          // any external exam; filled for canvas exams
```
- `external_url` must be https for canvas. `quiz_type` defaults from the link (`/courses/:c/quizzes/:q` classic, `/courses/:c/assignments/:a` new); 422 when it cannot be inferred. A PUT with `external_kind: "canvas"` and no `canvas` block keeps the stored quiz type. There is no access code: `canvas.access_code_source`, `canvas.access_code` and the open body field `canvas_access_code` are removed; old clients may still send them and they are ignored (never stored, never returned).
- `allowed_domains` is rebuilt on every save: the Canvas host, for `new` the `*.quiz-lti-<region>-prod.instructure.com` hosts (regions iad, pdx, dub, fra, sin, syd, yul), the sign-in hosts from `FAIRPLAY_CANVAS_SSO_HOSTS` (comma list; default `*.duosecurity.com, login.microsoftonline.com, login.microsoft.com, shib.oit.duke.edu, go.canvas.duke.edu, sso.canvaslms.com`) except those in `canvas.removed_hosts`, then the teacher's additions. `removed_hosts` (lowercased) is stored, returned in the teacher view and kept by later PUTs until a `canvas` block sends a new list; it may not contain the Canvas host (422), and the Canvas host and (for `new`) the quiz-lti hosts are always present. A PUT without `allowed_domains` keeps the stored teacher additions. Every host passes `site_policy`; a denied teacher addition or Canvas host is 422. An auto-added sign-in or quiz-lti host that `site_policy` drops is not silent: the teacher view's `canvas_notice` names it.
- `finish_url_contains` is ignored for canvas exams (stored `null`): only the regex decides.
- `finish_url_regex`: default per quiz type (`classic` `/quizzes/\d+/(submissions/\d+|history)`, `new` `submission-confirmation|submission-screen`, `assignment` `/assignments/\d+/submissions/\d+`). A value equal to any default is treated as "not overridden". Max 300 chars. The accepted dialect is the plain subset Python `re` and ICU read the same: groups `(...)` and `(?:...)`, a leading `(?i)`, classes without nesting or set operators, escapes `\d \D \w \W \s \S \b \B` and escaped punctuation, `* + ? {n} {n,} {n,m}` with an optional lazy `?`. Refused (422): backreferences, lookaround, conditionals, named groups, atomic or possessive forms, `{,n}`, unescaped `{` `}` that are not a repeat, other escapes (`\Z`, `\p{..}`), repeats of repeats, a group that contains `|`, a repeat or another group followed by `*`, `+` or `{...}` (the apps refuse the same shapes), more than 2 open-ended repeats (`*`, `+`, `{n,}`) or two of them in a row that can match the same characters (below), more than 16 repeats in all, counts above 100, patterns that do not compile.
- Exams that are not canvas keep `external_kind`, `canvas` and (unless a regex is set) `finish_url_regex` as `null`.

- `canvas.scope` is derived from `external_url` on every save and is never accepted from a client: `{"course_id": "123", "item_kind": "quizzes"|"assignments", "item_id": "456"}` (digits only, at most 20 each) for a link whose path is `/courses/C/quizzes/Q` or `/courses/C/assignments/A`, optionally followed by `/` and more path (`/take`, `/edit`, `/submissions/9`); `/quizzes/456x` gives none. A POST/PUT of a canvas exam whose link gives no scope is 422 "The link must open one quiz or assignment, like https://<canvas>/courses/123/quizzes/456". Exams saved earlier without a scope keep working and report `scope: null`.
- A canvas `external_url` may not contain a backslash, white space or control character, `%`, `;`, a `..` path segment, a user name before the host (`@`), or a host ending in a dot (422); `scope` is null for such a link.
- `finish_url_regex` (canvas): at most 2 open-ended repeats (`*`, `+`, `{n,}`) in total, and two open-ended repeats directly after each other are refused unless both are single literals that differ (compared lower-case) or `\d`/`\w` against `\s`, a literal against a `\d`/`\w`/`\s` that cannot match it; `.`, classes, groups, `\D \W \S \b` count as overlapping.
- Teacher `canvas` view also has `sign_in_hosts`: the configured sign-in host list.
- `canvas.open_book` (bool): column `canvas_open_book`, reset to false when the exam stops being a canvas exam.
- The student attempt view of a canvas exam carries `canvas_scope`: `{"course_id": "123", "item_kind": "quizzes"|"assignments", "item_id": "456", "open_book": false, "sign_in_hosts": [...], "quiz_lti_hosts": [...]}` (`null` when the link has no scope; absent for other exams). `sign_in_hosts` is the configured sign-in list minus the teacher's removals and policy-blocked hosts, as stored in `allowed_domains` (exact hosts or `*.host`); `quiz_lti_hosts` is the New Quizzes list for `new` exams, else `[]`. The web page passes the object unchanged in the `openExternal` bridge message.

## Views

Teacher exam and student exam/attempt views add `external_kind`, `canvas: {quiz_type, scope, open_book}` and `finish_url_regex`. The student attempt view of a canvas exam also carries `canvas_scope`: `{course_id, item_kind, item_id, open_book}` or `null` when `canvas.scope` is null (the key is absent for every other exam). Teacher views also add `canvas.removed_hosts`, `canvas.sign_in_hosts` and `canvas_notice` (plain-language string or null: sign-in hosts the server policy blocked).

## Opening, closing and platforms

- Opening and closing a canvas exam work like any other exam: no Canvas call is made, so opening cannot fail because of Canvas and no Canvas account link is needed. The teacher builds the quiz in Canvas with no access code.
- Removed 2026-10-26: managed code set/clear, `canvas_code_failed` (409), `canvas.code_status`, `canvas.remove_code_in_canvas`, `POST /api/student/attempts/{aid}/canvas-code` and its proof, replay and rate-limit rules, the events `canvas_code_issued`, `canvas_code_refused`, `canvas_code_failed`.
- Platform gate: `POST /api/student/exams/{id}/start` of a canvas exam needs a platform header (`mac`, `windows`, `ipad`; `ios` is read as `ipad`) that has a minimum Canvas version set. An app below it, or with a missing or unreadable `X-FairPlay-Version`, gets 426 `{"detail": "Update FairPlay to continue", "min_app_version": "..."}`. A platform with no minimum, a browser and an unknown platform get 409 "Canvas exams are not available on this device yet". Settings: `FAIRPLAY_CANVAS_MIN_VERSION_MAC` (default `0.7.2`; 0.7.0 and 0.7.1 still have the removed code typer and no lock-in), `FAIRPLAY_CANVAS_MIN_VERSION_WINDOWS` and `FAIRPLAY_CANVAS_MIN_VERSION_IOS` (default empty = not supported yet). A value must look like `0.7.2` (a leading `v` is dropped), else the server refuses to start; "" turns a platform off. To enable Windows or iPad when its app ships, set the variable to the first app version that implements the restricted Canvas browser and lock-in rule, and restart the server.
- A canvas exam whose `external_url` has no scope (older data) is never started (409 "This Canvas exam link needs to be fixed by your teacher") or opened (409); the teacher view's `canvas_notice` says to fix the link.
- At startup the server sets `canvas_code_enc` to NULL on every row (no Canvas calls). A code that an earlier test build left on a Canvas quiz must be cleared by the teacher in Canvas.
- Integrity events: `canvas_quiz_started` (the app entered QUIZ phase; bookkeeping, not a flag; `client_canvas_quiz_started` from older builds is treated the same) and the client-reportable `canvas_upload_unsupported`, `canvas_sso_popup`.

## Lock-in

After the quiz page has loaded (phase QUIZ in `docs/contract-canvas-mode-b-2026-10-25.md`) the student has no way to end the exam session except (a) `finish_url_regex` matches, (b) the teacher or proctor unlocks or hands the attempt in from the server side, (c) the existing emergency/safety unlock. The app shows no exit or "I'm done" button in QUIZ phase of a canvas exam; Back and "Go to my quiz" stay within the exam's item.

## Storage

Exam columns added (added to older databases by `db._add_missing_columns`): `external_kind`, `canvas_quiz_type`, `canvas_removed_hosts`, `canvas_open_book`, `finish_url_regex`. The columns `canvas_code_source`, `canvas_code_enc`, `canvas_code_failed`, `canvas_release_failed` and `canvas_open_claim` stay in the table for older databases but are no longer read or written; rows that hold them load as before.

# Student sign-in with an access code (2026-10-28)

Codes are made per student and exam (`docs/dkuoj-bridge-v2.md` §12). All three routes are under `/api/student`, so the browser gate hides them from a browser.

- `GET /api/student/code-login/challenge` → `{nonce, expires_in}`. The nonce is single use. The app signs it with the existing `attest` bridge action (`start|<nonce>|<device_id>`), so no app change is needed.
- `POST /api/student/code-login` `{code, client: {device_id, nonce, signature, version?, build?}}` → `200 {first_name, last_name, netid, exam_title, confirm_token}` (`Cache-Control: no-store`). The code is 6 characters (letters and digits, upper or lower case, spaces and dashes ignored). `client` is required unless `FAIRPLAY_REQUIRE_APP=0`. Nothing is stored yet. `confirm_token` is signed, single use, valid 5 minutes. A wrong, unknown, revoked, expired, other-exam or second-device code all give `400 {"detail": "That code is not valid."}`. A code works from 30 minutes before the exam's `scheduled_at` (or whenever the exam is live) until it closes (close time plus the largest extra time).
- Too early: a valid, unrevoked code for a draft exam whose `scheduled_at` is more than 30 minutes away answers `400 {"detail": "too_early", "starts_at": "<UTC ISO, trailing Z>"}` (`Cache-Control: no-store`) instead of the generic 400. It is counted like a failure (per network, device, and globally). A device other than the one the code is bound to gets the generic 400 and an `access_code_second_device` event instead. An unbound device still gets `too_early` with `starts_at`. A draft with no `scheduled_at`, a live exam that is closed, a closed exam, and every other refusal keep the generic 400. No exam name, NetID or first/last name is returned.
- Limits (failed tries only, including `too_early` answers): 30 a minute per source network (an IPv6 /64, an IPv4 address), 5 a minute and 20 an hour per device id. Global failure budget: 600 per 10 minutes and 1500 per hour across all sources together, checked in a sliding window. Over any limit: `429` with `Retry-After`; even the right code waits. Failures with a known exam, and every sign-in, are written to the exam's class events (`access_code_failed`, `access_code_signin`, `access_code_second_device`); the code is never stored or logged.
- `POST /api/student/code-login/confirm` `{confirm_token}` → `200 {token, role: "student", name}`, the same as a student login. First use binds the code to the device (`bound_device`, `used_at`). The account is found by NetID or created with no usable password. A second device gets the generic 400 and an `access_code_second_device` event (also on the student's attempt when there is one); the same device may sign in again; after the teacher's release-device or an approved swap the next device may bind. A used, expired or unknown token: `400` "This confirmation has expired. Enter your code again.". When a roster sync removes a student (all NetIDs on the exam's list), their codes are revoked; if the student returns to the list in a later sync, they get a fresh code with a new binding.
- Start: `POST /api/student/exams/{id}/start` needs no `start_code` when the student's code for that exam is bound to the device that sends the signed start proof. Every other start still needs it. The exam must be live.
- Exam list: an exam with codes has `"access_code": true` (key absent otherwise) and `package_id: null`. A draft exam the student signed in for, scheduled within 30 minutes, is listed with `status: "draft"` and `question_count: null` ("waiting for the exam to open").
- `GET .../package` and `.../package-key` of an exam with codes: `409 "This exam is taken online and has no offline copy"`.

## Code sessions, grades switch, legacy screens (2026-10-28)

- The token from `code-login/confirm` carries a sixth field, the exam id it is scoped to. With it, `/api/student/exams/{other id}/...`, `trial`, `practice` and `classes` answer 403, and the exam list holds that exam only. It stops working (401) 5 minutes after the attempt's real `submitted_at`, and 30 minutes after the exam's close time for a student who never started. Nothing is deleted. A second exam needs a new code.
- `GET /api/me` adds `netid` when the account has one.
- `/api/health` adds `student_grades` (bool, `FAIRPLAY_STUDENT_GRADES`, default off) and lists `legacy_student_ui` in `features` when `FAIRPLAY_LEGACY_STUDENT_UI=1`. With `student_grades` off, `GET /api/student/grades` and `/grades/{id}` answer the same JSON 404 as an unknown path; the web hides Grades and Past. Without `legacy_student_ui` the web hides class joining, course cards and the old home page.
- `GET .../access-codes.pdf` takes `layout=both|list|slips` (`docs/dkuoj-bridge-v2.md` §12).

## Homework sign-in (DKU OJ web sign-in in the app)

Off unless `FAIRPLAY_HOMEWORK_LOGIN=dkuoj`. It needs the DKU OJ bridge (`DKUOJ_URL`, `DKUOJ_TOKEN`) and
`DKUOJ_PUBLIC_URL`, the https address students open DKU OJ at (http only for loopback with `FAIRPLAY_DEV=1`);
otherwise the server refuses to start. Extra settings: `HOMEWORK_LOGIN_HOSTS` (comma list of other hosts the
web view may open: Duke NetID, Shibboleth, Duo; default empty) and `HOMEWORK_LOGIN_RESUME_PATHS` (default `/`).
When off, the routes below answer 404.

- `GET /api/health` gains `homework_login`: `null` or `{"provider": "dkuoj", "label": "DKU OJ"}`.
- `POST /api/homework/login/start` `{}` -> `{state, start_url, allowed_hosts[], finish_url, resume_paths[], expires_at}`.
  `state` is 32 random bytes (base64url, 43 chars), kept 10 minutes with a 32-byte verifier that never leaves the
  server. `start_url` = `<DKUOJ_PUBLIC_URL>/fairplay/app-login/?state=S&challenge=b64url(sha256(verifier))`,
  `finish_url` = `<DKUOJ_PUBLIC_URL>/fairplay/app-login/done`. `allowed_hosts` = the DKU OJ host + `HOMEWORK_LOGIN_HOSTS`.
  20 per minute per IP (429). No auth.
- The web view opens `start_url`. DKU OJ signs the student in, mints a one-time ticket (60 s) and sends the view to
  `finish_url#ticket=T&state=S`. The app reads both from the fragment.
- `POST /api/homework/login/finish` `{state, ticket}` -> `{token, name, netid, expires_at}`. No auth. The state is
  deleted on first use. 400 = state or ticket unknown / used / expired / not matching (one message for all);
  403 = no DKU OJ classes, or a teacher account; 429 = more than 10 per minute per IP or 20 per hour per NetID;
  503 = DKU OJ unreachable. Creates `oj:<netid>` (unusable password, `set_up_at` NULL) when the NetID has no account.
- `GET /api/homework/me` -> `{name, netid}`. Accepts a homework token or a plain student token; an exam-scoped or
  admin-test token gets 403.
- The token has scope `homework`, role student, 8 hours. Every route that needs `require_student` (all of
  `/api/student/*`, exams, attempts, materials, offline, practice, classes) answers 403 to it. It does not set up
  an account, cannot call `/api/setup/*` and does not touch `DKUOJ_SETUP_ACCESS_CODE`.

DKU OJ side (fp -> oj signed, `docs/dkuoj-bridge-v2.md` §1): `POST /api/fairplay/v1/app-login/redeem`
`{ticket, verifier}` -> 200 `{netid, first_name, last_name, email}`; 404 ticket unknown, used or older than 60 s;
409 `base64url(sha256(verifier))` is not the `challenge` the ticket was minted with. The ticket is spent by any
answer except 404. FairPlay then calls `GET /api/fairplay/v1/students/<netid>/classes`.

## Homework session expiry and remember me

Same switch as the homework sign-in (`FAIRPLAY_HOMEWORK_LOGIN`); with it off every route below answers 404.
Replaces "8 hours" above.

- The homework token carries `login_at` (payload `...:homework:<login_at>`; exam tokens and older homework tokens
  parse as before, an older one counts as signed in 8 hours before its expiry). `exp = min(now + 30 min, login_at + 4 h)`.
- `POST /api/homework/login/finish` -> `{token, name, netid, expires_at, absolute_expires_at, idle_seconds}`;
  `expires_at` is the idle expiry, `idle_seconds` is 1800.
- `GET /api/homework/me` -> `{name, netid, expires_at, absolute_expires_at}` (the two dates are absent for a plain
  student token).
- `POST /api/homework/session/renew` (Bearer homework token) -> `{token, expires_at, absolute_expires_at}`. The new
  token keeps `login_at`. 401 when the token is expired or `login_at + 4 h` has passed; 403 for any other token;
  429 above 12 per minute per user.
- `POST /api/homework/remember/create` (Bearer homework token) `{label?}` (trimmed to 40 chars) ->
  `{remember_token, expires_at}`. The token is 32 random bytes (base64url, 43 chars), shown once, stored as sha256
  (table `homework_remember`). Lives 14 days from creation. A user keeps 5 devices; the 6th revokes the oldest.
- `POST /api/homework/remember/exchange` (no auth) `{remember_token}` ->
  `{token, expires_at, absolute_expires_at, remember_token, remember_expires_at, name, netid}`. Every exchange
  replaces the token (store the new one); `remember_expires_at` stays the family's original 14-day date. The new
  session has a fresh `login_at`. 401 `"This device is no longer remembered. Sign in again."` for unknown, expired,
  revoked or malformed tokens; presenting a token that was already replaced revokes the whole family (401).
  403 when the account is a teacher or has no classes any more (the token is kept); 503 DKU OJ unreachable;
  429 above 20 per minute per IP or 10 per hour per user.
- `POST /api/homework/remember/forget` `{remember_token}` (no auth) -> `{ok: true}` and revokes that device;
  unknown tokens also answer `{ok: true}`. `POST /api/homework/remember/forget-all` (Bearer homework token) ->
  `{ok: true, revoked: n}`.
- A remember token is not a bearer token: every other route answers 401 to it. Neither token is logged (a sha256
  prefix is). The homework token is still refused on all exam routes.
