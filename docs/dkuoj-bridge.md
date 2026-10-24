# DKU OJ bridge — build contract (v1)

Teachers build and grade assignments in DKU OJ. If the teacher picks **FairPlay** as the lock, students take the assignment only in the FairPlay app. FairPlay's server downloads the exam before it starts, runs it from its own copy, sends each student's answers back to DKU OJ after hand-in, and shows released results. Full design: the "FairPlay ↔ DKU OJ exam integration spec" doc. This file is the binding contract for the three parts being built now: the DKU OJ patch, the FairPlay server, the FairPlay web UI.

**Keep it simple (product owner's rule):** no SSH keys, no per-request signatures, no key admin pages. One shared secret set once in each server's settings is the only credential.

## 1. Credential

- DKU OJ setting `FAIRPLAY_TOKEN` (string, ≥ 32 chars) and `FAIRPLAY_URL` (e.g. `https://exam.speakupai.uz`). Unset token = the whole bridge is off (endpoints return 404, the FairPlay lock option is hidden).
- FairPlay env `DKUOJ_URL` (e.g. `https://oj.example.edu`) and `DKUOJ_TOKEN` (the same value). Unset = bridge off.
- FairPlay → DKU OJ: `Authorization: Bearer <token>` on every call to `/api/fairplay/v1/*`. DKU OJ compares with `hmac.compare_digest`. HTTPS in production.
- DKU OJ → FairPlay (only the "Open live room" button): a link `GET {FAIRPLAY_URL}/integrations/dkuoj/live?exam=<key>&user=<username>&exp=<unix seconds, now+120>&sig=<hex HMAC-SHA256(token, "live|<key>|<username>|<exp>")>`. FairPlay checks the signature, expiry, and that `user` is an editor of that exam (from the last sync), then signs that teacher in and opens the live room. No password for teachers in FairPlay.

## 2. Teacher settings in DKU OJ (assignment editor)

In the anti-cheating box, the lock becomes one choice: **No lock · Safe Exam Browser · FairPlay**. Choosing FairPlay shows a small panel (all optional, sensible defaults):

| Field (on `Contest`) | Type | Default | FairPlay meaning |
| --- | --- | --- | --- |
| `fairplay_required` | bool | false | the assignment runs in FairPlay and is hidden from students on DKU OJ |
| `fairplay_calculator` | `off` / `scientific` / `graphing` | `off` | built-in calculator |
| `fairplay_open_book` | bool | false | students may add their own notes/files before starting |
| `fairplay_websites` | text, one host per line | empty | websites students may open inside the exam (e.g. `docs.python.org`, or a full link like `https://duke.app.box.com/folder/0`, v2 §10); FairPlay drops AI-assistant, messaging and file-sharing sites (Duke Box is an exception) and, when set, any site not on its `FAIRPLAY_EXTERNAL_SITE_ALLOWLIST` (v2 §3 `websites_blocked`) |
| `fairplay_strong_lock` | bool | false | only the strongest lock counts (Windows separate desktop, Mac assessment mode) |
| `fairplay_resume_needs_approval` | bool | true | after a crash a student waits for the invigilator |

SEB and FairPlay cannot both be on. Same permission as the SEB option (`contest_seb_requirement`). Below the panel, on the assignment page: an **Open FairPlay live room** button for editors (signed link, §1) and, once results exist, a "FairPlay report" link per student.

## 3. Hiding rule (DKU OJ)

While `fairplay_required` is on, non-editors (students, other teachers' students, anonymous) cannot see or reach the assignment or any of its problems on DKU OJ: contest page, join, problem pages, statement PDF/preview, contest PDF/Word export, submission lists/filters/counts, `/api/v2`, test-case and tester downloads, editor attachments of those problems, workspace JSON, practice submits. One function `fairplay_hidden_for(user, obj)` used everywhere. Problems stay hidden until the assignment has ended AND results are finalized/released; a problem reused in another not-yet-ended FairPlay assignment stays hidden until that one ends. Editors, testers, course admins and staff see everything as today.

## 4. DKU OJ endpoints (`/api/fairplay/v1/`, Bearer token)

All JSON, UTF-8, times ISO-8601 UTC with `Z`.

**`GET exams?changed_since=<iso>&page=<n>`** — assignments with `fairplay_required` on (and recently switched off, once, with `"removed": true`). Ordered by `updated_at, key`; 100 per page; `next_page` null at the end.
```json
{"exams":[{"key":"cs101a-midterm","name":"CS 101 Midterm","course":{"id":12,"name":"CS 101"},
  "classes":[{"id":31,"name":"CS 101 Section A"}],
  "start_time":"2026-11-27T01:00:00Z","end_time":"2026-11-27T03:00:00Z","time_limit_seconds":5400,
  "results_hidden":true,"package_version":"7f3a9c1e","removed":false,
  "settings":{"calculator":"off","open_book":false,"websites":["docs.python.org"],"strong_lock":false,"resume_needs_approval":true,
              "review":"nothing","review_updated_at":null},
  "editors":[{"username":"sd123","first_name":"Shuai","last_name":"Dong","email":"sd123@example.edu"}],
  "roster":[{"username":"ax19","first_name":"Avazbek","last_name":"Khamidov","email":"ax19@example.edu","extra_time_seconds":0}],
  "updated_at":"2026-11-20T09:14:00Z"}],
 "next_page":null,"server_time":"2026-11-20T09:15:02Z"}
```
`settings.review` / `review_updated_at`: student review after hand-in (v2 §8, 2026-10-24).
2026-10-24 (v2 §10): `settings.websites` entries may also be full links (`https://duke.app.box.com/folder/0`,
optionally after a title): FairPlay keeps the host on the allow-list and the link as a start page; optional
`settings.website_links: [{url, title}]` and `settings.upload_opens_days` (0–7) are read when present (DKU OJ does
not send them today; its schema does not change). Values set with v2 `POST /exams/<key>/settings` win over these.
`package_version` = first 16 hex of sha256 over the canonical package JSON (it changes whenever anything in the package changes). `time_limit_seconds` null = the whole window.

**`GET exams/<key>/package`** — 404 if not a FairPlay assignment; `425` + `Retry-After` earlier than 24 h before `start_time`. `ETag: "<package_version>"`, honours `If-None-Match` (304).
```json
{"key":"cs101a-midterm","package_version":"7f3a9c1e","total_points":100,"description_md":"Closed book.",
 "questions":[
  {"id":"p000123","order":1,"label":"Q1","title":"Hash maps","type":"choice","points":10,"partial":false,
   "statement_md":"Which structure gives O(1) average lookup?","statement_html":null,
   "choice":{"multiple":false,"true_false":false,"shuffle":true,"options":[{"id":501,"text_md":"Array"},{"id":502,"text_md":"Hash table"}]}},
  {"id":"p000125","order":2,"label":"Q2","title":"Fill","type":"fill_blank","points":6,"partial":true,"statement_md":"A ___ maps keys to values; lookup is O(___).",
   "fill_blank":{"blanks":[{"index":0,"points":3},{"index":1,"points":3}],"case_sensitive":false}},
  {"id":"p000126","order":3,"label":"Q3","title":"Explain","type":"short_answer","points":10,"partial":true,"statement_md":"Explain …","short_answer":{}},
  {"id":"p000127","order":4,"label":"Q4","title":"Report","type":"file_upload","points":10,"partial":true,"statement_md":"Upload …",
   "file_upload":{"allowed_extensions":["pdf","png","jpg"],"max_file_size":10485760,"instructions_md":""}},
  {"id":"p000124","order":5,"label":"Q5","title":"Reverse a list","type":"code","points":30,"partial":true,"statement_md":"Write …",
   "code":{"languages":[{"key":"PY3","name":"Python 3"},{"key":"CPP17","name":"C++17"}],"time_limit_s":2.0,"memory_limit_kb":262144,
           "templates":{"PY3":"def reverse(head):\n    pass\n"},"samples":[{"input":"1 2 3\n","output":"3 2 1\n"}]}}],
 "assets":[{"name":"cell.png","sha256":"…","bytes":48211,"content_type":"image/png"}]}
```
- No answer keys (`is_correct`, blank answers, reference answers, hidden tests, harness) ever.
- `points`/`partial` from `ContestProblem`. `label`/`title` from the assignment's display name if set.
- `statement_md` is DKU OJ markdown (KaTeX with `~…~` inline, `$$…$$` display). Full-markup problems: `statement_md` null and `statement_html` = DKU OJ's sanitized rendering.
- Images in statements are rewritten to `fp-asset:<name>` and listed in `assets`.
- FairPlay cleans `statement_md` / `statement_html`, option `text_md`, `instructions_md` and `description_md` on arrival (security review 2026-10-24, `app/integrations/sanitize.py`): a small allow-list of formatting tags and attributes (script / style / iframe / svg ... dropped with their content); links only http(s), mailto, `fp-asset:` and `#…`; images only `fp-asset:` and `data:image/png|jpeg|gif|webp` — any other image (e.g. an external `https://` picture) becomes the text `[external image removed]`. Code (fenced blocks, `inline code`) is kept exactly. The web cleans again (DOMPurify).
- Body ≤ 5 MB (counted after decompression; v2 §1).

**`GET exams/<key>/assets/<name>`** — the file (only names listed in the current package; from 24 h before start). `ETag: "<sha256>"`. ≤ 10 MB each, ≤ 50 MB per exam.

**`POST exams/<key>/uploads?username=<netid>&question_id=<id>&filename=<name>`** — raw body = one file; headers `X-Sha256`, `Content-Length` (v2: the student, question and file name are query parameters, which the signature covers; the old `X-Username` / `X-Question-Id` / `X-Filename` headers are not read). Checks size/extension against the problem. → `{"upload_id":"up_…"}`. Unclaimed uploads deleted after 15 days (longer than the 14 days FairPlay retries a hand-in).

**`POST exams/<key>/submissions`** — one student's final answers; ≤ 2 MB.
```json
{"username":"ax19","attempt_id":"fp_att_8f2c…","package_version":"7f3a9c1e",
 "started_at":"…","deadline_at":"…","handed_in_at":"…","handed_in_evidence":"server",
 "answers":[{"question_id":"p000123","choice":{"selected":[502]}},
            {"question_id":"p000125","fill_blank":{"answers":["hash table","1"]}},
            {"question_id":"p000126","short_answer":{"text":"…"}},
            {"question_id":"p000127","file_upload":{"upload_ids":["up_3c9e"]}},
            {"question_id":"p000124","code":{"language":"PY3","source":"…"}}],
 "integrity_report_url":"https://…/teacher/exams/<id>/submissions/<aid>"}
```
→ `200 {"receipt":"oj_rcpt_…","submissions":[{"question_id":"p000123","submission_id":88213},…]}`.
- Idempotent on `attempt_id` (unique DB constraint). Same body again → same receipt. Different body, same id → `409`.
- One transaction: participation (created if missing) + one submission per answer via `judge.utils.submission_create.create_submission`; judging enqueued on commit. Any failure → nothing kept, `422 {"question_id":…,"detail":…}`.
- `handed_in_evidence`: `"server"` (FairPlay server received it) or `"sealed"` (sealed offline by the app with the server-issued key). Late = `handed_in_at > deadline_at + 120 s`: stored and marked late for the teacher.
- `403` student not on roster / bridge off, `404` unknown assignment.

**`GET exams/<key>/results?changed_since=<iso>&page=<n>`** — only released results (`student_display_points`, `results_visible_to`), including later regrades; a withdrawn release reappears with `"released": false`.
```json
{"results":[{"username":"ax19","released":true,"total":82,"total_points":100,"updated_at":"…",
  "questions":[{"question_id":"p000123","points":10,"feedback_md":""}]}],"next_page":null}
```
- `feedback_md` is cleaned on arrival like the statements (above). Each page ≤ 2 MB.
- After hand-in, a student downloads their own uploaded answer files (`GET /api/student/attempts/<id>/files/<fid>`) only when the review level shows answers (`answers`, `answers_and_results`; v2 §8), like the statement pictures; otherwise 404.

## 5. FairPlay server (FastAPI)

- New columns on `Exam` (added like the others, `server_default`): `source` (`"fairplay"` | `"dkuoj"`), `oj_key`, `oj_package_version`, `oj_frozen_version` (set at the first student start; later packages never change a running exam), `oj_synced_at`, `oj_removed`.
- `app/dkuoj/` package: `client.py` (httpx, Bearer token, timeouts, retries on 429/5xx, test transport hook like `app.lms.base`), `adapter.py` (OJ package ⇄ FairPlay question model, OJ answers out), `sync.py` (poll `exams` every 120 s; schedule the package fetch at start−24 h; download assets, check sha256, store; create/update the FairPlay exam, roster and editor accounts), `delivery.py` (outbox), `results.py` (poll released results after the end), `routes.py` (`/oj/live` signed login, admin status page JSON).
- Background loop started in `main.py` lifespan when the bridge is configured, with a DB lock so only one worker runs it.
- **Exam created from OJ:** `source="dkuoj"`, `delivery="computer"`, `mode="questions"`, title = name, `scheduled_at` = start_time, `duration_min` = time limit (or the window), settings mapped to `calculator`, `open_book`, `websites`, `require_strong_lock`, `resume_needs_approval`. A FairPlay class per OJ class (`SchoolClass`, linked by OJ class id) with the roster as members. Read-only in the FairPlay exam editor (questions cannot be edited in FairPlay; a banner says "Edit this exam in DKU OJ").
- **Accounts:** students and editors are matched by username = OJ username (NetID). Missing ones are created with an unusable password (like `routes_lms.IMPORTED_PASSWORD`) — how students first sign in is a later feature (temporary class login); for the pilot the FairPlay admin can set passwords. Editors get role teacher.
- **Questions (FairPlay internal form for OJ exams)** — stored in `Exam.questions` with `"oj": {...}` holding OJ ids; no answer keys exist:
  - `mcq` + `multiple: bool`, `true_false: bool`, `options` (markdown strings), `oj.option_ids` (same order). Answer: int index (single) or sorted list of indexes (multiple).
  - `short` with no `accepted_answers` → graded in OJ. Answer: string.
  - `fill_blank` (new type): `blanks: int`, `blank_points: [..]`. Answer: list of strings (one per blank).
  - `file_upload` (new type): `allowed_extensions`, `max_file_size`. Answer: list of FairPlay upload ids (`/api/uploads/...`, existing upload storage; at hand-in delivery re-uploads each to OJ `uploads`).
  - `code`: `languages: [{key, name, runner}]` where runner = `python` | `javascript` | `c` | `cpp` | `java` | null (no in-app runner: "runs after hand-in"), OJ key mapping `PY3→python`, `PYPY3→python`, `JS`/`NODE`/`V8JS→javascript`, `C`/`C11→c`, `CPP*→cpp`, `JAVA*→java`; `templates: {ojkey: code}`, `tests` = samples as visible tests, `time_limit_ms`. Answer: `{"language": "<ojkey>", "source": "…"}`.
  - Every question: `prompt` = statement markdown, `prompt_format: "oj_md"` (or `"oj_html"` with sanitized HTML), `title`, `label`, `points`, `partial`; images referenced as `fp-asset:<name>` and served by FairPlay at `/api/student/attempts/{aid}/assets/<name>` (attempt owner only, exam live) and `/api/exams/{id}/assets/<name>` (teacher).
  - Local grading: none for OJ exams. Results come only from OJ results sync.
- **Package encryption / start code / live room / offline sealed submit:** unchanged — an OJ exam is a normal FairPlay exam once created.
- **Delivery outbox:** table `oj_deliveries` (`attempt_id` unique, `exam_id`, `status` pending|sent|failed|dead, `tries`, `next_at`, `last_error`, `receipt`, `created_at`, `sent_at`). Row written in the same transaction as the final hand-in (and sealed submit). Worker sends with backoff 30 s → 15 min, throttled to 10 req/s; 409/422 → `dead` + admin alert; 5xx/timeout retried for 14 days. Student hand-in receipt never waits for OJ.
- **Results:** stored per attempt (`oj_result` JSON: total, total_points, per question points + feedback, released flag); the student Grades page shows them when released.
- **Teacher in FairPlay:** OJ editors see the OJ exam in their exam list and the live room (open with start code, approvals, events, integrity report) exactly like FairPlay exams; results/grading pages say "Graded in DKU OJ" with a link.
- Config: `DKUOJ_URL`, `DKUOJ_TOKEN`, `DKUOJ_SYNC_SECONDS=120`, `DKUOJ_DELIVERY_RPS=10`.

## 6. FairPlay web (React)

- Render `prompt_format: "oj_md"` with Markdown + KaTeX (self-hosted, no CDN), DKU OJ delimiters `~…~` inline and `$$…$$` display, fenced code highlighting, tables; `fp-asset:` images via the attempt asset URL. `oj_html` rendered as-is (already sanitized by DKU OJ) inside the normal prompt container.
- Show points on every question ("Q3 · 15 points", "partial credit" when `partial`) and the total.
- `mcq` multiple → checkboxes; `true_false` → two big options; `fill_blank` → the statement with inline inputs at `___`/blank markers, or numbered inputs under it if the markers can't be placed; `file_upload` → file picker with extension/size checks, uses the existing uploads API; `code` with several `languages` → language picker (templates per language), Run on samples and custom input for languages with a runner, and "This language runs after you hand in" for those without.
- Teacher web: OJ exams read-only in the editor with the "Edit in DKU OJ" banner; results page links to DKU OJ.
- Everything works offline exactly like existing exam types (answers through the existing answer queue).

## 7. Tests (each part)

- DKU OJ: endpoint tests (auth, 404/425/304, no answer keys in any package, idempotency incl. concurrent duplicate, all-or-nothing, late marking, roster check), hiding rule per route and role, editor settings save/validation, live-room link signature.
- FairPlay server: adapter round-trip for every question type, sync against a fake OJ (httpx MockTransport), frozen version at start, outbox survives restart and retries, results sync, `/integrations/dkuoj/live` login checks.
- FairPlay web: rendering (markdown, KaTeX, assets), every new answer type saves and restores, language picker, no runner message.
