# DKU OJ ↔ FairPlay bridge, v2: professors use only DKU OJ

Status: build contract (2026-10-24; review fixes 2026-10-24). Extends `docs/dkuoj-bridge.md` (v1: exam list, package,
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
  from a built-in list. Stored ONLY as a password hash. Valid 14 days. The plain password is
  returned only in this response.
- **`DELETE /classes/<class_id>/login`** → `{"ok": true}` (idempotent).
- **`GET /classes/<class_id>/status`** →
  ```json
  {"login": {"username": "cs101a-m4tz", "created_at": "...", "expires_at": "...", "used": 28} | null,
   "students": [{"netid": "bl642", "set_up": true, "username": "boblee", "set_up_at": "...",
                 "via_this_class": true}]}
  ```
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
                "strong_lock":true,"resume_needs_approval":true},
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
   → username 3–32 of [a-z0-9._-], unique (409 "That username is taken"); password ≥ 10, not
   equal to the username or NetID, both equal. Creates or completes the NetID's account
   (role student, `set_up_at` = now), increments the class login's `used`, returns the normal
   login response (`token`, role, etc.). Identity tokens are single-use.

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
  **Students** (per class; Reset with inline confirm), **Class login** (per class; Create /
  Create new / Stop; the password shown once with Copy and Print).
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
