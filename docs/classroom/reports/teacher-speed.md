# Teacher pages at university scale (agent P)

Branch `p-teacher-speed` (from `fairplay-classroom` @ c5a9659). Date 2026-10-20.

**Short answer.** At university scale every page an instructor opened cost **2.3–5.8 s**. Almost all
of that was one query in the navigation bar, a 2.4 s `user_course_subject_keys` lookup that ran on
every page for every course member (students too). The other problems grew with the data: the
assignment editor carried the whole question bank (11.8 MB for an administrator), the assignment
list carried every assignment (4.7 MB), lists ran one or two queries per row, and three filters
made MariaDB walk all 150,000 submissions. After the fixes, every measured instructor page takes
**35–260 ms** in-process, and page size no longer grows with the number of problems, courses,
classes or assignments. No grading, visibility or hidden-result rule changed. No migration was
needed: the slow plans came from query shapes, not from missing indexes (see "Indexes").

## How it was measured

* **Seed:** `classroom/scripts/loadtest/seed_teachers.py [--reset]` runs in about 2 minutes with
  bulk inserts. It creates 500 instructors (`tprof001`…, Instructors group, staff like approved
  site teachers), 3,000 students, 150 courses with 2 classes each, 6,000 problems of all five
  types (configs, options, blanks; 60 tags plus difficulty; a third of them public), 1,500
  assignments (homework, quiz and exam tags; past, running and upcoming; finalized exams),
  42,800 participations, about 150,000 submissions with sources, contest submissions and manual
  grades, 11,000 assignment drafts and 100 Excel-import drafts. The password is `loadtest-pw`. It
  refuses a database whose name does not look like a test database.
* **Measurement:** `classroom/scripts/loadtest/teacher_query_counts.py` uses the Django test client
  in-process with `CLASSROOM_DEBUG=0`. It warms every URL once and then reports queries, wall
  time, SQL time and response KB for 66 pages and APIs, as `tprof001` (a typical instructor with
  two courses) and as `admin`. `--verbose` prints repeated queries (N+1) and slow SQL,
  `--explain` prints large table scans, and `--json`/`--compare` produce the table below.
* **Box:** a 2-vCPU container shared with other agents. Times are single runs after a warm-up and
  vary by about ±30 %; query counts and KB are exact.
* **Browser checks** (Playwright, dev server on :8012) of every changed screen: the library
  (paging, picks across pages, filters, sort, add, save, reopen), the course/class pickers
  (search, cascade, save, reopen), the apply-as-instructor modal, the paged assignment list
  (filter reload, page links keep filters, clear), and the problem editor's per-course type check.

## Before / after (seeded scale)

The full table for all 66 pages and both users is in the appendix. Highlights:

| Page | Instructor before → after | Admin before → after |
|---|---|---|
| Home | 21 q, 5,808 ms, 20 KB → 21 q, 93 ms | 20 q, 3,013 ms, **234 KB → 27 KB**, 50 ms |
| `/problems/` (any sort/filter) | 31–40 q, 2.7–3.7 s → 20–21 q, 84–143 ms | unchanged (~100 ms) |
| `/contests/` | 52 q, 3,760 ms → 34 q, 116 ms | 29 q, 2,467 ms, **4,718 KB → 42 q, 351 ms, 680 KB** |
| Course page / class page | 2.5 s / 3.3 s, 61 / 80 KB → 53 / 70 ms, **31 / 50 KB** | 61 / 80 KB → 31 / 50 KB |
| Assignment create | 2,668 ms, **424 KB → 36 ms, 202 KB** | 3,609 ms, **11,823 KB → 32 ms, 208 KB** |
| Assignment edit | 2,923 ms, 442 KB → 91 ms, 221 KB | 3,850 ms, 11,846 KB → 62 ms, 232 KB |
| Problem create / edit (all types) | 2.4–3.4 s → 35–72 ms | 228–230 KB → 191–192 KB |
| Submissions per problem / per assignment | **122 / 120 q**, 2.8 s → 26 q, 115–122 ms | 71 / 65 q → 21 q |
| Submissions of a student | 64 q → 29 q | 91 q → 24 q |
| Grade page (exam) | 72 q, 2,930 ms → 34 q, 261 ms | 67 q → 29 q |
| Practice grade desk | 3,064 ms → 81 ms | 39 q, 833 ms → 17 q, 241 ms |
| Leaderboard `/users/` | **211 q → 11 q** | 208 q → 8 q |
| Admin submission changelist (teacher, staff) | 1,209 ms (SQL 1,100 ms) → 119 ms | 126 q → 10 q |
| Library / pickers (new JSON endpoints) | 9–12 queries, 3.7 KB per page of 10 | same |

