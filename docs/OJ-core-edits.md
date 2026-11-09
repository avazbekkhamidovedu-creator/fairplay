# DKU OJ core edits for FairPlay (branch feat/design-b)

Non-fairplay-named files still modified vs HEAD after cleanup. "Proof" = tests that fail when the hunk is reverted
(run 2026-11-09, tsettings, 579 FairPlay-area tests; 5 concurrency tests under tsettings_file). "untested" = no
Python test covers it; the reason is from reading the code.
Backup of the full WIP before cleanup: docs/oj-wip-backup-20261109-1529.patch (+ -untracked.tgz).

| File | Hunk | Verdict | FairPlay behaviour that needs it | Action |
|---|---|---|---|---|
| dmoj/settings.py | middleware entry NativeBrowserAdmissionMiddleware | A | plain browser cannot enter protected pages without the app; reverting fails 19 native_session tests | keep (1 line) |
| dmoj/settings.py | FAIRPLAY_OJ_PUBLIC_URL, CLASS_LOGIN_ENABLED, INSTALLATION_ID | A | defaults for new FairPlay settings read by fairplay_* modules (app sign-in done URL, installation API) | keep (3 settings) |
| dmoj/urls.py | 7 api/fairplay/browser-context + native/* routes, 2 fairplay-launch routes, installation route, imports | A | app claim/renew/finish/abort/recover, launch page for hidden exams, installation id | keep |
| dmoj/urls.py | wrapped long `from judge.views import (...)` | B | formatting only | reverted |
| judge/apps.py | import fairplay_lifecycle | A | registers lifecycle signal handlers (exam end / hand-in) | keep (1 line) |
| judge/models/__init__.py | export FairPlayDeviceSession/Slot/Audit | A | models are imported via the package; needs migrations 0220-0222 (owner decides) | keep |
| judge/models/profile.py | update_contest keeps pointer for active device session | A | without it a plain browser request erases the native participation (2 native_session tests fail) | keep; owner approve |
| judge/utils/assignment_harvest.py | judge_on_commit kwarg | A | native hand-in runs inside the admission transaction; judge must not see uncommitted rows (test_assignment_harvest) | keep |
| judge/models/tests/test_assignment_harvest.py | 2 tests for judge_on_commit | A | regression tests for the line above | keep |
| judge/views/contests.py | ContestListMixin Cache-Control no-store | A | roster-gated discovery rows must not be cached (2 discovery tests fail) | keep |
| judge/views/contests.py | list/list-v2 discovery_context, rows merge, total_count, filter options | A | enrolled students see the hidden FairPlay exam with a Start exam action | keep |
| judge/views/contests.py | hidden_exam_launch in ContestDetail.get, ContestJoin get/post | A | blocks plain-browser entry; redirects to app launch | keep |
| judge/views/contests.py | ContestLeave: finish_from_website (falls through to the stock Leave when harvest is off or drafts are pending) + browser completion record, recorded only when the contest is a FairPlay exam | A | native hand-in uses the existing Leave button; unlock confirmation (2 tests); ordinary leave writes no session key | keep |
| judge/views/contests.py | ranking: fairplay_modes + native flag | A | instructor sees mode/device per student | keep |
| judge/views/contests.py | exclude fp-aud-* tags (create, prefill, tag list, clone, tag create, tag ajax) | A (untested) | system audience tags must not show or be editable in the plain tag UI | keep; owner approve |
| judge/views/api/api_v2.py | exclude fp-aud-* from API contest tags | A (untested) | audience tag would leak in public contest API | keep (1 line) |
| judge/views/assignment_draft.py | fairplay_hidden_for guard | A | blocks draft writes from a plain browser after native join (device_session test fails) | keep (3 lines) |
| judge/views/problem.py | workspace_native_admitted context | A (flag test) | workspace.js disables double submit / keeps button state in the app | keep | (now also set on the normal workspace path, not only the editor preview)
| judge/views/problem.py | judge_on_commit in _create_submission | A (untested) | submission made inside the admission transaction must be judged after commit | keep |
| judge/admin/contest.py | clean_name rejects fp-aud- tags | B | staff-only hardening; tests pass without it; tag-create view already blocks it | reverted (owner may re-add) |
| judge/template_context.py | sqlite branch in __nav_tab | B | test-environment shim, prod is MySQL; tests pass without it | reverted |
| templates/problem/list.html | role=table, CSS display:block | B | accessibility tweak, not needed for any FairPlay rule | reverted |
| templates/contest/list-v2.html | launch link, FairPlay badge, tag hiding, empty-state | A | discovery row with Start exam | keep |
| templates/contest/list-v2.html | indentation of `{% if group == 'course' %}` | B | whitespace | reverted |
| templates/contest/list.html | include fairplay_discovery.html | A | legacy list shows discovery | keep (1 line) |
| templates/contest/ranking.html | toolbar: hide access-codes PDF in native mode, include modes summary | A | native mode has no access codes (pages test fp-tools); CSS restyle is bundled in the same hunk | keep; owner may ask to trim CSS |
| templates/contest/ranking-table.html | FairPlay mode column | A | instructor view of mode/device | keep |
| templates/submission/row.html | " · latest" on hand-in note | A | tested in test_fairplay_revision | keep |
| templates/problem/workspace.html | data-native-admitted / data-native-user | A | feeds workspace.js | keep (2 lines) |
| resources/workspace.js | fairplay-restore-draft listener, nativeAdmitted submit lock | A (untested) | native crash recovery of answers; no double submit in app | keep |
| resources/workspace.js + templates/contest/media-js.html | serialized server-draft writes, beacon no longer counts as saved, Leave waits at most 5 s for the last save, then always proceeds | A (Python flag test only, no JS test) | gated on data-native-admitted (judge/views/problem.py workspace_native_admitted, set from fairplay_native_scope); ordinary assignments run the HEAD code path unchanged (`if (nativeAdmitted) {new} else {HEAD}`, media-js uses window.dkuojFlushBlocksLeave) | keep; Leave is never blocked (5 s flush timeout, then stock Leave) |
| judge/native_browser_middleware.py (untracked) | whole file | A | the admission middleware above (device scope now applies to every route for a verified device with a session row; writes need an active session); FairPlay-only, off unless FAIRPLAY_NATIVE_BROWSER_ENABLED | keep |
| deploy/native-browser/, tools/native_browser_local_gateway.py (untracked) | nginx example, local gateway test fixture | B for OJ core | not OJ code; deploy example and test fixture ("never deploy") | owner decides where it lives |
| judge/migrations/0220-0222 (untracked) | not touched | OWNER DECIDES | 0222 adds a column to contestparticipation (core table) | untouched |

Counts (table rows): A = 25 (7 untested by Python tests), B = 5 reverted (urls import wrap, admin clean_name, nav_tab sqlite, problem/list.html, list-v2 whitespace), 3 owner decisions.
