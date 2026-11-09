# Editor-side reopen / extension of a FairPlay exam (DKU OJ, Design B), 2026-11-09

Deadline source for the apps: `personal_deadline` (judge/utils/fairplay_device_session.py:142) = live `participation.end_time` (judge/models/contest.py:940), signed as `deadline` at judge/views/fairplay_native_session.py:186.

| scenario | works? | evidence | gap | smallest fix (FairPlay modules only) |
|---|---|---|---|---|
| S1 end later, mid-exam | yes | renew re-reads participation every lease (90 s); Mac DeadlineFailsafe.swift:27-43 and Windows ExamDeadlineFailsafe.cs:39-58 replace fireAt on each verified context; harvest tasks refuse before the deadline | timed exam: a student past real_start+time_limit gets nothing from a later end_time (core rule) | tell instructors to use "Give extra time" for timed exams |
| S1 end earlier / limit cut | yes | same path; next renew -> close_after_deadline -> harvest | none | - |
| S2 reopen after deadline, session still `finishing` (about 1-3 min) | yes | close_after_deadline undo (fairplay_native_session.py:343-352) | claim (not recover) with a finishing slot gives `other_device` | in claim_device: if the slot session is finishing and personal_deadline > now, undo the close first |
| S2 reopen after deadline, session `completed` / auto-released | NO | confirm_completed sets completed; claim needs a `reopen` audit row or refuses `handed_in` (fairplay_device_session.py:260-264) | editor-only reopen cannot bring students back; each needs help-page Reopen. Not covered by a test | claim_device: treat a deadline-closed session as reopenable when participation.end_time > now, not disqualified, and no explicit hand-in |
| S2 explicit hand-in | yes (stays handed in) | hand-in-started blocks the undo; tests pass | none | keep excluded |
| S2 timed participation | partly | end = min(real_start+limit, end_time); help Reopen shifts real_start, editor does not | editor reopen gives timed students past their limit 0 minutes | point to help-page Reopen / Give extra time |
| S2 FairPlay server copy of the window | yes | sync ping after commit, loop every 30 s; lock deadline is OJ-signed only | stale closes_at affects only delivery/rescue/access codes for up to 30 s | - |
| S3 start later mid-exam | partly | admitted_session filters start_time <= now (fairplay_device_session.py:295) | locked student refused on every page until the new start | drop the start_time filter when the session already has a participation |
| S3 time limit change | yes | live recompute, harvest rescheduled | - | - |
| S3 hidden mid-exam | partly | claim -> not-visible 404; renew continues | locked student sees 404s until deadline + grace | sign `hold` instead (low priority) |
| S3 tag off/on | yes | editor refuses lock removal from start-24h to end+14d | Django admin can still remove the tag | refuse removal in the admin form while edit-locked |
| S4 save hooks | yes | post_save -> on_commit daemon thread, short timeouts, errors only logged; tests pass | opening the editor of a FairPlay exam can wait up to about 6 s if FairPlay is unreachable | short cache of open_exam_values failures |
| S5 divergences | - | help Reopen writes reopen + reopen-window and shifts real_start; editor writes nothing | main harm: students locked out after an editor-only reopen (S2) | fix the S2 row |

Tests run in the audit: 6 native-session tests plus test_fairplay_sync (30 tests): OK. The new-gap rows are not covered by any test.
