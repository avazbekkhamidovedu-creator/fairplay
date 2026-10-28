# DKU OJ ↔ FairPlay bridge, v2: professors use only DKU OJ

Status: build contract (2026-10-24; review fixes 2026-10-24; security review fixes 2026-10-24 (§1 rotation / transport / bounded answers, §3 reset refusal and websites_blocked); class-login password viewable, login
history, strong student passwords, student classes, student review after hand-in §8 2026-10-24, five review levels §8 2026-10-24; early upload window, website links, Duke Box and `POST /exams/<key>/settings` §10 2026-10-24; DKU OJ
without schema changes §11 2026-10-24). Extends `docs/dkuoj-bridge.md` (v1: exam list, package,
assets, uploads, submissions, results). Everything in v1 stays, except where this file changes it
(§1 signing replaces the Bearer token; §6 retires the live-room link).

Product rules (from Mister):
- Professors never sign in to FairPlay for DKU OJ classes. Everything is in DKU OJ: a top-menu
  item **FAIRPLAY** (teachers/admins only) and a **FairPlay** tab on FairPlay assignments.
- The FAIRPLAY pages list ONLY assignments whose lock is FairPlay (§11: assignments with the DKU
  OJ contest tag `fairplay`; this file's older wording `fairplay_required` means that tag).
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

**Token rotation** (security review 2026-10-24): `DKUOJ_TOKEN` / `FAIRPLAY_TOKEN` may hold TWO
comma-separated tokens, `<new>,<old>`. A server signs with the FIRST and accepts a request
signed with ANY of them (each token gives its own pair of direction keys; every token is
compared, so the timing doesn't tell which matched). More than two → the server refuses to
start. To rotate without downtime:
1. FairPlay: `DKUOJ_TOKEN=<old>,<new>` (still signs with old; accepts new). Restart.
2. DKU OJ: `FAIRPLAY_TOKEN=<new>,<old>` (signs with new; accepts old). Restart.
3. FairPlay: `DKUOJ_TOKEN=<new>,<old>`, then `<new>` alone. Restart.
4. DKU OJ: `FAIRPLAY_TOKEN=<new>`. Restart. The old token is now refused everywhere.

**Transport** (security review 2026-10-24): FairPlay refuses to start when `DKUOJ_URL` is
plain `http://` to anything but a loopback address (`localhost`, `127.0.0.0/8`, `::1`):
the signatures don't hide rosters, packages and grades. `https://` always; plain http to
another machine only with `FAIRPLAY_DEV=1` or `DKUOJ_ALLOW_INSECURE=1` (a test network).

The receiver:
1. bridge off (no token) → 404; missing/invalid headers → 401 `{"detail":"bad signature"}`;
2. `|now - timestamp| > 60` → 401;
3. compares the signature with `hmac.compare_digest`;
4. rejects a nonce seen in the last 180 s (cache/Redis key `fpb:nonce:<nonce>`, `SET NX EX 180`;
   longer than the 120 s a timestamp can be accepted in, with margin) → 401. The cache must be
   shared by every worker: DKU OJ's system check `judge.E101` fails with the bridge on and a
   LocMem/Dummy/FileBased default cache;
5. bodies over the endpoint's limit → 413 before hashing.

Responses are not signed (TLS protects them in production): DKU OJ refuses (system check
`judge.E102`, and every call answers "FairPlay not reachable") a plain `http://` `FAIRPLAY_URL`
that is not the loopback, unless `DEBUG` or `FAIRPLAY_ALLOW_INSECURE = True` (local kits only).
DKU OJ reads at most 2 MB of a FairPlay answer, and the whole call within connect + read
timeout (a slow trickle is cut off) → "FairPlay not reachable" / an error. Clients use timeouts (connect 5 s,
read 15 s; DKU OJ page loads and the live table's poll: 3 s), never retry a POST that may have
succeeded unless it is idempotent (marked below).

FairPlay reads every DKU OJ answer as a stream (security review 2026-10-24): it sends
`Accept-Encoding: identity`; a gzip / deflate body is still read, but its size is counted
AFTER decompression, and any other `Content-Encoding` is refused. A body past the endpoint's
cap is dropped as soon as it passes the cap (not after downloading it): package 5 MB, asset
10 MB, exam list / roster / student classes / results pages 2 MB each, upload and submission
receipts 256 KB, the body of a refusal (non-2xx) 64 KB (then only its status counts). Each
try also has a total deadline, whatever the server does: 2 × the call's timeout, at most
30 s (a body trickled a byte at a time no longer outlasts the read timeout). A capped,
undecodable or late answer is the same "could not reach DKU OJ" failure as a network error
(too-large answers are not retried).

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
student). 404 unknown class, and (2026-10-24, §9) 404 for a class FairPlay has no business
with: the class must be in `classes` of a `fairplay_required` assignment, have its course in
such an assignment's `organizations`, or have had a FairPlay class login made for it in DKU OJ
(§11: recorded in DKU OJ's shared cache, `fpb:class-login:<id>`, 180 days, set before the first
create call and renewed whenever a manager of the class sees an active login; no column).

**`GET /api/fairplay/v1/students/<netid>/classes`** (2026-10-24) — the ACTIVE classes the NetID
is a student of, by the same rule as the roster above (a class where they are an admin,
instructor, curator, ... is left out), for the course cards in the FairPlay app. Only classes
the roster endpoint serves (§9) are listed; a NetID on none of their rosters gets
`{"classes": []}`.

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
  Security review 2026-10-24: DKU OJ may reset only accounts DKU OJ alone gave access to. An
  account that is also a member of a class made in FairPlay (not a DKU OJ mirror class,
  `external_ref` not `dkuoj:*`), of an LMS course, or has an attempt on an exam that is not
  from DKU OJ (the sample exam doesn't count) → 403
  `{"detail": "This account is also used for FairPlay classes; reset it in FairPlay."}`
  (event `student_reset_refused` in the class's `recent`). Every reset and refusal is an
  event (`student_reset` / `student_reset_refused`, with the acting user).

### Exam control (key = DKU OJ assignment key)

- **`GET /exams/<key>`** → the FairPlay tab's data:
  ```json
  {"synced": true, "package_ready": true, "questions": 3, "points": 26,
   "status": "draft|live|closed", "start_code": "219074" | null,
   "invigilator_code": "583204" | null, "closes_at": "...", "open_to": "all" | ["bl642"],
   "settings": {"calculator":"scientific","open_book":false,"websites":["docs.python.org"],
                "website_links":[{"host":"duke.app.box.com","url":"https://duke.app.box.com/folder/0","title":"Course folder"}],
                "companions":[{"for":"duke.app.box.com","name":"Duke Box","sites":["app.box.com","..."]}],
                "upload_opens_days":2,"set_in_fairplay":["upload_opens_days","websites"],
                "websites_blocked":[{"site":"chatgpt.com","reason":"AI assistant"}],
                "strong_lock":true,"resume_needs_approval":true,"review":"nothing"},
   "counts": {"roster": 68, "set_up": 61, "not_started": 3, "writing": 23, "offline": 1,
              "waiting": 1, "handed_in": 41},
   "students": [{"netid":"bl642","name":"Bob Li","set_up":true,"status":"writing|not_started|offline|waiting|handed_in",
                 "answered":2,"questions":3,"seconds_left":2460,"warnings":["Left the app once (8 s)"]}],
   "server_time": "..."}
  ```
  `synced:false` (404 is not used) when FairPlay hasn't got the assignment yet.
  `settings.websites` = the sites the lock really opens; `settings.websites_blocked` = the
  ones DKU OJ asked for that FairPlay refused, each with its reason (always present, `[]`
  when none). Security review 2026-10-24: FairPlay has its own policy for sites an external
  source opens (`app/integrations/site_policy.py`): a built-in deny-list of AI chat and
  assistants (openai.com, chatgpt.com, claude.ai, gemini.google.com, copilot.microsoft.com,
  perplexity.ai, deepseek.com, kimi / doubao / tongyi / yiyan, huggingface.co, ...),
  messaging (whatsapp.com, telegram.org, wx.qq.com, discord.com, slack.com,
  teams.microsoft.com, ...) and file sharing / notes (drive.google.com, docs.google.com,
  dropbox.com, onedrive.live.com, notion.so, pastebin.com, gist.github.com, ...); a deny
  entry covers its subdomains, and a wildcard that reaches one (`*.google.com` →
  gemini.google.com) is refused too (`github.com` itself is refused for Copilot chat;
  `docs.github.com` is fine). Optional `FAIRPLAY_EXTERNAL_SITE_ALLOWLIST` (comma-separated, the
  teacher list's form, e.g. `docs.python.org,*.cppreference.com`): when set, ONLY sites it
  covers may be used (reason "not on this FairPlay server's list of sites for external
  exams"). A name that isn't a usable site (`*.com`, a path, an IP) is listed too, with the
  reason. FairPlay-native exams are not affected. DKU OJ shows `websites_blocked` on the
  FairPlay tab.
  2026-10-24 (§10): `website_links` = start pages (allowed websites given as full links),
  `companions` = sites the lock also opens so a service works (Duke Box), `upload_opens_days`
  = the early upload window (0 = off), `set_in_fairplay` = which of `upload_opens_days` /
  `websites` were set with `POST /exams/<key>/settings` (they win over the exam list).
- **`POST /exams/<key>/settings`** (2026-10-24, §10; every setting §11) body, any of
  `{"calculator": "off|scientific|graphing" | null, "open_book": bool | null, "strong_lock": bool | null,
  "resume_needs_approval": bool | null, "upload_opens_days": 0..7 | null, "review": "<level>",
  "websites": ["docs.python.org", "https://duke.app.box.com/folder/0", "Course folder https://duke.app.box.com/folder/123", {"url": "...", "title": "..."}] | null}`
  → GET shape. Editor-checked like the other exam endpoints (403). §11: an assignment FairPlay
  has not synced yet → the settings are KEPT for that key (pending, applied at its first sync;
  answer: the not-synced shape whose `settings` are FairPlay's defaults with the pending ones;
  key longer than 56 characters → 400; more than 2000 keys pending → 429; pending settings older
  than 180 days are dropped). After the first student started (or closed): `review` still
  changes; any other field that DIFFERS from the exam's → 409 (equal values are no change, so
  DKU OJ may send its whole form). No field → 400; a field of the wrong type → 400;
  `upload_opens_days` not a whole number 0–7 → 400; `websites` not a list of strings /
  `{url, title?}` (≤ 100) → 400; `review` null → 400.
- **`POST /exams/<key>/open`** body `{"open_to": "all" | ["netid", ...]}` → same shape as GET.
  Idempotent (already live: updates `open_to`).
- **`POST /exams/<key>/close`** → GET shape. Same effect as the FairPlay teacher Close (writers
  handed in with what is saved, delivered to DKU OJ through the outbox).
- **`POST /exams/<key>/add-time`** body `{"minutes": 10}` (1–120) → GET shape. Adds the
  minutes to the time limit, to every attempt still writing AND to the close time: FairPlay
  keeps them (`extra_close_minutes`, kept across syncs) and closes at DKU OJ's end time + the
  added minutes, so `closes_at` in the answer moves; students starting after it get the time,
  and the automatic close waits for it. The hand-ins then reach DKU OJ after the assignment's
  end time: DKU OJ files them anyway (until 14 days after the close, §9). DKU OJ counts the
  minutes added through its FairPlay tab (§11: in its shared cache); lateness is judged
  against end time + those minutes (§9).
- **`POST /exams/<key>/new-code`** → GET shape (new start code).
- **`POST /exams/<key>/review`** body `{"review": "nothing" | "score" | "breakdown" | "answers" | "answers_and_results",
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

## 8. Student review after hand-in (2026-10-24; five levels 2026-10-24)

The professor decides in DKU OJ what students can see in FairPlay after handing in, at any time
(before, during or after the exam). FairPlay enforces it on every student endpoint; data a level
does not allow is never sent, not even in fields the page does not show.

| Value | DKU OJ label | Students see after handing in |
|---|---|---|
| `nothing` (default) | Nothing (just "Handed in") | only "Handed in" (receipt, time) |
| `score` | Their grade only | once DKU OJ releases the result: the total score and percent. No questions, no per-question points, no answers, no feedback |
| `breakdown` | Their grade and points per question | once released: the total, plus per question its number (`label`), `title` and points earned / possible. No question text or options, no answers, no correct answers, no feedback |
| `answers` | Their own answers (no scores) | their own answers, read-only: no points, no correct answers, no feedback, no class average |
| `answers_and_results` | Everything: answers, scores and feedback | their answers; once released also the total, the points per question, the correct answers (DKU OJ sends none) and the feedback |

`score` and `breakdown` were added later; the other three values keep their meaning. No class
average is shown below `answers_and_results`.

DKU OJ (§11: DKU OJ stores nothing; the value lives in FairPlay):
- The five choices and their one-line descriptions are constants in DKU OJ
  (`judge/utils/fairplay.py`: `FAIRPLAY_REVIEW_CHOICES`, `FAIRPLAY_REVIEW_HELP`). The value is
  read from FairPlay's `GET /exams/<key>` and set with `POST /exams/<key>/settings {"review": ...}`
  (shown and changeable only while FairPlay answers). The rest of this list is the pre-§11
  design, kept for FairPlay's compatibility with an exam list that still has `settings`.
- Changed on the assignment's **FairPlay** tab: "Student review after hand-in", five radio
  options, each with its one-line description, + Save (`POST /contest/<key>/fairplay/review`,
  CSRF, `contest.is_editable_by`), shown in every state and also when FairPlay is down. Also in
  the assignment editor's FairPlay panel ("After handing in, students can see", a dropdown with
  the same five labels; same permission as the other FairPlay fields).
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
  - `GET /api/student/grades/{exam_id}`:
    - `nothing` → 403 `{"detail": "Your teacher has not opened the review for this exam."}`;
    - `score` → 200 `{"review": "score", "released", "score", "percent", "total_points",
      "questions": [], "class_average_percent": null, "overall_comment": ""}` (`score`/`percent`
      null before the release);
    - `breakdown` → as `score`, with `questions` = `[{"question_id", "index", "label", "title",
      "points", "points_awarded", "status"}]` only (`points_awarded` null and `status` `pending`
      before the release or for a question DKU OJ sent no points for; else `correct|partial|wrong|unanswered`);
    - `answers`, and `answers_and_results` before the release → 200 with `"review": "answers",
      "released": false`, `score`/`percent`/`class_average_percent` null, per question `given`,
      `points` (the question's maximum), `points_awarded: null`, `status` `answered|unanswered`,
      `comment: ""`, `correct_answer: null`;
    - `answers_and_results` once released → the full result with `"review": "answers_and_results",
      "released": true`.
  - `GET /api/student/grades` and `GET /api/student/exams`: `review` per DKU OJ exam; `released`
    is false and `score`/`percent` null unless the level is `score`, `breakdown` or
    `answers_and_results` and the result is released. With `breakdown` (released) a grade also
    has `breakdown: [{"index", "label", "points", "points_awarded"}]` (the card's chips).
  - `GET /api/student/attempts/{aid}` of a handed-in attempt with `nothing`, `score` or
    `breakdown`: `questions: []`, `answers: {}`, no `answer_files` (the done page needs none).
  - `GET /api/student/grades/{exam_id}/assets/<name>` and, while the exam is still live, `GET
    /api/student/attempts/{aid}/assets/<name>` of a handed-in attempt: only while answers are
    shown (`answers`, `answers_and_results`); else 404.
  - `GET /api/student/exams/{id}/package` and `/package-key` for a student who handed in, while
    the exam is live: 409 "You handed this exam in." unless answers are shown.
  - Correct answers never reach students (DKU OJ sends no answer keys).
- Teacher views are not affected. FairPlay-native exams have no such setting (they keep "release
  grades"); their responses carry no `review`.
- Web: grade cards (score ring) on the Grades page and the Past tabs: `nothing` → "Handed in ·
  your teacher keeps this one private" (no link); `score` → the ring with the percent and "x / y
  points" (no link); `breakdown` → the ring plus a chip per question ("Q1 · 4/4"; more than 12 →
  "All N questions →" opens the grade page with a points table); `answers` → "Your answers →";
  `answers_and_results` → "Your answers →" before, the ring + "Feedback →" after the release.

## 9. Security review fixes (2026-10-24)

DKU OJ side (FairPlay → DKU OJ calls are trusted only as far as below, even when signed):

- **Class scope** (§2): roster and student classes only for classes a `fairplay_required`
  assignment uses (its `classes`, or classes of its `organizations`) or that have a FairPlay
  class login (§11: recorded in DKU OJ's shared cache); else 404 / not listed.
- **Exam list** (v1 `GET exams`): `editors` and `roster` entries no longer carry `email`
  (FairPlay never used it): `{"username","first_name","last_name"}` (+ `extra_time_seconds` in
  the roster).
- **Hand-ins** (v1 `POST exams/<key>/submissions`, `POST exams/<key>/uploads`):
  - one FairPlay attempt per student and exam: a different `attempt_id` for a student who already
    has a hand-in → `409 {"detail": "This student already has a FairPlay hand-in for this exam
    (attempt <id>). A professor can clear it ..."}`. The professor clears it on the student's
    FairPlay report page in DKU OJ ("Clear FairPlay hand-in"); FairPlay then delivers again
    (the filed submissions stay; the cleared attempt's replay still gets its receipt).
    The same attempt id stays idempotent (same body → same receipt, different body → 409).
  - refused after the exam's close + 14 days → `410 {"detail": "This exam closed more than 14 days
    ago; ..."}`; close = `end_time` + the minutes added from DKU OJ. A replay of an already filed
    attempt still gets its receipt.
  - lateness: late when the hand-in counts as made more than 120 s after the close. It counts as
    made at FairPlay's `handed_in_at`, but never later than DKU OJ's receive time (a hand-in that
    waited in FairPlay's outbox while DKU OJ was down is not late; a time in the future counts as
    the receive time). The 14-day window and the one-attempt rule bound what a back-dated time
    can do, and the report shows both times. The participation start is clamped into
    [start_time, now].
  - `integrity_report_url` is kept only when it starts with `FAIRPLAY_URL + "/"` (or DKU OJ's
    optional `FAIRPLAY_PUBLIC_URL + "/"`, FairPlay's browser address when it differs); otherwise
    it is dropped (the hand-in is still filed).
- **Signing** (§1): two-token rotation; shared nonce cache enforced by a system check; https
  `FAIRPLAY_URL` off the loopback; capped, time-limited reading of FairPlay's answers.

## 10. Early upload window, allowed websites as links, Duke Box (2026-10-24)

Product rule (from Mister): for an open-book exam the professor may let students upload their
files one or more days before the exam; during that time the exam shows on the student's pages
with only the upload. Professors and students use Duke Box: a Box link the professor allows
must open, and every page under that site too.

DKU OJ's database schema does not change for this: the values are kept in FairPlay
(`external_meta.fairplay_settings` of the exam) and set with `POST /exams/<key>/settings` (§3).
A later step moves all FairPlay settings of DKU OJ assignments into FairPlay this way.

**Precedence.** A field set with `POST /exams/<key>/settings` wins over the same field in the
exam list (`settings.upload_opens_days`, `settings.websites` / `settings.website_links`): every
later sync keeps the FairPlay value. Sending the field as `null` hands it back to DKU OJ (the
value from the last exam list applies at once). Fields not sent are left as they are.
`settings.set_in_fairplay` in the GET shape says which fields are FairPlay's. Like all exam
settings, they are fixed once the first student started (409).

**Upload window** (`upload_opens_days`, 0 = off, 1–7). Only for open-book exams with a start
time (DKU OJ `start_time`). From `start_time − N days` until `start_time` (or until the exam is
opened, if earlier) the exam is listed to its students as "Upcoming — upload your files ·
uploads close <time>" with only the student's own materials upload (`/api/student/exams/<id>/materials`,
same limits: 5 files, 20 MB, PDF/PNG/JPEG/TXT/MD). The server gives nothing about the questions:
`question_count: null`, no `package_id`, start / package / package-key refuse (409). After the
window the files are fixed and the student reads them in the exam. Answers to file-upload
questions are part of the attempt and are made during the exam only. The exam list may also
carry `settings.upload_opens_days` (DKU OJ does not send it today).

**Websites as links.** A website may be a full link (`https://duke.app.box.com/folder/0`,
optionally after a title). Its host goes into `settings.websites` (every path of that host is
allowed: `.../folder/0/somewherenew/123` opens too) and the link into `settings.website_links`
(`[{host, url, title}]`, at most 20): the student's websites tab opens it first. `websites`
keeps its format for the apps (lowercase hosts, `*.x.com`; at most 20). The exam list may send
links in `settings.websites` or `settings.website_links: [{url, title}]` too.

**Duke Box.** FairPlay's site policy for external exams (§3) refuses file sharing, `box.com`
among it. Exceptions (`FAIRPLAY_SITE_POLICY_EXCEPTIONS`, exact hosts; default
`duke.app.box.com,duke.box.com`) are allowed: Duke Box is the university's own storage. Box in
general (`app.box.com`, `*.box.com`, other organisations' Box) stays refused as a site of its
own. Trade-off: Box shows the student their OWN Box (all their files, files shared with them,
Box Notes they can open): allowing it is the professor's choice, like open book.

**Companions** (`app/site_companions.py`). A service works only when a few more hosts load:
for a Box site the lock also opens `app.box.com`, `<tenant>.app.box.com`, `account.box.com`,
`<tenant>.account.box.com`, `api.box.com`, `sso.services.box.net`, `*.boxcdn.net`,
`*.boxcloud.com`; for Duke Box also Duke's sign-in `shib.oit.duke.edu` and Duo
`*.duosecurity.com`. They are in the GET shape's `settings.companions` (the FairPlay tab shows
"Also opened so Duke Box works: …") and in the student's `tools.allowed_sites` (what the page
hands the app), never in `websites`. Websites + companions are at most 64 entries (the Windows
app reads 64). Sources: Box "Configuring a firewall for Box applications and services"
(support.box.com/hc/en-us/articles/33552262497683); Duke OIT Shibboleth documentation
(authentication.oit.duke.edu/manager/documentation: IdP shib.oit.duke.edu); Duke OIT Duo
(oit.duke.edu/service/duo/). Left out on purpose: Box uploads (`upload.*.box.com`) and Box Notes
(`notes.services.box.com`).

Note: in the apps the websites pane is a frame; Box / Duke sign-in and Duo do not work inside a
frame (and Safari blocks third-party cookies), so a native site view in the apps is planned
separately. This section only makes the data and the web side right.

## 11. DKU OJ without schema changes (2026-10-24)

Product rule (from Mister, for DKU OJ's acceptance): the integration adds NO column, table or
migration to DKU OJ. DKU OJ only reads its own data and makes the writes it already makes
(submissions, participations, attaching existing contest tags, files in its submission store).
Everything FairPlay-specific lives in FairPlay and is exchanged through the signed API.

DKU OJ side:
- **Which assignments are FairPlay exams:** the DMOJ contest tag `fairplay` (`ContestTag`, made on
  first use). The editor's lock option FairPlay sets and clears it (the tag picker neither shows
  nor sets it; "Add new" refuses the name); the admin may attach it (checked: not with SEB, needs
  a student list). Everything that read `fairplay_required` reads the tag.
- **Settings:** none stored. The editor's FairPlay panel and the FairPlay tab read FairPlay's
  `GET /exams/<key>` (`settings`) and save with `POST /exams/<key>/settings` (only when changed;
  the tab's form sends its fields, the review form only `review`). FairPlay not reachable: the
  panel and the tab say "FairPlay not reachable: the settings can't be shown or changed right
  now"; what the professor typed is not dropped (kept in their session and offered again on the
  tab, marked "NOT saved"). A new assignment's settings go to FairPlay as pending (§3). An
  assignment whose key changes takes its settings along (read under the old key, saved under the
  new one).
- **Exam list:** entries have no `settings`. Change tracking (`updated_at`) is a digest per entry
  in DKU OJ's shared cache (`fpb:exam-track:<contest id>`, 60 days); a digest the cache lost counts
  as a change (the entry is listed again; FairPlay applies it idempotently). The first page also
  carries `"active_keys": [...]` (every FairPlay exam whose end time is after `active_since`,
  = now − 30 days) and `"active_since": "<iso>"`. Switched-off assignments are no longer listed
  as `"removed": true` entries.
- **Hand-ins** (`POST exams/<key>/submissions`): the receipt records live in the shared cache
  (`fpb:attempt:<sha256(attempt id)>` and `fpb:handin:<contest>:<student>`, 60 days). A cache lock
  per student and exam serialises deliveries. Before filing, DKU OJ also checks its database: a
  student who already has submissions in the exam (newer than a professor's clear) but no hand-in
  record (the cache lost it) is refused with `409 {"detail": "This student already has answers on
  file for this exam in DKU OJ, but DKU OJ no longer has the record of that hand-in. ..."}` and the
  professor is warned on the assignment page and the FairPlay tab (rather than filing twice). Rare
  edge: a replay of the SAME attempt after the cache lost its record also gets that 409 (FairPlay
  marks the delivery dead and shows it to the teacher; the answers are already in DKU OJ). "Clear
  FairPlay hand-in" deletes the hand-in record and remembers the newest submission id
  (`fpb:cleared:...`), so later submissions count as a new hand-in. One attempt per student and the
  idempotent replay (same body → same receipt, other body → 409) are unchanged.
- **Uploads** (`POST exams/<key>/uploads`): the file is staged in DKU OJ's submission store at
  `fairplay_uploads/<contest>/<student>/<problem>/<token><ext>` (tracked by the store's own record
  of every saved file); the upload id is `up_<token>` and only works for that student, exam and
  question. On hand-in it becomes a normal file submission (graded in DKU OJ as usual) and the
  staged copy is deleted; unclaimed ones are deleted after 15 days. The original file name is kept
  in the cache (lost: `answer<ext>`).
- **Results** (`GET exams/<key>/results`): the students are the exam's LIVE participations (not
  staff); change tracking is a digest per student in the cache (one entry per exam). A result
  without a digest is listed (also unreleased, `"released": false`), so a withdrawn release is
  never missed.
- **Close time:** "Add time" on the FairPlay tab counts the minutes in the cache
  (`fpb:added-minutes:<contest>`). Lateness and the 14-day window use end time + that count; if
  the cache lost it, the hand-in's `exam_closes_at` (FairPlay's close time, below) is used, at most
  8 hours past the end time.

FairPlay side:
- `POST /exams/<key>/settings` takes every setting and keeps settings for unsynced keys (§3);
  they are stored in `oj_state` rows `pending:<key>` and moved into the exam's
  `external_meta.fairplay_settings` (and `review_after_handin`) at its first sync.
- An exam-list entry without `settings` leaves the exam's settings alone (a new exam starts from
  FairPlay's defaults: calculator off, no websites, not open book, normal lock, resume needs
  approval, no early upload, review `nothing`); an entry WITH `settings` (older DKU OJ) is applied
  as before, and FairPlay-set fields still win.
- `GET /exams/<key>`: `settings.set_in_fairplay` lists every setting set through the endpoint;
  `settings.websites_input` = the websites as DKU OJ last sent them (for its form), when set.
- `active_keys`: after a complete pass, every DKU OJ exam of FairPlay that is not removed, whose
  end time is at least a day after `active_since` and whose key is not listed, is marked removed;
  one listed again is no longer removed. A list without `active_keys` changes nothing.
- The hand-in body (v1 submissions) gains `"exam_closes_at": "<iso Z>"` (FairPlay's close: DKU OJ's
  end time + the minutes added).

## 12. Per-student access codes (2026-10-28)

Signed oj->fp (§1) like the exam endpoints of §3, for an acting editor of the exam (403
otherwise). The codes are made for the students on the exam's synced list.

- **`GET /exams/<key>/access-codes.pdf`** → `200 application/pdf`, `Content-Disposition:
  attachment; filename="access-codes-<key>.pdf"`, `Cache-Control: no-store`. A code is made for
  every student on the list who has none (existing codes never change, so a second download is the
  same sheet); students without a NetID are skipped. Query `layout=both` (default) | `list` | `slips`
  (anything else: 422). One PDF with up to two parts: **Class list** for the invigilator (sorted by last
  name, then first name; columns #, Last name, First name, NetID, Access code in bold monospace and an
  empty Present box; 28 rows a page, header with exam title, course, generated time (UTC) and "Do not
  share. One code per student." repeated on each page) and **Slips** to cut out (same order, 12 per A4
  page, 2 x 6, dashed cut lines; last name, first name, NetID, the code in Courier-Bold 26 pt and the exam
  title in small type). Every page has "Page x of y". 50 students: both = 2 + 5 pages; 60 students: list
  3 pages, slips 5. Names are the roster's `first_name` / `last_name` (kept on the roster row at each
  sync, refreshed on every download; an entry with neither falls back to the account name split at
  its first space); a text with characters outside Latin-1 (Chinese names) is printed in the
  built-in CJK font. Unknown exam → 404; no student with a NetID on the list → 409
  `{"detail": "..."}`. Each download is an event `access_codes_downloaded` (acting user, number of
  codes, never a code) in the class's `recent` list.
- **`POST /exams/<key>/access-codes/reissue`** body `{"netid": "ep530"}` → `{"ok": true}`. The
  student's old code stops matching and a new one is made (event `access_code_reissued`). NetID
  missing → 400; not on the exam's list → 404.

Codes: 6 characters from `ABCDEFGHJKMNPQRSTUVWXYZ23456789`, random, unique within the exam,
matched ignoring case, spaces and dashes. Stored as an HMAC-SHA256 (key derived from the server
secret) and sealed (`app.lms.secretbox`) so the same sheet can be printed again; table
`exam_access_codes` (unique `(exam_id, netid)` and `(exam_id, code_hmac)`). Student sign-in with
a code is not part of this version. No new signing vectors: the format is unchanged.
