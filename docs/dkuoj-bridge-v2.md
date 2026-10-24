# DKU OJ ↔ FairPlay bridge, v2: professors use only DKU OJ

Status: build contract (2026-10-24; review fixes 2026-10-24; class-login password viewable, login
history, strong student passwords, student classes, student review after hand-in §8 2026-10-24). Extends `docs/dkuoj-bridge.md` (v1: exam list, package,
assets, uploads, submissions, results). Everything in v1 stays, except where this file changes it
(§1 signing replaces the Bearer token; §6 retires the live-room link).

Product rules (from Mister):
- Professors never sign in to FairPlay for DKU OJ classes. Everything is in DKU OJ: a top-menu
  item **FAIRPLAY** (teachers/admins only) and a **FairPlay** tab on FairPlay assignments.
- The FAIRPLAY pages list ONLY assignments whose lock is FairPlay (`fairplay_required = True`).
- Students get ONE temporary class login per class from the professor; at first sign-in in the
  FairPlay app they type first name, last name, NetID, then choose their own username and
  password. The professor can reset a student and create a new class login at any time.

## 1. Signed server-to-server calls (both directions)

Only the two servers talk to each other. Browsers and apps never call across.

Shared secret: DKU OJ `FAIRPLAY_TOKEN` = FairPlay `DKUOJ_TOKEN` (≥ 32 chars, unchanged). Each
direction uses its own key so a captured request can't be sent back the other way:

```
key_oj_to_fp = HMAC-SHA256(token, "fairplay-bridge v2 oj->fp")      # DKU OJ signs, FairPlay checks
key_fp_to_oj = HMAC-SHA256(token, "fairplay-bridge v2 fp->oj")      # FairPlay signs, DKU OJ checks
```

Every request carries:

| Header | Value |
|---|---|
| `X-FP-Timestamp` | unix seconds (1–12 ASCII digits `[0-9]`; anything else → 401) |
| `X-FP-Nonce` | 32 hex chars, random per request |
| `X-FP-Acting-User` | (DKU OJ → FairPlay only, optional) DKU OJ username of the professor who clicked |
| `X-FP-Signature` | hex HMAC-SHA256(direction key, canonical string) |

Canonical string (UTF-8, `\n` between lines, no trailing newline):

```
v2
<METHOD uppercase>
<path exactly as sent, including the query string, e.g. /api/fairplay/v1/exams?page=2>
<X-FP-Timestamp>
<X-FP-Nonce>
<X-FP-Acting-User or empty>
<lowercase hex sha256 of the raw request body (empty body: sha256 of "")>
```

The receiver:
1. bridge off (no token) → 404; missing/invalid headers → 401 `{"detail":"bad signature"}`;
2. `|now - timestamp| > 60` → 401;
3. compares the signature with `hmac.compare_digest`;
4. rejects a nonce seen in the last 180 s (cache/Redis key `fpb:nonce:<nonce>`, `SET NX EX 180`;
   longer than the 120 s a timestamp can be accepted in, with margin) → 401;
5. bodies over the endpoint's limit → 413 before hashing.

