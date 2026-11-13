# DKU OJ path: completion punch list (2026-11-09)

Scope: Mac student flow (Continue with DKU OJ -> OJ login/launch card -> Start -> lock -> answer/submit -> hand-in -> unlock),
fairplay-help actions, server bridge, VM. Out: Canvas, video vision, easy-unlock "Release device", iPad.
Evidence: [V] = ran it today; [R] = read-only (code/docs read, not run); [C] = caller-reported, not rerun.
Ran today [V]: OJ `test_fairplay_pages/_help/_security` 90 OK; `test_fairplay_native_session/_device_session` 153 OK;
server `test_native_exit*.py` 49 passed. Local stack was DOWN (`status.sh`), so verify.sh / sim_native_exit.py not rerun ([C] 41/41).

## 1. Must do before real students use the OJ button

| # | Item | Status | Who | Exact files | Acceptance test |
|---|---|---|---|---|---|
| M1 | Commit Mac relock-registration fixes; same for Windows | built-untested [R] | agent headless (after mac/ editor finishes) | mac/Sources/Proctora/{MainWindowController,NativeExit,NativeLeaveSheet,NativeLock,NativeRenewSchedule,ProtectedBrowserController}.swift, mac/Tests/ProctoraTests/{NativeExit,NativeLeave,NativeRegistration}Tests.swift; Windows src/FairPlay.Core/{NativeExit,NativeLockSetup,NativeLockState}.cs, src/FairPlay.Shell/MainWindow.University.cs | `swift test` all pass (caller: 582) and Windows tests pass on the committed tree |
| M2 | Migration 0222 off the core table (see section 3) | not built | agent headless | dkuoj-design-b: judge/utils/fairplay_device_session.py, judge/views/fairplay.py; revert hunks in judge/models/contest.py, judge/utils/assignment_schedule.py, judge/models/tests/test_assignment_harvest.py; judge/migrations/0222_* | reopen tests in test_fairplay_device_session.py + test_student_with_extra_time... pass without the field; `git diff judge/models/contest.py` empty; `makemigrations --check --dry-run` clean |
| M3 | Live OJ state check: which migrations (0220-0222) and which of the 45 modified files are already in the live image | needs owner | owner with sudo (OJ VM) | live image vs dkuoj-design-b `git diff --stat`; `manage.py showmigrations judge` | written list: live == checkout, or the exact delta to ship. Read-only hint [R]: live baseline-orig fairplay_device_session.py writes `fairplay_deadline_override` unguarded, so 0222 is probably applied live |
| M4 | Core OJ edits outside FairPlay modules (settings, urls, models/contest.py, models/profile.py, views/problem.py, views/contests.py, api_v2.py, template_context.py, assignment_*, workspace.js, list/ranking/workspace/row templates) | needs owner | owner decides; agent splits | files listed in `git -C ~/code/dkuoj-design-b diff --stat` | owner signs off each core hunk or it moves into fairplay_* modules / native_browser_middleware.py |
| M5 | OJ delivery bundle matching feat/design-b (native-oj-update-20261107 covers only 8 files) | not built | agent headless builds bundle; owner with sudo activates outside exam windows | ~/code/dku-vm-deploy/native-oj-update-* (new folder, never edit the old ones) | `update-oj.sh --check` PASS on the OJ VM; after activate, OJ health + `/contest/<key>/fairplay-help` loads |
| M6 | OJ https gateway `https://10.200.31.64:8443/` (mTLS) + enrollment at dku.speakupai.uz, as hardcoded in mac/Sources/Proctora/NativeBrowserProfile.swift:16 | needs owner | owner with sudo | OJ VM gateway config; FairPlay env (enrollment CA) | from a Mac: no cert -> refused, enrolled cert -> 200 (same checks as verify.py lines 134-160) |
| M7 | Signed release Mac build with FAIRPLAY_NATIVE_EXIT_PUBLIC_KEY (key in fairplay_vm_deploy_notes), published as "Latest" without a version bump | not built | owner (signing) | mac/build.sh, release-mac.yml; public repo fairplay-download | downloaded app's Leave code -> fairplay-help "Offline exit code" -> 6 digits unlock it |
| M8 | Real-Mac run of the local e2e script steps 1-11, then once against the VM + live OJ | never done | owner on real Mac | ~/code/fairplay-local-e2e (run-app.sh, panic.sh ready) | every step's "Expected" holds; plus signed-build Keychain relock after a kill in the same boot, ATS to the OJ IP, and after a restart: no relock, home screen, Wi-Fi and AnyConnect work |
| M9 | VM: FAIRPLAY_ADMIN_TEST_CODE >= 12 chars or unset BEFORE any new server deploy (server/app/config.py refuses to start) | done (VM value is 20 chars) | owner (fairplay.env, no sudo) | /home/fairplay/fairplay.env | `systemctl --user restart fairplay` then /api/health ok |
| M10 | VM: bind uvicorn 8200 to 127.0.0.1 or restrict FORWARDED_ALLOW_IPS (spoofed client IP defeats the per-IP escrow/exit caps); set login_school_nets | needs owner | owner (maybe sudo for nginx) | fairplay.service drop-in, fairplay.env | curl with forged X-Forwarded-For from outside gets the real IP in logs |
| M11 | Deadline auto-unlock (e2e step 8) relies on Celery harvest + ASSIGNMENT_HARVEST_ENABLED on the live OJ (local stack has no worker) | built-untested [R] | owner with sudo (check) | OJ celery container, dmoj settings | FP2-style exam on live OJ: app unlocks by itself at the deadline |
| M12 | Native "Give extra time": hidden in native mode (templates/contest/fairplay_help.html:113); the old path only tells the FairPlay server, the OJ end_time is unchanged | not built | needs owner (drop from scope or build via M2 real_start shift) | judge/views/fairplay.py, judge/utils/fairplay_device_session.py, fairplay_help.html | active timed native student gets +N min; OJ and app both end N min later |

