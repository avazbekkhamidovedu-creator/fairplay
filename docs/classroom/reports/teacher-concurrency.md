# Teacher writes under concurrency

Branch `worktree-agent-a3d8c15b5b3ac4d27` (from `fairplay-classroom` @ c5a9659). Date 2026-10-20.
Scope: correctness when many teachers (500+ professors) create and edit at the same time:
no 500s, no IntegrityError, no lost or mixed-up data. Speed is not in scope.

## Summary

Before, with 16–30 teachers acting together on a real multi-threaded server:

* **Creating questions failed most of the time.** In a 60 s mixed run with 30 teachers, 83 of 113
  question creates (73%) answered HTTP 500 `IntegrityError: Duplicate entry 'p0000NN' for key 'code'`.
  Under the same load after the fix there were 0 failures in 1,801 requests (90 s).
* Seven more write paths answered 500 under a burst of identical requests: assignment clone,
  question clone, add-group, class create, grade save, saving the same question from two tabs
  (deadlock), and a double-clicked Join. Assignment create answered 400 for 5 of 16 teachers.
* **Silent lost updates and mixed data.** Two tabs or two teachers saving the same question or
  assignment silently overwrote each other. A submission being judged, or a student joining, wrote a
  stale copy of the whole problem or assignment row over a teacher's edit saved a moment earlier.
  Saving a grade while a colleague clicked Release All left the ranking with a different score than
  the released grade. A second tab's new question was thrown away as "already saved". A double click
  on an import row's Create made the question twice.

After the fixes, every scenario below is clean at N = 16–24. The full mixed run has 0 failures. No
migration was added.

## How it was tested

* **Server:** gunicorn 26 `gthread` with 3 workers × 4 threads, from `/tmp/lt-venv`, on
  port 8011. Database: MariaDB 10.11 on the same box. Django runs it at READ COMMITTED (checked:
  `@@tx_isolation`). Redis DB 11 as the cache. Dev data comes from `seed_dev_data` plus 30
  instructors `t01..t30` (`scripts/concurrency/make_teachers.py`).
* **Race harness** (`classroom/scripts/concurrency/race.py`): for each scenario, N teachers log in
  first. Then N threads fire the same write at the same instant (`threading.Barrier`). Each thread
  has its own session and HTTP connection, so each uses its own DB connection. The harness prints
  the status histogram plus a data check, such as "distinct codes == N" or "released grade ==
  ranking score".
* **Soak** (`classroom/scripts/concurrency/soak.py`): 30 teachers loop with no barrier for 60–90 s.
  Each loop opens the add page, creates a question of a random type (single, multiple, true/false,
  blank, short, file) with a random existing or new tag, then opens its editor and saves it. It then
  creates an assignment with the question, opens the assignment editor and saves it, and sometimes
  creates a class.
* **"Before" numbers:** the fixed call sites were temporarily put back to c5a9659 with
  `git checkout c5a9659 -- <files>` and the server was reloaded. "After" numbers use this branch.
* **Deterministic tests** (`judge/views/tests/test_teacher_concurrency.py`, 37 tests). Django's
  `TestCase` cannot run two requests at once, so each test replays the race. At the moment the code
  under test opens the savepoint around its INSERT (after every "is it free?" check has passed), the
  row another teacher's request would have committed is inserted. The INSERT then really hits the
  unique index. A `SELECT … FOR UPDATE` is asserted where serialization is the fix, and stale-copy
  writes are replayed with a second model instance. **25 of the 32 behavioural tests fail on the
  c5a9659 call sites.** The other 7 cover the new helpers and backward-compatible behaviour. The
  deadlock-retry and lock-order tests were added afterwards (5 tests). The lock-order test fails
  without the submission-first lock.

### Burst results (harness, same instant)