Responses are not signed (TLS protects them in production). Clients use timeouts (connect 5 s,
read 15 s; DKU OJ page loads and the live table's poll: 3 s), never retry a POST that may have
succeeded unless it is idempotent (marked below).

Class login passwords (product decision 2026-10-24, from the professors: "I can't find the
temporary password"): FairPlay keeps each class login's password encrypted (§3) and sends it to
DKU OJ in `GET /classes/<id>/status`; DKU OJ shows it, masked, to the class's managers. So the
class login password is RECOVERABLE by anyone who can manage the class in DKU OJ (course admins,
the class's instructors, site staff) and by anyone holding FairPlay's server secret plus its
database. This is deliberate: a class login only starts the set-up (§4: name + NetID checked
against the live DKU OJ roster, then the student's own password), it expires in 14 days, and a
new one can be made at any time. Student passwords are never recoverable (hash only).

Everything a receiver acts on is in the signed parts: the path with its query string, the body,
the acting user. Changed from v1: the upload's student, question and file name are query
parameters (`POST exams/<key>/uploads?username=…&question_id=…&filename=…`), no longer the
unsigned `X-Username` / `X-Question-Id` / `X-Filename` headers (DKU OJ does not read those any
more). `X-Sha256` stays a header: DKU OJ checks it against the signed body.

v1's `Authorization: Bearer` is REMOVED on both sides (both servers ship together).
Test vectors: `docs/dkuoj-bridge-v2-vectors.json` (fixed; both sides' tests must reproduce every
`canonical` and `signature` in it).

## 2. New DKU OJ endpoint (FairPlay → DKU OJ, signed fp->oj)

**`GET /api/fairplay/v1/classes/<class_id>/roster`** — the class's current members, fetched by
FairPlay when a student sets up (so the list is never stale).

```json
{"class": {"id": 31, "name": "CS 101 Section A"},
 "course": {"id": 12, "name": "CS 101 Programming"},
 "students": [{"username": "bl642", "first_name": "Bob", "last_name": "Li"}]}
```
`students` = the class's members minus its course's admins and instructors, the class's
instructors, site staff, and anyone who is an author or curator of any `fairplay_required`
assignment (a TA or professor of another course who is a plain member here can't be claimed as a
student). 404 unknown class.

**`GET /api/fairplay/v1/students/<netid>/classes`** (2026-10-24) — the ACTIVE classes the NetID
is a student of, by the same rule as the roster above (a class where they are an admin,
instructor, curator, ... is left out), for the course cards in the FairPlay app.

```json
{"classes": [{"id": 31, "name": "CS 101 Section A",
              "course": {"id": 12, "name": "CS 101 Programming"},
              "teachers": ["Shuai Dong"]}]}
```
`teachers` = the class's instructor display names (`instructor_names`), else its instructors'
(class admins') names, else the course admins' names. Unknown NetID → `200 {"classes": []}`
(server to server, signed: no need to hide whether it exists). `<netid>` is escaped like Django's
`request.get_full_path()` (`:@&+$,-_.!~*'()` kept) so both sides sign the same path.
`Cache-Control: no-store`.

## 3. New FairPlay endpoints (DKU OJ → FairPlay, signed oj->fp)

Prefix `/api/integrations/dkuoj/v1`. JSON. `X-FP-Acting-User` = the professor (for audit and
for exam permission checks). DKU OJ has already checked the professor may manage the
class/assignment; FairPlay re-checks exam endpoints against the editors from its last sync
(`oj_editors`) → 403 if the acting user is not an editor of that exam.

### Class logins and students

- **`POST /classes/<class_id>/login`** body `{"class_name": "...", "course_name": "..."}` →
  `{"username": "cs101a-m4tz", "password": "river-cotton-pine-moss-583", "expires_at": "<iso>"}`.
  Creates a new class login, replacing (and immediately disabling) any previous one for that
  class. Username: lower-case course-ish prefix (from class_name, ascii letters/digits, ≤ 8) +
  `-` + 4 random [a-z0-9]; unique. Password: four short random words + 3 digits (≥ 20 chars, about 10^11 choices),
  from a built-in list. Stored as a password hash (sign-in) AND encrypted (2026-10-24, see §1
  "Class login passwords"): column `class_logins.password_enc` = `"v1:" + base64(nonce ‖
  AES-256-GCM ciphertext)`, key = HKDF-SHA256(FairPlay server secret `PROCTORA_SECRET`, no salt,
  info `"fairplay class-login password v1"`), 96-bit random nonce, the login's username as
  associated data. Changing the server secret makes the stored copies unreadable (status then
  says `password: null`); sign-in keeps working. Valid 14 days.
- **`DELETE /classes/<class_id>/login`** → `{"ok": true}` (idempotent).
- **`GET /classes/<class_id>/status`** → (`Cache-Control: no-store`: it carries a password)
  ```json
  {"login": {"username": "cs101a-m4tz", "password": "river-cotton-pine-moss-583" | null,
             "created_at": "...", "expires_at": "...", "used": 28, "created_by": "prof_dong" | null} | null,
   "history": [{"username": "cs101a-m4tz", "created_at": "...", "expires_at": "...",
                "ended_at": null, "end_reason": null, "used": 28, "created_by": "prof_dong"},
               {"username": "cs101a-k2p9", "created_at": "...", "expires_at": "...",
                "ended_at": "...", "end_reason": "expired|stopped|replaced|locked", "used": 3,
                "created_by": null}],
   "students": [{"netid": "bl642", "set_up": true, "username": "boblee", "set_up_at": "...",
                 "via_this_class": true}],
   "recent": [...]}
  ```
  `login` = the active login; `password` is its plain password, `null` for a login made before
  the encrypted copy existed (DKU OJ: "Create a new class login to see the password").
  `history` = the class's logins, newest first, at most 20, the active one included (with
  `ended_at`/`end_reason` null); never a password. `end_reason`: `replaced` (a new login was
  made), `stopped` (DELETE), `locked` (40 different NetIDs failed, §4), `expired` (ran out; also
  a login replaced/stopped only after it had run out: `ended_at` = its `expires_at`).
  `created_by` = the DKU OJ username that created it (X-FP-Acting-User), if any.
  `students` = every FairPlay account linked to the class: a NetID that set up through this
  class's logins, a member of its FairPlay mirror class, or on a synced exam roster of an exam
  for this class (exams not closed, or changed in the last 180 days). An exam roster can hold
  students of OTHER classes sharing the assignment, so `via_this_class` is true only for a NetID
  that set up through one of THIS class's logins. DKU OJ merges the list with its own roster;
  NetIDs FairPlay doesn't know are shown as "not set up"; an entry not on DKU OJ's roster is
  shown (as a former member) only with `via_this_class: true`.
- **`POST /students/<netid>/reset`** body `{"class_id": 31}` → `{"ok": true}` (idempotent):
  the account's own username and password are removed, all its sessions end at once
  (token version bump), `set_up` becomes false. Attempts, answers and results stay.
  DKU OJ allows it only for a NetID on the class's live roster, or one FairPlay reports with
  `via_this_class: true`, and sends the canonical NetID (the roster username, or FairPlay's).
  FairPlay checks again: a NetID not linked to `class_id` (see `students` above) → 403
  `{"detail": "This student is not linked to this class in FairPlay: ..."}`; a teacher's
  account → 400; a NetID FairPlay has no account for → `{"ok": true}`.

### Exam control (key = DKU OJ assignment key)

- **`GET /exams/<key>`** → the FairPlay tab's data:
  ```json
  {"synced": true, "package_ready": true, "questions": 3, "points": 26,
   "status": "draft|live|closed", "start_code": "219074" | null,
   "invigilator_code": "583204" | null, "closes_at": "...", "open_to": "all" | ["bl642"],
   "settings": {"calculator":"scientific","open_book":false,"websites":["docs.python.org"],
                "strong_lock":true,"resume_needs_approval":true,"review":"nothing"},
   "counts": {"roster": 68, "set_up": 61, "not_started": 3, "writing": 23, "offline": 1,
              "waiting": 1, "handed_in": 41},
   "students": [{"netid":"bl642","name":"Bob Li","set_up":true,"status":"writing|not_started|offline|waiting|handed_in",
                 "answered":2,"questions":3,"seconds_left":2460,"warnings":["Left the app once (8 s)"]}],
   "server_time": "..."}
  ```
  `synced:false` (404 is not used) when FairPlay hasn't got the assignment yet.
- **`POST /exams/<key>/open`** body `{"open_to": "all" | ["netid", ...]}` → same shape as GET.
  Idempotent (already live: updates `open_to`).
- **`POST /exams/<key>/close`** → GET shape. Same effect as the FairPlay teacher Close (writers
  handed in with what is saved, delivered to DKU OJ through the outbox).
- **`POST /exams/<key>/add-time`** body `{"minutes": 10}` (1–120) → GET shape. Adds the
  minutes to the time limit, to every attempt still writing AND to the close time: FairPlay
  keeps them (`extra_close_minutes`, kept across syncs) and closes at DKU OJ's end time + the
  added minutes, so `closes_at` in the answer moves; students starting after it get the time,
  and the automatic close waits for it. The hand-ins then reach DKU OJ after the assignment's
  end time: DKU OJ files them anyway (`POST exams/<key>/submissions` has no end-time check;
  lateness is judged from FairPlay's `deadline_at`).
- **`POST /exams/<key>/new-code`** → GET shape (new start code).
- **`POST /exams/<key>/review`** body `{"review": "nothing" | "answers" | "answers_and_results",
  "updated_at": "<iso>"}` → GET shape (2026-10-24, §8). Idempotent. Any other `review` → 400;
  `updated_at` optional, a string if present (else 400). Not synced yet → 409 (the exam list
  brings it later). Allowed at any time: draft, live or closed.
- **`POST /exams/<key>/approve`** body `{"netid": "ep530"}` → GET shape (lets a waiting
  student continue after a crash, same as the live-room approval).
- **`GET /exams/<key>/students/<netid>/report`** → `{"name","netid","status","started_at",
  "handed_in_at","events":[{"at","kind","detail"}]}` (the integrity events, oldest first, ≤ 500).

Errors: `{"detail": "..."}` with 400/403/404/409; DKU OJ shows the detail text.

## 4. Student accounts in FairPlay (class login → own login)

FairPlay users gain `netid` (unique, nullable; the DKU OJ username) and `set_up_at`. The
bridge's roster sync matches and creates accounts by `netid` (not by `username` any more); a
synced account that never set up has an unusable password and a placeholder username
`oj:<netid>` that cannot sign in. A set-up student signs in with their own username.

Older accounts without a `netid` are linked to a NetID by username only if the bridge made or
used them before: the v1 sync's accounts (username = NetID, the unusable import password) or
accounts that have DKU OJ roster / editor rows. Any other account that just has the same
username (e.g. a dev server's demo `alice`) is never adopted; the NetID gets its own
`oj:<netid>` account.

App/web flow (all on the FairPlay server; the app shows the FairPlay web pages):

1. `POST /api/auth/login` with a class login's username/password → `200
   {"setup_token": "...", "class": {"name": "CS 101 Section A", "course": "CS 101 Programming"}}`
   (no `token`; the token is a separate kind, valid 20 minutes, usable ONLY for the two setup
   calls). Expired / stopped class login → 401 "This class login has expired. Ask your professor
   for a new one."
2. `POST /api/setup/identify` (Authorization: Bearer <setup_token>) body
   `{"first_name","last_name","netid"}` → FairPlay fetches the class roster from DKU OJ (§2; if
   unreachable: 503 "Can't check the class list right now, try again in a minute"), then
   requires: the NetID is in it (case-insensitive), first and last name equal after
   normalisation (trim, collapse spaces, case-fold, strip accents), and that NetID is not set up
   yet. Success → `{"identity_token": "...", "name": "Bob Li", "netid": "bl642"}` (valid 10
   minutes, bound to that NetID and that class login). Any failure → 400 with ONE generic
   message ("This name and NetID are not on the class list, or this NetID is already set up.
   Check them, or ask your professor.") — never which part was wrong.
   Limits: 5 tries a minute per setup token. 5 failed tries in an hour for the same typed NetID
   (normalised: trimmed, lower case) on one class login → that NetID is refused for the rest of
   the hour, with the same generic message. Failed tries for 40 DIFFERENT NetIDs in an hour on
   one class login (someone guessing who is in the class) → the class login is disabled (the
   professor sees it and creates a new one). A NetID that is on the list with the right name but
   already set up gets the same generic message and is not counted.
   Signing in with the class login itself (`POST /api/auth/login`) has no per-username lockout
   (a whole lab shares that username, often behind one NAT): per IP, after 60 wrong class-login
   passwords in 5 minutes wrong ones get 429; the right password is always accepted.
3. `POST /api/setup/account` (Bearer <identity_token>) body `{"username","password","password2"}`
   → username 3–32 of [a-z0-9._-], unique (409 "That username is taken"); password (2026-10-24,
   the FairPlay web set-up shows the same rules as you type): at least 10 characters with at
   least one small letter, one capital letter, one digit and one symbol (any character that is
   not a letter, digit or space; Unicode letters count as letters) — else 400 "Password needs at
   least 10 characters, a capital letter, a small letter, a number and a symbol."; not equal
   (case-insensitive) to the username or NetID — else 400 "Your password must not be your
   username or NetID."; both entries equal — else 400 "The two passwords are not the same."
   (checked in that order). Creates or completes the NetID's account
   (role student, `set_up_at` = now), increments the class login's `used`, returns the normal
   login response (`token`, role, etc.). Identity tokens are single-use.

Student's classes (course cards, 2026-10-24): `GET /api/student/classes` (FairPlay student
sign-in) lists the student's FairPlay classes (joined with a join code) AND, for a student with a
`netid`, their DKU OJ classes, fetched from DKU OJ (§2 `students/<netid>/classes`, signed fp->oj,
one try, 3 s timeout), kept 60 s per student. DKU OJ unreachable → the classes FairPlay knows
(the class the student set up through, the classes of synced exams whose roster has them, mirror
classes they are a member of), kept 15 s. FairPlay's own mirror copies of DKU OJ classes
(`classes.external_ref = "dkuoj:<id>"`) are never listed as FairPlay classes. Each item (sorted
by course, then name):

```json
{"id": "dkuoj:31" | 12, "name": "CS 101 Section A", "course": "CS 101 Programming" | null,
 "source": "dkuoj" | "fairplay", "teacher": "Shuai Dong" | null,
 "upcoming_exams": 2, "next_exam": {"title": "...", "starts_at": "<iso>|null", "status": "draft|live|closed"} | null,
 "can_leave": false, "term": "", "joined_at": "<iso>" | null}
```
`upcoming_exams`/`next_exam`: DKU OJ classes — the student's synced exams (on its roster) whose
`external_meta.classes` has the class and that are not closed; FairPlay classes — the class's
exams that are live, or draft with a date (not paper, not the trial). `next_exam` = a live one
first, else the soonest `starts_at`. DKU OJ classes have `can_leave: false` (enrollment is DKU
OJ's); FairPlay classes keep `term` and `joined_at` and have `can_leave: true`, `course: null`.
`GET /api/student/exams` items carry `"class_ids": ["dkuoj:31", ...] | ["12"] | []`: DKU OJ
exams every class of `external_meta.classes` as `"dkuoj:<id>"`, other exams `[String(class_id)]`
(empty when the exam has no class). An exam belongs to a course card when
`exam.class_ids.includes(String(card.id))`.
`POST /api/student/classes/join` with a class login username (any, also an old one; any case)
→ 400 "That is a class login for first sign-in. Your DKU OJ courses appear here by themselves."

Audit: every setup success, failure (also a NetID blocked for the hour:
`setup_netid_blocked`), reset and class-login creation is an event the professor can see (FairPlay keeps `class_login_events`; `GET /classes/<id>/status` may add
`"recent": [...]`).

## 5. DKU OJ pages (teachers/admins only)

- Top menu **FAIRPLAY** (after SUBMISSIONS) when the bridge is on and the user teaches at least
  one course (or is staff).
- `/fairplay/` course cards: courses the user administers/teaches; per card the class count,
  student count, set-up count (from FairPlay status, cached 60 s; FairPlay down → "FairPlay not
  reachable", page still works), FairPlay exam count, next FairPlay exam. The page waits for
  FairPlay at most 3 s per call; after the first call that can't reach it, or once 2 s were
  spent in total, it makes no more calls and shows cached counts or "FairPlay not reachable".
- `/fairplay/<course>/` tabs **Exams** (only `fairplay_required` assignments of that course),
  **Students** (per class; Reset with inline confirm), **Class login (for first sign-in)** (per
  class the user manages; the Exams and Students tabs show a hint linking to it). Per class:
  username (Copy), password masked `••••••••` with **Show** (toggles) and **Copy**
  (`navigator.clipboard.writeText`, select-and-copy fallback) and Print, created, valid until,
  used by N students, Create new / Stop (inline confirm) and a **History** table (username,
  created, ended / valid until, why, used). The tab always asks FairPlay afresh; the password is
  in the page only inside the masked element's data attribute; the page is
  `Cache-Control: no-store` (all course tabs are); only course admins, the class's instructors
  and site staff get it (other teachers of the course see no classes there). Create redirects
  back to the tab (a reload can't make another login). DKU OJ's 60 s cache of the class status
  (course cards) never holds the password: it is removed before caching, and only a fresh call
  returns it. DKU OJ never stores or logs it.
- Assignment tab **FairPlay** (`/contest/<key>/fairplay`, editors only, only when
  `fairplay_required`): before opening (settings summary, who can start, Open exam, not-set-up
  warning); live (start code + New code, invigilator code Show/Hide, closes at, Add 10 min,
  Close with confirm, counts, waiting approvals, student table polled via a DKU OJ JSON view that
  calls FairPlay: one request at a time, the next 5 s after the previous answer or error, none
  while the browser tab is hidden); after close (reports per student).
- All professor actions are POST with CSRF, go through DKU OJ views that check the permission
  (`contest.is_editable_by(user)` for exams; course admin / class instructor for classes) and
  then call FairPlay signed. Nothing FairPlay returns is rendered unescaped.

## 6. Retired

- The "Open FairPlay live room" signed link and FairPlay's `/integrations/dkuoj/live` sign-in
  are removed (DKU OJ exams are run from DKU OJ). FairPlay's own teacher dashboard keeps working
  for FairPlay-native exams.

## 7. Independence

- FairPlay down: DKU OJ pages load, FairPlay boxes say "FairPlay not reachable"; nothing else in
  DKU OJ depends on it. DKU OJ down: running FairPlay exams continue; hand-ins queue (outbox);
  new student set-ups wait (503 message).
- No shared database; every cross call has timeouts; the protocol is versioned (`v2` line in
  the canonical string, `/v1` paths kept for the URLs).

## 8. Student review after hand-in (2026-10-24)

The professor decides in DKU OJ what students can see in FairPlay after handing in, at any time
(before, during or after the exam). FairPlay enforces it.

| Value | Students see after handing in |
|---|---|
| `nothing` (default) | only "Handed in" (receipt, time) |
| `answers` | their own answers, read-only: no points, no correct answers, no feedback, no class average |
| `answers_and_results` | their answers; once DKU OJ releases the result (v1 results feed), also the points and feedback |

DKU OJ:
- `Contest.fairplay_review` (CharField, choices above, default `nothing`) and
  `fairplay_review_updated_at` (set whenever the value changes; migration
  `0221_contest_fairplay_review`).
- Changed on the assignment's **FairPlay** tab: "Student review after hand-in", three radio
  options + Save (`POST /contest/<key>/fairplay/review`, CSRF, `contest.is_editable_by`), shown in
  every state and also when FairPlay is down. Also in the assignment editor's FairPlay panel
  ("After handing in, students can see", same permission as the other FairPlay fields).
- On a change DKU OJ saves first, then calls `POST /exams/<key>/review` signed (the editor: after
  the commit, 3 s wait; a failure only shows "FairPlay will get the change within a few minutes").
- The exam list (v1 `GET exams`) carries it in `settings`: `"review": "...", "review_updated_at":
  "<iso>" | null`. It is part of the entry's digest, so a change is listed again (also up to 30 days
  after the end). The package (`package_version`) does not change.

FairPlay:
- Stores it per exam (`Exam.review_after_handin`; `external_meta.review_updated_at`). The newer
  DKU OJ value wins: a value (from the list or the direct call) whose `updated_at` is older than
  the stored one is ignored, so a list fetched just before the professor's change can't undo it.
  Applied also to running (frozen) and closed exams. Missing/unknown → `nothing`.
- Enforced in the student API (`app/review.py`):
  - `GET /api/student/grades/{exam_id}`: `nothing` → 403 `{"detail": "Your teacher has not
    opened the review for this exam."}`; `answers`, and `answers_and_results` before the release →
    200 with `"review": "answers", "released": false`, `score`/`percent`/`class_average_percent`
    null, per question `given`, `points` (the question's maximum), `points_awarded: null`, `status`
    `answered|unanswered`, `comment: ""`, `correct_answer: null`; `answers_and_results` once
    released → the full result with `"review": "answers_and_results", "released": true`.
  - `GET /api/student/grades` and `GET /api/student/exams`: `review` per DKU OJ exam; `released`
    is false and `score`/`percent` null unless the level is `answers_and_results` and the result
    is released.
  - `GET /api/student/attempts/{aid}` of a handed-in attempt with `nothing`: `questions: []`,
    `answers: {}` (the done page needs neither).
  - `GET /api/student/grades/{exam_id}/assets/<name>`: only while answers are shown.
  - Correct answers never reach students (DKU OJ sends no answer keys).
- Teacher views are not affected. FairPlay-native exams have no such setting (they keep "release
  grades"); their responses carry no `review`.
- Web: the Grades list and past-exam rows show "Handed in" (no link) for `nothing`, "See your
  answers" for `answers` (and before a release), the score + "See feedback" once released; the
  review page shows the closed message, the answers-only view (no score) or the result.