Professor actions in native mode today [R]: Unlock device, Unlock and reopen, Reopen exam (after hand-in), Offline exit code.
"Approve resume" is hidden by design: not applicable. "Issue new code" is gone (the access code was removed everywhere).

## 2. Can wait

| # | Item | Status | Who | Exact files | Acceptance test |
|---|---|---|---|---|---|
| W1 | Unlocked-until-Start-exam + https-only signed start-link claim | not built | fp-architect then agent | mac/Sources/Proctora/*, judge/views/fairplay_launch.py | plain-http claim refused; lock begins at Start |
| W2 | Roster sync + professor materials OJ patches (planned "0021/0022"; those numbers are already taken in dkuoj-patches, renumber) | not built | agent headless | judge/utils/fairplay_*.py, server/app/routes_lms.py | removed student's start refused; handout visible in exam |
| W3 | FairPlay tables in the OJ DB (0220/0221 device session/slot/audit) vs "all FairPlay state in FairPlay's DB" | needs owner | owner decides | judge/models/fairplay_device_session.py | decision recorded; no new OJ migration added |
| W4 | TLS renewal automation for dku.speakupai.uz (expires 2027-01-26) | needs owner | owner | le/renewal conf + crontab | `certbot renew --dry-run` ok |
| W5 | Security audit findings touching the lock (shared app key, offline-key re-fetch, sealed_at backdating) | not built | fp-security then agent | see ~/security-audit-skill/*/run-1/REPORT.md | each finding fixed or accepted |
| W6 | Windows native flow on a real PC | built-untested | owner on real PC | fairplay-windows-design-b | e2e steps 1-11 on Windows |

## 3. Migration 0222 decision