| Scenario | Before (c5a9659) | After |
|---|---|---|
| 16 teachers create a question with the same suggested code | 14 × 500, 2 created | 16 created, 16 distinct codes |
| 16 create with no code (API) | 14 × 500 | 16 × 200 |
| 16 create with the same brand-new tag | 14 × 500 | 16 × 200, one tag row |
| 16 "add group" with the same name | 4 × 500 | 16 × 200 |
| 16 saves of one question (tabs) | 5 × 500 (deadlock 1213) | 16 × 200 |
| 16 assignment creates (auto key) | 5 × 400 ("ID None already exists") | 16 × 200 |
| 16 class creates (same generated name) | 2 × 500, 6 × form error | 24/24 → 302 (×5 runs at N = 24) |
| 16 assignment clones (same suggested key) | 13 × 500 | 16 × 302 |
| 16 question clones (same suggested code) | 14 × 500 | 16 × 302 |
| 12–16 grade saves on one ungraded submission | 3 × 500 (seen 2 of 5 runs) | 16 × 200, one grade row (×8 runs) |
| grading 10 submissions while Release All runs (6–8 rounds) | 3–4 released grades ≠ ranking score | 0 mismatches, 0 errors (×8 runs) |
| student double-clicks Join | 1 × 500 (seen 1 of 5 runs) | 0 (×4 runs) |
| 30-teacher mixed soak, 60–90 s | 83/113 question creates → 500 | 0 failures / 1,801 requests |

The "add tag" endpoint gave 15 × 400 "This category ID already exists" both before and after. That
is the intended answer for a duplicate ID. The fix makes sure the race also gets that 400 and not
a 500.

## Hazards found and fixes

File:line refers to c5a9659.

### A. Unique ids chosen by "check, then INSERT" → IntegrityError 500

| Where | What |
|---|---|
| `judge/utils/problem_create.py:35` `generate_problem_code` + `views/problem_create_v2.py:1311,1626,1678,1718,1764,1823` | The first free `p######` is suggested on the add page (or chosen at save). Everyone who opened the page in the same minute gets the same code, and the INSERT has no retry. This is the 73% failure. |
| `views/problem_create_v2.py:887` (tag from the editor), `:1925` (add-tag endpoint), `:1964` (add-group endpoint) | `exists()` then `create()` on the unique `ProblemType.name` / `ProblemGroup.name`. |
| `views/contests.py:1149` and `:414` | Assignment create already retried the key, but `full_clean()` checked uniqueness first, so a key taken a moment earlier gave a 400. Five retries, all aimed at the same "first free" key, ran out ("ID None"). |
| `forms.py:777,791` + `views/problem.py:2335`, `views/contests.py:1855` | The clone forms check the code or key, then the view INSERTs it with no retry. |
| `forms.py:596` + `views/organization.py:2113,2161` | Class names are unique site-wide and generated from year, session and instructor, so two sections created together collide. The ModelForm's own unique check produced "already exists" for a name the teacher never typed. |
| `views/problem_import.py:1852`, `views/mcq.py:414,496`, `views/fill_blank.py:154` | Same generated-code race (import had 3 deterministic retries). |

**Fix:** `judge/utils/unique_ids.save_with_unique_id`. The INSERT (or rename UPDATE) runs in a
savepoint. On a duplicate of *that* field (it checks; other integrity errors propagate) it takes a
new value and tries again, up to 10 times. Retries after the first spread over the first few free
ids (`next_prefixed_id(spread=…)`, `uniquify_class_name(skip=…)`), so a crowd does not keep fighting
over one slot. A value the teacher typed is never changed: `UniqueIdTaken` becomes the existing
"already exists, choose another" message (400 or a form error). It is used for problem create
(all six types, import, legacy MCQ and fill-blank editors, clone), assignment clone, and class
create and edit. Tag and group creation reuse the row that won, or take the next `-N` suffix.
Assignment create uses `full_clean(validate_unique=False)`, leaves uniqueness to the INSERT that
already retried, and now uses 10 spread retries.

### B. InnoDB deadlocks (500 "Deadlock found when trying to get lock")

* Two saves of one question: `_clear_type_children` (`problem_create_v2.py:534`) deletes and
  re-creates options, blanks and cases. Two such transactions interleave their gap locks.
  **Fix:** each edit save locks the problem row first (`SELECT … FOR UPDATE`), so saves of one
  question run one after another.