"Before" for the teacher's admin changelists: the first seed did not make instructors staff, so
the baseline shows 302 redirects. The 1,209 ms comes from the run after the seed fix and before
the changelist fix. A teacher's assignment pages are 10 KB larger after the fix only because staff
users see the admin links.

## What was slow and what changed

Commits on `p-teacher-speed`:

1. **e4ab231 Course-role lookups as subqueries; seed and measuring script.**
   `Q(member=p) | Q(admins=p) | Q(instructors=p) | Q(class__admins=p) | Q(class__members=p)` makes
   Django LEFT JOIN every role table at once. MariaDB builds the product of each course's members,
   admins and classes before it applies the OR. That took **2.4 s** in
   `judge/subjects/access.user_course_subject_keys`, which the navigation bar calls on every page.
   The new `judge/utils/course_roles.py` (`courses_q`, `teaching_q`) asks the same question with one
   indexed `IN (SELECT …)` per role, about 1 ms. The same change went into `teaching_courses`,
   `assignable_organizations` and `orgs_reviewable_by`. `Contest.get_visible_contests` (3+ s, same
   join pattern over authors, curators, testers, spectators, private contestants, scoreboard
   viewers, courses and classes) now uses one `EXISTS` per rule, the way
   `Problem.get_visible_problems` already did. **Same answers:** a 12-user × 11-assignment matrix
   test passes before and after, and the visible ids of 14 sampled seed users are identical.
2. **2813bbe Assignment editor library a page at a time.** The editor rendered every problem the
   teacher may attach and filtered them in the browser. The new `/contests/library/`
   (`ContestProblemLibraryView`) serves the same set (`problems_attachable_by`, rules unchanged)
   with the same filters (search, type incl. single/multiple/true-false, difficulty with the
   easiest-tag rule of `Problem.difficulty`, course, author for allowed viewers, tags, solved,
   AC range), sorts and page size. Picks survive paging. One visible change: difficulty sorts
   easy < medium < hard < none instead of alphabetically.
3. **ebebd88 Per-row queries and data-sized plans.**
   * `Contest.editor_ids` uses prefetched authors and curators, so per-row `is_editable_by` stops
     querying (submission lists and assignment list). `ai_points` is no longer deferred in
     `submission_related`. Question names are translated in one query on submission lists, the
     grade page and contest stats.
   * `?contest=` / `?problem=` / `?user=` on submission lists resolve to ids first. The list is a
     forced STRAIGHT_JOIN from `judge_submission`, so filtering on a joined key walked every
     submission by id. Assignment list SQL went from 232 ms to 29 ms.
   * Practice grade: the editable problems were a subquery, and MariaDB walked the date index over
     all submissions. They are now a literal id list (`_practice_problem_q`, same rule as
     `get_editable_problems`), and the window is picked by id before the wide join. Grade rows
     also select the uploaded file (`upload`).
   * Problem list: `Problem.editable_ids_among` answers `is_editable_by` for the page in one query.
   * Leaderboard: `first_name`/`last_name` are no longer deferred.
   * Course, class and course-list pages carried the apply-as-instructor catalog of every course
     and class (30 KB of a 60 KB page at 150 courses). The modal now fetches it from
     `/organizations/instructor-apply-catalog/` when it opens.
   * Admin submission changelist: `locked_after` is no longer deferred.
     `ClassAdmin.has_add_permission` is answered once per request.
4. **e5671d5 Assignment list paged on the server when large.** Lists with more than
   `CLASSROOM_CONTEST_LIST_PAGE_SIZE` (50) past assignments page the past section with the existing
   pagination markup. They apply the list's filters in SQL (course, class, every tag, author,
   search terms, status), show at most one page of ongoing and upcoming assignments with a note
   counting the rest, and reload when a filter changes. A teacher's own list, under the limit,
   still renders in full and filters instantly in the browser.
5. **25fe8f6 Home sidebar, teacher admin changelist, series ids.** The home sidebar listed every
   ongoing and upcoming assignment (456 for the administrator). It now shows the first
   `CLASSROOM_HOME_CONTEST_LIMIT` (10) of each with a link to the list. A teacher's admin
   submission changelist joined both role tables under the STRAIGHT_JOIN with DISTINCT (1.2 s); it
   now uses a literal list of their problem ids, and the rejudge action uses the same list. Next
   problem code and assignment id load only the codes and keys of their series prefix.
