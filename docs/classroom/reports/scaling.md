# Speed and scale: hundreds of teachers at once (2026-10-20)

Goal: the classroom site stays fast and error-free with 500+ professors online at once, creating
and editing questions and assignments, grading and browsing, on top of the student load measured
in `load-test.md`.

This report covers the whole-site part (server model, static files, per-request overhead, demo
hosting, a teacher load test). Two companion reports cover the details:

- `teacher-speed.md`: every teacher page measured on a university-sized copy of the data
  (500 instructors, 6,000 questions, 150,000 submissions) and the fixes.
- `teacher-concurrency.md`: what happens when many teachers save at the same moment
  (unique codes, two people editing the same assignment, half-finished saves) and the fixes.

## Why the demo felt slow

Measured from a browser in China against the Render demo:

| | Before | After |
|---|---|---|
| Downloaded per page (first visit) | 2.2 MB (/contests/) to 8.8 MB (question page) | 0.3 MB to 1.8 MB |
| Downloaded per page (next visits) | the same again (no browser cache) | 0 KB (all 19 files from cache) |
| /contests/ fully loaded, warm | several seconds | 0.55 s |

What was wrong:

1. **Static files were sent uncompressed and without cache headers** (`runserver --insecure`), so
   every click downloaded the CSS, the editor bundle and the libraries again.
2. **MathJax (1.1 MB) loaded on every page**, even pages without a formula. Question text renders
   math with KaTeX; MathJax is only needed for older Markdown pages.
3. **Render's free plan gives the demo 0.1 CPU** and sleeps after 15 minutes. One request from
   China to the Singapore server takes about 0.5-0.7 s of network time alone. This is the demo's
   hosting, not the app: on the same code a page takes 50-180 ms of server time locally, and a
   DKU-hosted server is a few milliseconds away from campus.

Fixes:
- The demo now runs gunicorn (threads) instead of the development server, serves static files
  with WhiteNoise (gzip, browser cache), gzips HTML and keeps database connections open.
- MathJax is loaded only when the page, or content added to it later, actually contains math.
  A small stand-in keeps every existing `MathJax.typesetPromise(...)` call working.

## Per-request overhead (every page, every user)

| Item | Before | After |
|---|---|---|
| `UPDATE judge_profile SET last_access…` | on every signed-in request (page, autosave, poll) | at most once a minute per user, or when the IP changes |
| Navigation bar | 3 queries per page | 0 (cached, dropped on any change) |

The last-access write was the largest source of database writes under load: with 500 teachers
and thousands of students, every autosave and status poll rewrote a profile row.

## Bug found on the way

The Statistics chart on /submissions/ counted every submission on the site (cached for everyone)
while the list only showed what the viewer may see: "No submissions" next to "Total: 10", and a
visitor could count private course answers. It now counts exactly the list's submissions (one
cache entry for visitors, a 5-minute entry per signed-in user).

## Teacher pages at university scale (details in `teacher-speed.md`)

Seeded: 500 instructors, 150 courses, 6,000 questions, 1,500 assignments, 3,000 students,
150,000 submissions. Every instructor page took 2.3–5.8 s, almost all of it one 2.4 s query that
builds the navigation menu (`user_course_subject_keys`), run on every page for every course
member. Rewritten as indexed subqueries:

| Page (instructor) | Before | After |
|---|---|---|
| Home | 5.8 s | 93 ms |
| /contests/ | 3.8 s | 116 ms |
| /problems/ | ~3 s, 39 queries | ~120 ms, 20 queries |
| Practice grading | 3.1 s | 81 ms |
| Submission lists | 120 queries | 26 queries |
| Leaderboard | 211 queries | 11 queries |
| Assignment editor (admin) | 11.8 MB | 208 KB |
| /contests/ (admin) | 4.7 MB | 680 KB |

## Many teachers saving at once (details in `teacher-concurrency.md`)

With 8 teachers creating a question at the same instant, 6 got HTTP 500 (two got the same
generated code). Now: generated codes and keys retry on a collision, two saves of the same
question run one after another, deadlock victims are retried, and a save from a page that is out
of date gets a 409 "someone else saved this, reload" instead of overwriting the other person's
work (checked in a browser with two tabs). 30 teachers creating and editing for 90 s: 1,801
requests, 0 errors (before: 83 of 113 creates failed).

## Teacher load test