* Concurrent INSERTs of the same unique value keep a shared lock on the index record after their
  savepoint rolls back. Their next INSERTs into the same gap then deadlock. This was seen on class
  create at N = 24 even after fix A. **Fix:** `judge/utils/db_retry.retry_on_deadlock` re-runs the
  outermost write after a short random pause (only when no outer transaction is open). It wraps the
  question save, assignment create, edit and clone, question clone, class create and edit, grade
  save, Release All, import create and create-all, and the legacy MCQ and fill-blank editors.
* Grade save and Release All locked rows in opposite orders. Release All updated grades and then,
  through the ContestSubmission signal, the submission row. A save held its submission row and
  waited for the grade. **Fix:** both lock submission → grade, in primary-key order. Release All
  (and the practice Release All, `views/grade.py:192`) updates by primary key: an UPDATE whose WHERE
  joins `judge_submission` takes shared locks on those rows.

### C. Lost updates (two editors, or a background write of a stale row)

| Where | What | Fix |
|---|---|---|
| `views/problem_create_v2.py` edit save, `views/contests.py:1165` `_update` | Last write wins: the second tab or teacher silently overwrites the first. | **Edit-conflict check.** The edit page embeds a fingerprint of everything the editor manages (`judge/utils/edit_conflict.py`: the row minus volatile columns, M2M ids, child rows such as options, blanks, cases, configs, solution, translations, assignment problems). Save sends it as `edit_base`. The view locks the row, recomputes the fingerprint and answers **409** `{"conflict": true, "error": "Someone else (or another tab) saved this question after you opened it. Your changes were not saved. Copy anything you want to keep, reload…"}`. Both editors already show `error` in their toast and keep the form, so the teacher can copy their text. A successful save always navigates, so the next page load has a fresh base. Clients without `edit_base` (API, old pages) keep last-write-wins but still get the row lock. Solve counts, user count, finalize state and scheduled task ids are not in the fingerprint, so judging, joins and finalize never cause a false conflict. |
| `views/contests.py:1165` | `_update` saved the contest object loaded in `dispatch()`, writing back stale `results_finalized`, `user_count` and task ids. | It reloads the row under the lock before applying the payload. |
| `models/problem.py:700` `Problem.update_stats` | A full `save()` after every judged or graded submission, on an instance loaded earlier, overwrote a teacher's new name, statement or points. | `save(update_fields=['user_count', 'ac_rate'])`. |
| `models/contest.py:433` `update_user_count` (each Join) | A full save of the request's contest copy undid an assignment edit, for example an extended end time. | `update_fields=['user_count']`. |
| `views/contests.py:2060`, `models/profile.py:477` | Join and leave fully saved the profile. | `update_fields=['current_contest']`. |
| `models/contest.py:803` `recompute_results` | Concurrent recomputes of one student (grading, judge, release) could write a total computed before the other grade landed. The formats save the whole row (`contest_format/default.py:47`). | Lock the participation row for the recompute, and reload `harvest_scheduled_task_id` (set by the scheduler) before the full save. |
| `utils/contest_grade.py:391` and `:415` | Grade save: get-then-create of the one-to-one grade (500). It read `released` and the ranking score before Release All committed, and Release All could then write an older score over a newer save. | One transaction with locks (above). A lost create race updates the winner's row. The score and flag are read after the lock. |
| `views/problem_create_v2.py:1269` | A second tab opened with the same suggested code was answered "already saved", and its question was dropped. | **Create token.** The add page sends a random `create_token`. A repeat with the same token (double click, network retry) waits for the first save and answers `already_saved` (or 409 "still being saved"), so no duplicate is made. A different tab is a new question and gets the next code. Pages without a token keep the old behaviour. |
| `views/problem_import.py:834` and `:915,1053,1081` | Create on an import row: the row was not locked, and every draft save fully saved the row, writing back `created_problem=None`, so a double click created the question twice. | The row is locked and re-read before creating. Draft saves never write `created_problem`. |

### D. Partial writes

