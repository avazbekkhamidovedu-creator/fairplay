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

<!-- SECTIONS BELOW ARE FILLED IN AS RESULTS COME IN -->

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