`scripts/loadtest/seed_teacher_load.py --teachers 500` (each teacher: own course, class of 10,
4 questions, a homework with every student's answers) and `scripts/loadtest/locust_teachers.py`:
every simulated teacher logs in, then loops over browsing (assignments, question bank search,
course, assignment, submissions), grading (grade page + save a grade), analytics, and authoring
(create a question, edit it twice, create an assignment with it, edit the assignment), with
10–30 s of thinking between steps. Server: gunicorn gthread 4 workers x 4 threads,
`CONN_MAX_AGE=60`, DEBUG off, MariaDB and Redis on the same 2-CPU box as the load generator.

| Run | Teachers | Requests | Failures | req/s | p50 | p95 | p99 |
|---|---|---|---|---|---|---|---|
| A | 500 | 10,439 | **0** | 24.9 | 11 s | 24 s | 25 s |
| B | 200 | 9,260 | **0** | 25.7 | 0.43 s | 2.2 s | 3.9 s |

No request failed in either run, including 1,247 question creates, 2,318 question saves,
366 assignment creates and 349 assignment edits.

What limits it is CPU in the app server: both runs pinned the box at ~25 requests per second with
gunicorn using ~150% (of 2 cores), MariaDB ~33%, Redis 2%. A request costs about 70 ms of app CPU
on average (single requests take 30–90 ms; about a third is database time, a third template
rendering). 200 thinking teachers already ask for ~25 req/s, so 500 at once ask for ~60 req/s,
more than two cores can serve; requests then wait in line (run A), but none fails.

## What production needs (500 teachers + a 300-student exam at the same time)

Rule of thumb from these runs: **one CPU core serves about 12 requests per second**. 500 active
teachers ≈ 60 req/s; a 300-student exam ≈ 45 req/s (`load-test.md`). With 2x headroom:

- **App server: 8 cores, 16 GB**, `GUNICORN_WORKERS=16 GUNICORN_THREADS=4` (gthread is now the
  default in `docker/entrypoint.sh`), `GUNICORN_TIMEOUT=120`. Or two 4-core app servers behind
  nginx; nothing in the app keeps state in a worker.
- **Django settings:** `DATABASES['default']['CONN_MAX_AGE'] = 60` and `CONN_HEALTH_CHECKS = True`;
  Redis for cache and sessions (already `cached_db`).
- **MariaDB: its own 4 cores, 8 GB**, `innodb_buffer_pool_size` 4–6 GB (the default is 128 MB),
  `max_connections` ≥ workers x threads x app servers + Celery + 50 (e.g. 300).
- **nginx:** `gzip on` for text/css/js/json, `expires 7d` on `/static/` (style.css carries `?v=`),
  HTTP/2, `client_max_body_size` for uploads, `proxy_read_timeout 120s`.
- **Celery workers** on their own cores for finalize, exports, AI grading and MOSS.
- **Logins** cost ~0.4 s of CPU each (PBKDF2 password hashing, Django's default strength). If
  hundreds of people sign in in the same minute (exam start), that is the spike to plan for;
  NetID single sign-on avoids it.
- Re-run both load tests on the real hardware before the first big exam:
  `scripts/loadtest/run.sh` (students) and `scripts/loadtest/run_teachers.sh` (teachers).

## Also fixed while testing

- A teacher saving their own assignment without themselves in Authors (or an empty list) locked
  themselves out with a 403. They now stay an author; site editors can still change the list.
- The Pwned Passwords check on login waited 2 s per login when the API is blocked (it is in this
  sandbox and may be from mainland China). After a failure it is skipped for 10 minutes.
- MathJax could be loaded twice on a page where the Markdown preview loaded it first; the loader
  now steps aside.
- `local_settings.example.py`: a Redis URL with a database index works; `CLASSROOM_DB_CONN_MAX_AGE`.

## Tests

Full suite after merging everything: **1,992 tests OK**, flake8 clean, no missing migrations. New modules:
`test_submission_stats_visibility`, `test_user_access_log`, `test_navigation_cache`,
`test_contest_editor_self_author`, `test_pwned_backoff`, `test_fairplay_brand` (MathJax),
`test_teacher_speed` (30), `test_teacher_concurrency` (37).

## Left

- Run the load tests again on production hardware; numbers from a shared 2-CPU box only show
  where the limit is, not the real latencies.
- The admin /contests/ page is still 680 KB (about 4 KB of markup per row).
- The Django admin, the old MCQ and fill-blank pages and course/class edit do not have the
  stale-page (409) check yet; their saves are serialized but a later save still wins.
- The Render demo stays on 0.1 CPU and sleeps after 15 minutes (free plan).