* Class create and edit (`views/organization.py:2107-2120`, `:2159-2170`) were not atomic. The
  class, its admins and the CSV roster are now one transaction.
* Material upload or new folder into a folder a co-teacher deleted a moment earlier
  (`views/organization.py:2280,2301`) failed on the foreign key. The folder row is now locked while
  adding to it. If it is gone, the teacher gets "This folder was deleted. Nothing was added."
* Question and assignment creates were already atomic. The tests check that a refused create keeps
  nothing.

### Reviewed and left as is

* Assignment drafts / autosave (`assignment_draft.py`): `get_or_create` / `update_or_create` on a
  `unique_together`. These are safe at READ COMMITTED.
* Assignment tag create (`get_or_create`), and the difficulty and import tags (`get_or_create`):
  also safe.
* Concurrent saves of one assignment's problem list: the contest UPDATE already serialized them.
  The burst failure seen at first came from a harness bug (duplicate ids in the payload).
* No cache keys shared between users and no per-request mutation of module globals were found in
  these paths. The create token key includes the user id.

## Tests

* New: `judge/views/tests/test_teacher_concurrency.py`, 37 tests, all passing. 25 of the
  behavioural ones fail against the c5a9659 call sites.
* Updated: `test_problem_import_creation.py`. The old test mocked the internals of the removed
  3-try loop. It now checks that import saves through `save_new_problem` and still reports
  exhausted retries.
* Run green with the code in its final state: test_problem_create_v2, test_contest_editor,
  test_contest_v2, test_problem_import(_creation), test_contest_grade(_fixes, _variants),
  test_grade_entry, test_mcq, test_fill_blank, test_problem_clone, test_unique_ids, models
  test_contest, test_problem, test_exam_contest, test_assignment_harvest, test_profile and
  test_submission, test_organization_courses, test_click_through_fixes, test_authoring_security,
  test_full_crawl_fixes, test_hidden_results, test_p1_gap_fixes, test_p1_verification,
  test_problem_workspace, test_subjects. That is 1,019 tests, OK (1 skipped). The full suite was
  not run, as the shared-box rules require.
* `flake8 judge/ scripts/concurrency/`: clean.

## What is left / uncertain

* **Other editors stay last-write-wins.** The conflict check covers the two editors that send
  `edit_base` (problem editor `create_v2` and the assignment editor). The Django admin, the legacy
  MCQ and fill-blank pages, the test-data page, course edit and class edit still overwrite. Their
  saves are now serialized and never stale-write background columns, but a teacher can still undo
  another's edit there.
* The contest formats still save the whole participation row. Only the scheduler's column is
  reloaded first. A disqualify racing a recompute could still lose `is_disqualified`.
* The create token relies on the shared cache (Redis in production). With a per-process cache a
  repeated save landing on another worker would not be recognized.
* `retry_on_deadlock` re-runs the whole save. File side effects (test-data files written before a
  rolled-back deadlock) are overwritten by the retry, not cleaned up in between.
* Class create imports the roster CSV inside the transaction. A very large CSV holds its locks for
  that long.
* Class names stay unique site-wide (existing model rule): two courses cannot both have a typed
  "Section 1". MariaDB ignores the conditional `unique_active_name` constraint (system check
  W036), so the plain `unique=True` on `name` is what applies.
* Measured on MariaDB 10.11 at READ COMMITTED (Django's default). MySQL 8 should behave the same,
  but this was not tested.

## Re-run

```bash
# dev DB seeded (AGENT_SETUP.md), then:
python manage.py shell -c "exec(open('scripts/concurrency/make_teachers.py').read())"
gunicorn dmoj.wsgi --bind 127.0.0.1:8011 --worker-class gthread --workers 3 --threads 4   # DJANGO_SETTINGS_MODULE=dmoj.settings
cd scripts/concurrency
RACE_BASE=http://127.0.0.1:8011 RACE_DB=classroom_t N=16 python race.py          # all bursts
RACE_BASE=http://127.0.0.1:8011 SECONDS_=90 USERS=30 python soak.py              # mixed load
bash scripts/dev-test.sh judge.views.tests.test_teacher_concurrency
```