What it does [R]: adds `ContestParticipation.fairplay_deadline_override` (core table) and `FairPlayDeviceSession.reopen_until`; core
`ContestParticipation.end_time` (judge/models/contest.py:944) returns the override, so "Reopen exam" can extend a timed (time_limit) attempt
past real_start + time_limit; assignment_schedule.py was edited so harvest follows it. Untimed exams never need it (reopen is capped at contest end).
FairPlay-owned table alone: storage yes, but core end_time only reads core fields, so it would still need the core edit. Not enough.
Minimal fix (no schema, no core code): for timed exams set the existing column `real_start = min(until, contest.end_time) - time_limit`
inside instructor_reopen/attach_participation; OJ end_time, timer and harvest then follow unchanged. Record the original start in
FairPlayDeviceAudit.reason. Replace the `reopen_until` gate with "a reopen audit row newer than the last session". Drop the 0222 file if it
is not applied live; if M3 shows it applied, keep the file untouched and just stop using the columns (no reverse migration).
Side effect to accept: the participation's shown start time and ranking cumtime shift for reopened students.

## 4. Local e2e script (~/code/fairplay-local-e2e/README.md) vs headless coverage [R]

| Step | Headless coverage |
|---|---|
| 1 start + run-app | partial: verify.py service/health checks; app launch only on real Mac |
| 2 Continue with DKU OJ, prompt, login, list | partial: gateway login + roster listing; prompt/quit-apps UI not covered |
| 3 Start -> lock | partial: signed claim + join (verify.py 151-162); real lock not covered |
| 4 answer + Submit | not covered (only "exam page shows the problems") |
| 5 hand-in -> unlock, restart refused | covered server/OJ side (completed receipt, 409); Mac unlock-on-receipt by Swift unit tests only |
| 6 Leave -> Mac code -> offline exit code | covered: verify.py 215-229 + sim_native_exit.py; code entry on Mac by NativeExitTests only |
| 7 kill -9 relock | not covered (real Mac only) |
| 8 deadline auto-unlock | OJ unit tests only (close_after_deadline); no Celery in local stack |
| 9 not on roster | covered (verify.py 113-116, 189) |
| 10 plain browser refusal | covered (verify.py 118-124) |
| 11 hold after restart -> Unlock -> Reopen | partial: "Unlock device -> released" (verify.py 203); hold + reopen admitted by OJ unit tests only |

## How to run tests
- Server: bare `pytest` resolves to the system Python 3.9 and fails with `MappedAnnotationError ... Mapped[str | None]` at app/models.py:16. Use a Python 3.14 venv:
  `cd ~/code/fairplay-design-b/server && .venv/bin/python -m pytest -q` (or `/Users/ava/code/fairplay-release-0.7.9/server/.venv/bin/python -m pytest -q`, the FP_PY of the local stack). Full suite: 1652 passed, 1 skipped.
- OJ: use the OJ venv with the local-e2e settings, but with the stack-only values reset (native off, no ingress mode, harvest off, insecure off), else page and deadline tests fail for settings reasons.
  `/tmp/ojt/ojt_settings.py`: `from oj_settings import *` then `FAIRPLAY_NATIVE_BROWSER_ENABLED=False`, `FAIRPLAY_NATIVE_INGRESS_MODE=''`, `ASSIGNMENT_HARVEST_ENABLED=False`, `FAIRPLAY_ALLOW_INSECURE=False`;
  `cd ~/code/dkuoj-design-b; PYTHONPATH=/tmp/ojt:$HOME/code/fairplay-local-e2e/config DJANGO_SETTINGS_MODULE=ojt_settings .tmpvenv-ojtest/v12/bin/python manage.py test judge.views.tests judge.utils.tests.test_fairplay_help judge.utils.tests.test_fairplay_revision judge.models.tests.test_assignment_harvest --noinput -p "test_fairplay*"` (539 OK).
- Local sim: export FAIRPLAY_NATIVE_LOCK_SIGNING_KEY (base64 32 bytes) BEFORE ./start.sh (the FairPlay server reads it, not the sim), then `source tools/env.sh; "$OJ_PY" tools/sim_native_exit.py`; ./stop.sh after.