6. **0f4e88b Problem editor capabilities per distinct list.** `ORG_QUESTION_CAPS` repeated the
   full capability list for every offered course (40 KB for an administrator). Each distinct list
   is now sent once, and the page expands it.
7. **b0ba400 Course and class pickers search.** The assignment editor rendered every course and
   class into four selects (course, class, join organizations, library course filter): about
   70 KB at 150 courses, growing with every term. They now render only the stored choices and
   search the same sets through `/contests/editor-options/<courses|classes>/`. Removing a course
   still drops its classes, and the class search is empty without a course, as before.
8. **836a314 Visibility worked out once per list request.** The visibility rule is a set of per-row
   EXISTS checks over all assignments. The list, its counts and the four filter-option queries
   each evaluated it again (7 times per page). Teachers' visible ids are now computed once (SQL
   137 → 31 ms).

Permission and hidden-result rules: no change to `results_hidden_q`, `visible_results`,
`results_visible_to`, `get_visible_problems`, `is_editable_by` or the editor's attach check. The
new endpoints use the editor's own access rule (`contest_editor_allowed`) or login, and return only
sets the page already showed. `judge/views/submission.py:_get_result_data` was not touched. That
file was changed elsewhere (`submission_related` gains `ai_points`, display-name priming, id-based
filters), so expect a merge there.

## Indexes

`--explain` on every page found no filter or sort that lacks an index. The large scans were caused
by query shape, and are fixed above: joins of role tables, joined-key filters under STRAIGHT_JOIN,
and subquery-driven date scans. The remaining scans are of `judge_problem` (6,000 rows, about
10–25 ms) in the problem list's visibility, count and points queries, and of `judge_contest_classes`
(2,100 rows) inside contest visibility. Both grow with the size of the bank, not with submissions.
**No migration was added.**

## Tests

New module `judge/views/tests/test_teacher_speed.py`, 30 tests. Each fails on the code before its
fix (checked by checking the old sources out over the tree), except the "answers unchanged" ones
(visibility matrix, practice-grade scope, stored editor choices), which pass both before and after:

* SQL shape: no role-table joins in subject keys, teaching courses or visible contests. The
  matrix test covers member, class member, private contestant, course admin, instructor, class
  admin, author, curator, tester, spectator, scoreboard viewer and outsider.
* Editor: page size and queries flat with +30 problems and +30 courses/classes; library filters,
  sorts, paging, permission and attachable set; picker options and access; stored choices
  rendered.
* Query counts flat for 3 versus 15 rows: assignment submissions, all submissions, problem list,
  assignment list, leaderboard and contest stats.
* Practice-grade scope and literal ids; course page flat with +25 courses and the catalog
  endpoint; assignment list paging, server filters, small lists in full, visibility run once;
  home sidebar cap; teacher admin changelist rows and shape; problem editor caps grouping.

Existing tests changed to follow the new mechanism without dropping what they check: five in
`test_contest_editor` (library rows are checked through the JSON endpoint), one in `test_subjects`
(caps format), and two mock tests in `test_contest` / `test_problem_import_creation` (the series
query is now prefix-filtered).

Runs, all OK: 971 tests in 28 modules (teacher speed, contest editor, contest v2, submission list,
problem list, the four grade modules, hidden results, load performance, organization courses,
subjects ×2, problem create v2, problem import creation, models contest/problem, contest security,
teacher actions, P1 ×2, full crawl, click-through, workspace, select2, authoring security), plus
80 in `test_exam_contest`, `test_submission`, `test_teacher_finalize`, `test_file_upload` and
`test_api_v2_visibility`. flake8 is clean on every changed file.

## What is left

* **The admin assignment list is still 680 KB.** That is 150 rows at about 4 KB of markup each,
  plus 150 courses and 300 classes as filter checkboxes. Smaller rows, or a lower page size for
  administrators, would help; the filter options could become a search.
* **"All submissions" for a teacher:** the visible-problem STRAIGHT_JOIN scans newest-first until
  it fills a page (count up to 101 rows, about 100 ms at 150k submissions). The cost grows with
  the share of submissions the teacher cannot see. Owner of `_get_result_data` too.
