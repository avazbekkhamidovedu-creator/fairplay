# FairPlay bridge

FairPlay is a locked exam app. With the bridge on, a teacher can pick **FairPlay** as the lock of an
assignment: students then take it only in the FairPlay app, and it stays hidden from them on DKU OJ.
FairPlay's server downloads the exam the day before it starts, runs it from its own copy, sends every
student's answers back after hand-in, and reads the released results. Teachers build and grade the
assignment in DKU OJ exactly as before, and run the exam from DKU OJ.

**No database changes.** The bridge adds no model, field, table or migration to DKU OJ. It reads DKU OJ's
own data and only makes the writes DKU OJ already makes: participations and submissions (the answers),
attaching an existing kind of row (the contest tag `fairplay`), and files in the submission file store.
Everything FairPlay-specific (the exam's settings, class logins, student accounts, integrity events) is
kept by FairPlay and reached through the signed API below. Small working records (hand-in receipts,
change tracking) live in DKU OJ's shared cache (Redis).

## Turning it on (site administrator)

Set two values in `dmoj/local_settings.py` and restart the site (`dmoj/settings.py` declares all four
FairPlay settings with their defaults: `FAIRPLAY_TOKEN = ''`, `FAIRPLAY_URL = ''`,
`FAIRPLAY_PUBLIC_URL = ''`, `FAIRPLAY_ALLOW_INSECURE = False`):

```python
FAIRPLAY_TOKEN = '<a random secret of at least 32 characters>'   # e.g. python -c "import secrets; print(secrets.token_urlsafe(48))"
FAIRPLAY_URL = 'https://exam.example.edu'                         # the FairPlay server
```

Give the FairPlay server the same token (`DKUOJ_TOKEN`) and this site's URL (`DKUOJ_URL`). That is the
only credential. To rotate it, set two tokens (`FAIRPLAY_TOKEN = ['<new>', '<old>']` or
`'<new>,<old>'`, the same on FairPlay): calls are signed with the first and accepted with either.

The bridge is on when `FAIRPLAY_TOKEN` holds a token of at least 32 characters. With the bridge on,
`manage.py check` (and runserver) fails unless the default cache is shared by all workers (`judge.E101`:
Redis or Memcached, not the local-memory, dummy or file cache: the replay check of signed calls must see
every worker, and the hand-in records live there), `FAIRPLAY_URL` is set (`judge.E103`) and it is
`https://` (`judge.E102`; plain `http://` only to the loopback, with `DEBUG`, or with
`FAIRPLAY_ALLOW_INSECURE = True` for a local test kit). If FairPlay shows report links under another address than `FAIRPLAY_URL`, set
`FAIRPLAY_PUBLIC_URL` to it. Leave `FAIRPLAY_TOKEN` empty (the default) and the bridge is off: the API
answers 404 and teachers are not offered the FairPlay lock. No
migration is needed either way.

Turning the token off does not reveal FairPlay exams in their hiding window; switch those off in the
editor before the editor lock (the option stays visible on them), or let them be released.

## What teachers see

* In the assignment editor, under **Anti-cheating**, the lock is one choice: **No lock**, **Safe Exam
  Browser** or **FairPlay** (same permission as the SEB option). FairPlay marks the assignment with the
  contest tag `fairplay` (made on first use; the tag picker does not offer it, and "Add new" refuses the
  name). That radio is the only FairPlay control in the editor: it shows no explanation, status or
  warning, and DKU OJ does not read or send the exam's settings from there (FairPlay keeps its own;
  a settings payload sent to the editor's save endpoint is still accepted and forwarded). When a save is
  refused because FairPlay holds the exam, the editor shows one short line at the lock choice ("The
  lock can't be changed now."), only after Save.
* A FairPlay assignment must be limited to a course, classes or selected students: that audience is the
  exam roster. It leaves out the same people as a class roster: admins and instructors of its courses,
  instructors of classes in them, authors, curators and testers of any FairPlay exam, site staff, and
  the assignment's banned users. Safe Exam Browser and FairPlay can't both be on (the admin checks the
  same when the tag is attached there). A hidden (not visible) assignment is a draft: FairPlay is not
  given it until it is visible.
* **Hiding window.** From **24 hours before the start** (when FairPlay may download the package) until
  the exam is **released** (its results are finalized and it has closed for everyone, or **14 days
  after the end**, when hand-ins can no longer arrive, whichever comes first; "closed for everyone" is
  the end time plus the minutes added with Add time, or, if DKU OJ lost that count, the latest close
  time FairPlay reported with a hand-in, else 8 hours after the end), students and anyone who is not its author,
  curator, tester, course admin or instructor, or site staff cannot open it or any of its problems on
  DKU OJ: not the pages, PDFs and Word exports, submission lists, the `/api/v2` API, user pages, test data
  or tester downloads, attachments (also those referenced from file-upload instructions, translations,
  options, editorials and clarifications), or practice submits. A problem the exam shares with another
  assignment, or a public one, is hidden there too during the window. Outside the window a FairPlay assignment and its problems behave like
  any other. Points are not affected: a user's points still count every public problem they solved.
* **Editor lock.** From the start of the hiding window until 14 days after the end, the editor refuses
  to change the assignment ID, to switch the lock away from FairPlay, and to add, remove or replace
  problems (a one-line form error, e.g. "The lock can't be changed now."). Everything else (times, points, order, texts) can still change.
* DKU OJ has no FAIRPLAY menu item, no FairPlay course pages and no FairPlay tab, box or report page:
  the exam is run from the FairPlay side. The only FairPlay controls in DKU OJ are the lock choice in
  the editor and the access codes button below.
* On the assignment's **Participation** page, editors of a FairPlay assignment (bridge on) get one
  button, **Download access codes (PDF)**, which saves `access-codes-<assignment ID>.pdf` (the request
  is below). If FairPlay does not have the codes yet (404 or 409), or can't be reached, the page shows
  one short line ("The access codes are not ready yet." / "The access codes could not be downloaded.").
* Answers arrive as normal submissions of each student (file answers as normal file submissions) and are
  graded as usual. Results go back to FairPlay only as students would see them here (released grades,
  finalized exam results).

## How the two servers talk

Only the two servers talk to each other; browsers and the apps never call across.

**Signing (both directions).** Each direction has its own key derived from the shared token, so a
request captured one way can't be replayed the other way:

```
key_oj_to_fp = HMAC-SHA256(token, "fairplay-bridge v2 oj->fp")   # DKU OJ signs its calls to FairPlay
key_fp_to_oj = HMAC-SHA256(token, "fairplay-bridge v2 fp->oj")   # DKU OJ checks FairPlay's calls
```

Every request carries `X-FP-Timestamp` (unix seconds), `X-FP-Nonce` (32 hex), optionally
`X-FP-Acting-User` (the DKU OJ username of the professor who clicked, DKU OJ to FairPlay only) and
`X-FP-Signature` = hex HMAC-SHA256 of

```
v2 \n METHOD \n path?query \n timestamp \n nonce \n acting user \n sha256(body)
```

"v2" in the key labels and in the canonical string is the version of this signature format.

The receiver answers 404 while the bridge is off. Before reading the body it answers 401 for missing or
malformed headers or a timestamp more than 60 s off, and 413 for a declared `Content-Length` over the
endpoint's limit; then it reads at most the limit and answers 401 for a wrong signature (compared in
constant time) or a nonce seen in the last 180 s (cache key `fpb:nonce:<nonce>`). Answers are
not signed, so they travel over https. DKU OJ reads at most 2 MB of a FairPlay answer and waits at most
connect + read timeout for all of it (5 s + 15 s for actions, 3 s for the editor).

**FairPlay to DKU OJ** (`/api/fairplay/v1/`, UTF-8 JSON, ISO-8601 UTC times ending in `Z`):

| Method and path | Purpose |
| --- | --- |
| `GET exams?changed_since=&page=` | FairPlay exams (tagged `fairplay`, visible, ended at most 30 days ago), 100 per page, with editors, roster and `package_version`; no settings. With `changed_since`, only those changed since. The first page also has `active_keys` (all of them) and `active_since`: an exam no longer listed there was switched off. |
| `GET exams/<key>/package` | The exam package: questions of every type without any answer key, statements as DKU OJ markdown (or sanitized HTML for full-markup problems), images as `fp-asset:<name>` (editor attachments and images uploaded with the problem editor, `MEDIA_URL` + `MARTOR_UPLOAD_MEDIA_DIR`; no other file under `MEDIA_ROOT`). Code questions offer the allowed languages, only those with a harness when the problem has harnesses. `425` until 24 hours before the start; `ETag` / `If-None-Match`. |
| `GET exams/<key>/assets/<name>` | One file listed in the package. |
| `POST exams/<key>/uploads?username=&question_id=&filename=` | A student's file answer (raw body; student, question and file name in the signed query string, `X-Sha256` header). Returns an `upload_id` that only works for that student and question; unused uploads are deleted after 15 days (longer than FairPlay retries a hand-in). |
| `POST exams/<key>/submissions` | One student's final answers. Answer: `{"receipt", "submissions": [{question_id, submission_id}], "warnings": [...]}`. Idempotent on `attempt_id` (same body, same receipt; different body, `409`), all-or-nothing (`422` names the question), `403` for students not on the roster. An answer to a question no longer in the exam, or option ids that no longer exist, is skipped and listed in `warnings` (kept with the hand-in and shown to the professor); the rest is filed with `200`. A failure of the database or the file store is `503` with `Retry-After` (FairPlay retries); only a refused request is `4xx`. One attempt per student: another attempt id while one is on file → `409` (a professor can clear it). `410` after the exam's close + 14 days. Code answers only in the problem's allowed languages, and only those with a harness when it has harnesses. Late = handed in more than 2 minutes after the close (end time + minutes added in FairPlay), where the hand-in time is FairPlay's `handed_in_at` kept between the exam's start and the time DKU OJ received it. `integrity_report_url` kept only under `FAIRPLAY_URL` (or `FAIRPLAY_PUBLIC_URL`). |
| `GET exams/<key>/results?changed_since=&page=` | Each handed-in student's result as they may see it: released points and feedback, or `"released": false`; later regrades and withdrawn releases too. |
| `GET classes/<id>/roster` | A class's students (members minus its teachers, course admins and instructors, authors, curators and testers of any FairPlay exam and staff), read when a student sets up FairPlay. Only for a class a FairPlay exam uses or that got a class login from DKU OJ; else 404. |
| `GET students/<netid>/classes` | The NetID's active classes as a student, among the same classes. |

**DKU OJ to FairPlay** (`/api/integrations/dkuoj/v1/`, the professor in `X-FP-Acting-User`; DKU OJ checks
the permission first and FairPlay checks again that the professor edits the exam). `FairPlayClient` can
make all of the calls below, but since the FairPlay pages were removed only the access codes call and the
settings forwarded on an editor save are made from DKU OJ's web interface: class logins
(`POST/DELETE classes/<id>/login`, `GET classes/<id>/status`), student reset
(`POST students/<netid>/reset`), the exam (`GET exams/<key>`: state, codes, counts, students and its
`settings`) and its actions (`POST exams/<key>/open|close|add-time|new-code|approve`, a student's report
`GET exams/<key>/students/<netid>/report`), the access codes (`GET exams/<key>/access-codes.pdf`, below) and its settings (`POST exams/<key>/settings`: any of
`calculator`, `open_book`, `websites`, `strong_lock`, `resume_needs_approval`, `upload_opens_days`,
`review`; kept by FairPlay also for an assignment it has not fetched yet; after the first student started
only `review` may change).

## What DKU OJ keeps where (no schema change)

| What | Where |
| --- | --- |
| Which assignments are FairPlay exams | the contest tag `fairplay` (`ContestTag`, `Contest.tags`) |
| The exam's settings, review level | FairPlay (read and saved through the API) |
| Answers | participations and submissions, as from the website; file answers as file submissions |
| A file answer before its hand-in | the submission file store, `fairplay_uploads/<contest>/<student>/<problem>/<token><ext>`, deleted after the hand-in or after 15 days |
| Hand-in receipts (attempt id, receipt, times, late mark, report link, answers not filed) | shared cache, 60 days (`fpb:attempt:*`, `fpb:handin:*`) |
| Minutes added with Add time | shared cache, until 60 days after the end, at most 180 days (`fpb:added-minutes:<contest>`) |
| Latest close time FairPlay reported with a hand-in | shared cache, same lifetime (`fpb:fairplay-closes:<contest>`) |
| Whether any assignment is a FairPlay exam (lets the hiding rule skip its queries on a site without one) | shared cache, refreshed when the tag is attached or removed (`fpb:any-exam`) |
| Hashes of package files | shared cache, 7 days (`fpb:asset:*`) |
| Change tracking of the exam list and of results | shared cache (`fpb:exam-track:*`, `fpb:results:*`) |
| Classes given a class login from DKU OJ | shared cache, 180 days (`fpb:class-login:<class>`), renewed whenever a manager of the class sees an active login |
| Settings FairPlay did not take | the professor's session, until saved |

**If the cache loses these records** (for example Redis restarted without persistence):

* Change tracking: everything is listed to FairPlay again once; FairPlay applies it idempotently.
* Hand-in receipts: exactly-once is kept by the database. Before filing, and again inside the
  transaction after locking the student's profile row (so a delivery whose cache lock expired while it
  waited is still caught), DKU OJ checks whether the student already has submissions in the exam (newer
  than a professor's clear). If so, the hand-in is
  refused with 409 instead of being filed twice, FairPlay shows it to the teacher as not delivered. A replay of the same
  attempt whose response was lost and whose record the cache lost is refused the same way (its answers
  are already filed).
* Added minutes: lateness and the 14-day window use FairPlay's close time from the hand-in instead, but
  never more than 8 hours past the end time.
* Class logins: a class that has no FairPlay exam is served to FairPlay only while a class-login mark
  (180 days) from before the course pages were removed is still in the cache; DKU OJ no longer makes new marks.
* File names of uploads not yet handed in: the file is still filed, named `answer<ext>`.

## Behaviour notes

* A clone of a FairPlay assignment is an ordinary assignment (the `fairplay` tag is not copied); turn
  FairPlay on in its editor.
* Renaming an assignment's key in the editor (before the editor lock) takes its FairPlay settings along
  (DKU OJ reads them under the old key and saves them under the new one; values it does not know become
  FairPlay's defaults).
* `GET exams/<key>/access-codes.pdf` is signed like the other calls (`X-FP-Acting-User` is the professor who
  clicked; DKU OJ checks that they edit the assignment and that it is a FairPlay assignment first), sends
  `Accept: application/pdf` and answers `200 application/pdf`. `404` or `409` with `{"detail": ...}` means
  the exam is not ready; DKU OJ then shows its own short line, never FairPlay's text. DKU OJ streams the
  bytes to the browser as `access-codes-<key>.pdf` (attachment, `Cache-Control: no-store`), at most 2 MB.
* The editor waits at most 3 s for FairPlay when it saves settings it was given; if FairPlay does not take
  them, only the `judge.fairplay` log says so.
* Everything the bridge logs goes to the `judge.fairplay` logger.
* Changing an assignment while FairPlay runs it does not change the running exam; FairPlay picks up
  edits until the first student starts.

## Code

`judge/utils/fairplay.py` (switch, the tag, hiding window and rule, checks),
`judge/utils/fairplay_signals.py` (keeps the "any FairPlay exam" flag current), `judge/utils/fairplay_signing.py`,
`judge/utils/fairplay_client.py` (calls to FairPlay), `judge/utils/fairplay_package.py` (exam list and
package), `judge/utils/fairplay_delivery.py` (uploads, hand-ins, results), `judge/utils/fairplay_editor.py`
(editor lock and the settings forwarded on save), `judge/utils/fairplay_teaching.py` (class
rosters), `judge/views/api/fairplay.py` (the API), `judge/views/fairplay.py` (the access codes download), `judge/views/fairplay_editor.py` (forwards settings after an editor save) and
`judge/checks.py`; the rest of the code base only calls into these modules (`fairplay_hidden_for`,
`fairplay_hidden_q`, `fairplay_visible_problems`, `fairplay_public_problems`, ...) at the places that serve
an assignment or a problem. Tests: `judge/views/tests/test_fairplay_*.py`,
`judge/utils/tests/test_fairplay.py`.