* **Problem list:** `get_visible_problems_filter` (LEFT JOINs plus DISTINCT) scans the whole bank
  three or four times per page, about 25 ms each at 6,000 problems. Worth the same EXISTS rewrite
  if the bank grows tenfold. `Problem.get_editable_problems` still OR-joins authors and curators
  (11 ms admin problem changelist).
* **Per-assignment pages** (grade, analytics) scale with the size of the assignment, as measured
  in the earlier load test, not with the site. The exam grade page inlines every submission.
* **Course page:** 39 constant queries, with pending-request checks computed three times.
* The assignment calendar renders all of a month's visible assignments (213 KB for the
  administrator).
* Times here come from a shared 2-vCPU box with an untuned MariaDB (128 MB buffer pool). Re-measure
  on production hardware with `seed_teachers.py` + `teacher_query_counts.py`.

## Appendix: all pages

Queries, ms and KB per page, before → after, as `tprof001` (instructor) and `admin`.

| who | page | queries before → after | ms before → after | KB before → after |
|---|---|---|---|---|
| tprof001 | home | 21 → 21 | 5808 → 93 | 20 → 20 |
| tprof001 | problems | 39 → 20 | 2781 → 119 | 111 → 111 |
| tprof001 | problems page 5 | 20 → 20 | 2838 → 111 | 68 → 68 |
| tprof001 | problems search | 35 → 20 | 3734 → 132 | 103 → 103 |
| tprof001 | problems sort ac_rate | 39 → 20 | 3125 → 142 | 113 → 113 |
| tprof001 | problems sort date | 39 → 20 | 2707 → 143 | 111 → 111 |
| tprof001 | problems filter course | 39 → 20 | 3647 → 84 | 111 → 111 |
| tprof001 | problems filter format MCQ | 39 → 20 | 3438 → 120 | 109 → 109 |
| tprof001 | problems filter type | 40 → 21 | 2843 → 106 | 111 → 111 |
| tprof001 | problems filter author | 31 → 21 | 3010 → 89 | 97 → 97 |
| tprof001 | contests | 52 → 34 | 3760 → 116 | 97 → 98 |
| tprof001 | contests search | 52 → 34 | 4035 → 134 | 97 → 98 |
| tprof001 | contests calendar | 11 → 11 | 2972 → 46 | 18 → 18 |
| tprof001 | course page | 41 → 39 | 2461 → 53 | 61 → 31 |
| tprof001 | course users | 13 → 13 | 2819 → 41 | 54 → 54 |
| tprof001 | class page | 37 → 35 | 3316 → 70 | 80 → 50 |
| tprof001 | assignment create | 18 → 11 | 2668 → 36 | 424 → 202 |
| tprof001 | assignment edit | 39 → 33 | 2923 → 91 | 442 → 221 |
| tprof001 | assignment edit (running exam) | 39 → 33 | 3078 → 94 | 443 → 222 |
| tprof001 | check key | 4 → 4 | 7 → 7 | 0 → 0 |
| tprof001 | select2 profile | 7 → 5 | 35 → 39 | 1 → 1 |
| tprof001 | select2 organization | 7 → 7 | 10 → 8 | 0 → 0 |
| tprof001 | select2 class | 5 → 5 | 9 → 9 | 0 → 0 |
| tprof001 | select2 problem | 7 → 7 | 18 → 18 | 1 → 1 |
| tprof001 | select2 contest | 7 → 7 | 241 → 36 | 1 → 1 |
| tprof001 | select2 author filter | 7 → 7 | 39 → 33 | 1 → 1 |
| tprof001 | preview contest | 3 → 3 | 15 → 7 | 0 → 0 |
| tprof001 | preview problem | 3 → 3 | 6 → 6 | 0 → 0 |
| tprof001 | problem create | 20 → 20 | 3350 → 35 | 182 → 182 |
| tprof001 | problem edit C | 43 → 43 | 2844 → 72 | 184 → 184 |
| tprof001 | problem edit M | 40 → 40 | 2581 → 70 | 184 → 184 |
| tprof001 | problem edit B | 40 → 40 | 2360 → 71 | 183 → 184 |
| tprof001 | problem edit S | 39 → 39 | 2501 → 62 | 183 → 183 |
| tprof001 | problem edit F (no own F problem: 404) | 23 → 23 | 2477 → 35 | 9 → 9 |
| tprof001 | problem detail | 46 → 45 | 2241 → 134 | 116 → 116 |
| tprof001 | problem detail MCQ | 42 → 41 | 3804 → 130 | 113 → 113 |
| tprof001 | submissions all | 16 → 16 | 2812 → 254 | 62 → 63 |
| tprof001 | submissions all stats | 3 → 3 | 5 → 6 | 0 → 0 |
| tprof001 | submissions mine | 14 → 14 | 2463 → 46 | 14 → 14 |
| tprof001 | submissions of student | 64 → 29 | 2370 → 89 | 35 → 35 |
| tprof001 | submissions problem | 122 → 26 | 2790 → 115 | 97 → 97 |
| tprof001 | submissions problem stats | 10 → 10 | 19 → 20 | 0 → 0 |
| tprof001 | submissions assignment | 120 → 26 | 2829 → 122 | 97 → 97 |
| tprof001 | submissions assignment stats | 7 → 8 | 23 → 22 | 0 → 0 |
| tprof001 | contest submissions user | 34 → 30 | 2765 → 71 | 20 → 20 |
| tprof001 | assignment page | 30 → 29 | 2808 → 81 | 49 → 59 |
| tprof001 | assignment page (running) | 30 → 29 | 2801 → 75 | 49 → 59 |
| tprof001 | grade page | 41 → 36 | 3006 → 121 | 153 → 153 |
| tprof001 | grade page exam | 72 → 34 | 2930 → 261 | 213 → 214 |
| tprof001 | grade status (no submission id: 400) | 7 → 7 | 11 → 10 | 0 → 0 |
| tprof001 | practice grade | 19 → 21 | 3064 → 81 | 109 → 109 |
| tprof001 | analytics | 19 → 19 | 2846 → 53 | 264 → 264 |
| tprof001 | ranking | 24 → 24 | 2658 → 67 | 52 → 52 |
| tprof001 | ranking (running) | 24 → 24 | 2523 → 68 | 49 → 49 |
| tprof001 | participations | 21 → 21 | 2768 → 44 | 35 → 35 |
| tprof001 | contest stats | 31 → 26 | 2925 → 57 | 31 → 31 |
| tprof001 | user profile | 18 → 18 | 2734 → 43 | 14 → 14 |
| tprof001 | student profile | 18 → 18 | 2926 → 36 | 14 → 14 |
| tprof001 | user list | 211 → 11 | 2993 → 39 | 46 → 46 |
| tprof001 | admin problems (before: not staff, 302) | 3 → 18 | 5 → 146 | 0 → 55 |
| tprof001 | admin contests | 3 → 13 | 5 → 129 | 0 → 29 |
| tprof001 | admin submissions | 3 → 13 | 4 → 119 | 0 → 90 |
| tprof001 | admin organizations | 3 → 12 | 4 → 65 | 0 → 14 |
| tprof001 | admin problem change | 3 → 31 | 4 → 126 | 0 → 74 |
| tprof001 | admin contest change | 3 → 38 | 4 → 138 | 0 → 69 |
| admin | home | 20 → 20 | 3013 → 50 | 234 → 27 |
| admin | problems | 16 → 16 | 108 → 99 | 139 → 139 |
| admin | problems page 5 | 16 → 16 | 76 → 87 | 140 → 140 |
| admin | problems search | 16 → 16 | 134 → 103 | 141 → 141 |
| admin | problems sort ac_rate | 16 → 16 | 95 → 99 | 140 → 140 |
| admin | problems sort date | 16 → 16 | 95 → 121 | 140 → 140 |
| admin | problems filter course | 16 → 16 | 64 → 76 | 140 → 140 |
| admin | problems filter format MCQ | 16 → 16 | 79 → 96 | 140 → 140 |
| admin | problems filter type | 17 → 17 | 114 → 104 | 140 → 140 |
| admin | problems filter author | 17 → 17 | 66 → 75 | 112 → 112 |
| admin | contests | 29 → 42 | 2467 → 351 | 4718 → 680 |
| admin | contests search | 29 → 33 | 2410 → 242 | 4718 → 516 |
| admin | contests calendar | 8 → 8 | 52 → 69 | 213 → 213 |
| admin | course page | 26 → 24 | 66 → 34 | 61 → 31 |
| admin | course users | 9 → 9 | 30 → 30 | 55 → 55 |
| admin | class page | 27 → 25 | 63 → 67 | 80 → 50 |
| admin | assignment create | 15 → 8 | 3609 → 32 | 11823 → 208 |
| admin | assignment edit | 32 → 26 | 3850 → 62 | 11846 → 232 |
| admin | assignment edit (running exam) | 32 → 26 | 3560 → 52 | 11846 → 233 |
| admin | check key | 4 → 4 | 7 → 7 | 0 → 0 |
| admin | select2 profile | 5 → 5 | 34 → 30 | 1 → 1 |
| admin | select2 organization | 5 → 5 | 9 → 7 | 1 → 1 |
| admin | select2 class | 3 → 3 | 7 → 5 | 0 → 0 |
| admin | select2 problem | 5 → 5 | 9 → 9 | 1 → 1 |
| admin | select2 contest | 5 → 5 | 14 → 14 | 1 → 1 |
| admin | select2 author filter | 5 → 5 | 30 → 37 | 1 → 1 |
| admin | preview contest | 3 → 3 | 6 → 6 | 0 → 0 |
| admin | preview problem | 3 → 3 | 6 → 7 | 0 → 0 |
| admin | problem create | 17 → 17 | 39 → 44 | 228 → 191 |
| admin | problem edit C | 37 → 37 | 56 → 62 | 230 → 192 |
| admin | problem edit M | 34 → 34 | 49 → 46 | 230 → 192 |
| admin | problem edit B | 34 → 34 | 55 → 65 | 230 → 192 |
| admin | problem edit S | 33 → 33 | 59 → 71 | 230 → 192 |
| admin | problem edit F | 33 → 33 | 61 → 60 | 230 → 192 |
| admin | problem detail | 39 → 39 | 181 → 354 | 298 → 298 |
| admin | problem detail MCQ | 35 → 35 | 160 → 154 | 295 → 295 |
| admin | submissions all | 13 → 13 | 74 → 52 | 113 → 113 |
| admin | submissions all stats | 3 → 3 | 7 → 6 | 0 → 0 |
| admin | submissions mine | 10 → 10 | 30 → 30 | 15 → 15 |
| admin | submissions of student | 91 → 24 | 127 → 75 | 104 → 104 |
| admin | submissions problem | 71 → 21 | 111 → 91 | 117 → 117 |
| admin | submissions problem stats | 6 → 6 | 8 → 11 | 0 → 0 |
| admin | submissions assignment | 65 → 21 | 217 → 71 | 123 → 123 |
| admin | submissions assignment stats | 4 → 5 | 6 → 8 | 0 → 0 |
| admin | contest submissions user | 27 → 24 | 58 → 58 | 24 → 24 |
| admin | assignment page | 24 → 24 | 46 → 60 | 59 → 59 |
| admin | assignment page (running) | 25 → 25 | 51 → 52 | 59 → 59 |
| admin | grade page | 36 → 31 | 103 → 91 | 153 → 153 |
| admin | grade page exam | 67 → 29 | 281 → 129 | 214 → 214 |
| admin | grade status | 4 → 4 | 9 → 5 | 0 → 0 |
| admin | practice grade | 39 → 17 | 833 → 241 | 382 → 382 |
| admin | analytics | 15 → 15 | 81 → 48 | 264 → 264 |
| admin | ranking | 20 → 20 | 62 → 63 | 52 → 52 |
| admin | ranking (running) | 21 → 21 | 99 → 68 | 50 → 50 |
| admin | participations | 17 → 17 | 37 → 39 | 36 → 36 |
| admin | contest stats | 26 → 21 | 55 → 37 | 31 → 31 |
| admin | user profile | 15 → 15 | 29 → 25 | 14 → 14 |
| admin | student profile | 15 → 15 | 39 → 27 | 15 → 15 |
| admin | user list | 208 → 8 | 200 → 34 | 46 → 46 |
| admin | admin problems | 32 → 16 | 254 → 221 | 107 → 107 |
| admin | admin contests | 27 → 11 | 226 → 220 | 90 → 90 |
| admin | admin submissions | 126 → 10 | 342 → 222 | 98 → 98 |
| admin | admin organizations | 26 → 10 | 158 → 149 | 73 → 73 |
| admin | admin problem change | 55 → 38 | 227 → 181 | 130 → 130 |
| admin | admin contest change | 96 → 79 | 308 → 309 | 126 → 126 |

The admin column was never slow on the navigation bar, because superusers see every subject; its
small time differences on unchanged pages are noise. The admin `/contests/` query count rose (29 → 42)
because of the per-section counts of the paged list. The count is constant and the page is 7×
smaller.
