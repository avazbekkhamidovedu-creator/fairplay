"""2026-11 owner decision: "FairPlay just shows DKU OJ". For DKU OJ exams FairPlay adds no limits of its
own: nothing is frozen, no 24 h package lead, no audience confirmation, no caps on time, reopen, release or
exit codes; settings change any time; staff are any non-student DKU OJ signs. Against a FAKE DKU OJ."""
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.integrations.dkuoj.lock_sites import build_lock_sites
from app.integrations.dkuoj.models import OjEditor
from app.models import Attempt, Exam
from app.timeutil import iso, parse_iso, utcnow

from .conftest import login
from .oj_helpers import DEV_A, KEY, sign_in
from .test_dkuoj_bridge import db, package, run
from .test_dkuoj_rescue import STU, V1, attempts_of, call, fake, oj, put, qid_of, room, started  # noqa: F401


def exam_row(c):
    return db(c, lambda s: s.scalar(select(Exam).where(Exam.external_key == KEY)))


def view(c):
    r = call(c, "GET", f"{V1}/exams/{KEY}")
    assert r.status_code == 200, r.text
    return r.json()


def bump(fake):
    fake.listing_updated = iso(utcnow() + timedelta(seconds=5))


# ---------------------------------------------------------------- 1. only the questions are frozen
def test_questions_freeze_at_the_first_start_but_name_time_and_settings_follow_dkuoj(oj, room, fake, monkeypatch):
    eid, codes, _, teacher = room
    body = sign_in(oj, codes["alice"], DEV_A)
    a = started(oj, body, eid, DEV_A)
    q5 = qid_of(a)
    put(oj, body, a, {q5: "my answer"}, 1)
    before = attempts_of(oj)[0].deadline_at
    first = exam_row(oj)
    assert first.external_frozen_version == first.external_version
    # DKU OJ: Q5 removed, a title changed, the time limit 90 -> 120 min, a new exam name
    pkg = package("newversion1")
    pkg["questions"] = [q for q in pkg["questions"] if q["label"] != "Q5"]
    pkg["questions"][0]["title"] = "Changed live"
    fake.package = pkg
    orig = fake.listing
    monkeypatch.setattr(fake, "listing", lambda: {**orig(), "time_limit_seconds": 7200, "name": "Renamed"})
    bump(fake)
    n_calls = len(fake.calls)
    run(oj)
    e = exam_row(oj)
    assert e.external_version == first.external_version and e.question_count == 7     # questions stay as started
    assert e.questions[0]["title"] != "Changed live"
    assert not any(c.endswith("/package") for c in fake.calls[n_calls:])
    assert e.title == "Renamed" and e.duration_min == 120                              # name and time follow
    now = attempts_of(oj)[0]
    assert now.deadline_at - before == timedelta(minutes=30)
    assert now.answers == {q5: "my answer"}                                            # nothing rewritten


def test_a_newer_package_listed_but_not_fetched_blocks_a_first_start(oj, room, fake):
    eid, codes, _, _ = room
    # DKU OJ lists a newer package version, FairPlay has not fetched it yet, nobody started
    meta = exam_row(oj).external_meta
    db(oj, lambda s: _upd(s, Exam, {"external_meta": {**meta, "package_version": "newer-not-fetched"}}))
    body = sign_in(oj, codes["alice"], DEV_A)
    from .oj_helpers import start
    r = start(oj, body, eid, DEV_A)
    assert r.status_code == 409 and "has not arrived" in r.json()["detail"]


def test_a_frozen_exam_never_takes_a_newer_package(oj, room, fake):
    version = exam_row(oj).external_version
    db(oj, lambda s: _upd(s, Exam, {"external_frozen_version": version}))
    fake.package = package("v-after")
    bump(fake)
    run(oj)
    assert exam_row(oj).external_version != "v-after"


async def _upd(s, model, values):
    await s.execute(update(model).values(**values))
    await s.commit()


def test_end_time_moved_earlier_shortens_a_running_attempt(oj, room, fake):
    eid, codes, _, _ = room
    body = sign_in(oj, codes["carol"], DEV_A)
    started(oj, body, eid, DEV_A)
    fake.end = utcnow() + timedelta(minutes=5)
    bump(fake)
    run(oj)
    left = attempts_of(oj)[0].deadline_at - utcnow()
    assert left <= timedelta(minutes=5, seconds=5)               # end + carol's 0 extra


def test_settings_change_after_start_and_after_close(oj, room):
    eid, codes, _, teacher = room
    body = sign_in(oj, codes["carol"], DEV_A)
    started(oj, body, eid, DEV_A)
    path = f"{V1}/exams/{KEY}/settings"
    assert call(oj, "POST", path, {"calculator": "graphing", "box_link": "https://duke.box.com/s/" + "a" * 20}
                ).status_code == 200
    assert call(oj, "POST", f"{V1}/exams/{KEY}/close", {}).status_code == 200
    r = call(oj, "POST", path, {"calculator": "off", "websites": ["docs.python.org"]})
    assert r.status_code == 200 and r.json()["settings"]["calculator"] == "off"


# ---------------------------------------------------------------- 2. package timing
def test_package_not_asked_after_a_removed_listing_but_asked_far_before_start(oj, fake):
    fake.start = utcnow() + timedelta(days=30)
    fake.end = fake.start + timedelta(hours=2)
    out = run(oj)
    assert out["packages"] == 1


# ---------------------------------------------------------------- 4. time
def test_add_time_has_no_cap_and_is_idempotent_with_a_request_id(oj, room):
    eid, codes, _, _ = room
    body = sign_in(oj, codes["carol"], DEV_A)
    started(oj, body, eid, DEV_A)
    d0, extra0 = exam_row(oj).duration_min, int((exam_row(oj).external_meta or {}).get("extra_close_minutes") or 0)
    before = attempts_of(oj)[0].deadline_at
    for _ in range(2):
        assert call(oj, "POST", f"{V1}/exams/{KEY}/add-time", {"minutes": 300, "request_id": "dbl-1"}).status_code == 200
    assert exam_row(oj).duration_min == d0 + 300
    assert attempts_of(oj)[0].deadline_at - before == timedelta(minutes=300)
    assert int(exam_row(oj).external_meta["extra_close_minutes"]) == extra0 + 300
    assert call(oj, "POST", f"{V1}/exams/{KEY}/add-time", {"minutes": 1000, "request_id": "dbl-2"}).status_code == 200
    assert exam_row(oj).duration_min == d0 + 1300
    # the X-FP-Request-Id header is not signed, so it is not an idempotency id: both calls add time
    for _ in range(2):
        assert call(oj, "POST", f"{V1}/exams/{KEY}/add-time", {"minutes": 7},
                    headers={"X-FP-Request-Id": "hdr-1"}).status_code == 200
    assert exam_row(oj).duration_min == d0 + 1314
    for bad in ({"minutes": 5, "request_id": "has space"}, {"minutes": 5, "request_id": 7}):
        assert call(oj, "POST", f"{V1}/exams/{KEY}/add-time", bad).status_code == 400


def test_add_time_reopens_an_exam_the_clock_closed(oj, room):
    db(oj, lambda s: _upd(s, Exam, {"status": "closed", "auto_closed": True, "closed_at": utcnow()}))
    r = call(oj, "POST", f"{V1}/exams/{KEY}/add-time", {"minutes": 20})
    assert r.status_code == 200, r.text
    e = exam_row(oj)
    assert e.status == "live" and e.auto_closed is False and e.start_code
    # closed by the instructor: still closed
    db(oj, lambda s: _upd(s, Exam, {"status": "closed", "auto_closed": False}))
    assert call(oj, "POST", f"{V1}/exams/{KEY}/add-time", {"minutes": 20}).status_code == 409


def test_fairplay_teacher_extend_is_open_for_dkuoj_exams(oj, room):
    eid, _, _, teacher = room
    r = oj.post(f"/api/exams/{eid}/extend", json={"minutes": 500}, headers=teacher)
    assert r.status_code == 200, r.text
    assert oj.post(f"/api/exams/{eid}/extend", json={"minutes": 0}, headers=teacher).status_code == 422


def test_approve_resume_gives_back_all_the_waiting_time_on_a_dkuoj_exam(oj, room):
    eid, codes, _, _ = room
    body = sign_in(oj, codes["carol"], DEV_A)
    started(oj, body, eid, DEV_A)
    before = attempts_of(oj)[0].deadline_at
    db(oj, lambda s: _upd(s, Attempt, {"pending_resume": True, "pending_since": utcnow() - timedelta(minutes=40)}))
    assert call(oj, "POST", f"{V1}/exams/{KEY}/approve", {"netid": "carol"}).status_code == 200
    gained = attempts_of(oj)[0].deadline_at - before
    assert timedelta(minutes=39) < gained < timedelta(minutes=41)      # not cut at 15


# ---------------------------------------------------------------- 6. staff
def test_ta_and_course_staff_the_editors_list_does_not_name_may_act_on_a_class(oj, room):
    for who in ("ta-new", "course-staff-2"):
        r = call(oj, "POST", f"{V1}/classes/31/login", {"class_name": "CS 101"}, acting=who)
        assert r.status_code == 200, (who, r.text)
        assert call(oj, "GET", f"{V1}/classes/31/status", acting=who).status_code == 200
    # a student account never acts as staff
    assert call(oj, "POST", f"{V1}/classes/31/login", {"class_name": "CS 101"}, acting="alice").status_code == 403
    assert call(oj, "GET", f"{V1}/classes/31/status", acting="alice").status_code == 403
    assert call(oj, "GET", f"{V1}/classes/31/status", acting="").status_code == 403


def test_a_ta_who_is_an_editor_acts_on_students_without_any_fairplay_lock(oj, fake):
    fake.editors = fake.editors + [{"username": "ta1", "first_name": "Tina", "last_name": "Assistant"}]
    run(oj)
    for action, data in (("release-device", {}), ("extra-time", {"minutes": 5})):
        r = call(oj, "POST", f"{STU}/carol/{action}", data, acting="ta1")      # no lock, no app, just DKU OJ's signature
        assert r.status_code == 200, (action, r.text)


# ---------------------------------------------------------------- 7a. websites
def hosts(n, prefix="site"):
    return [f"{prefix}{i}.example.edu" for i in range(n)]


def test_more_than_twenty_websites_are_kept_up_to_what_the_lock_carries(oj, room):
    r = call(oj, "POST", f"{V1}/exams/{KEY}/settings", {"websites": hosts(30)})
    assert r.status_code == 200, r.text
    s = r.json()["settings"]
    assert s["websites"] == hosts(30) and s["websites_blocked"] == []
    pages = s["lock_sites"]["pages"]
    assert len(pages) == 30 and len(pages) + len(s["lock_sites"]["resources"]) <= 32


def test_websites_past_the_lock_limit_are_reported_not_dropped_silently(oj, room):
    r = call(oj, "POST", f"{V1}/exams/{KEY}/settings", {"websites": hosts(40)})
    assert r.status_code == 200, r.text
    s = r.json()["settings"]
    assert s["websites"] == hosts(31) and len(s["lock_sites"]["pages"]) == 31
    assert [b["site"] for b in s["websites_blocked"]] == hosts(40)[31:]
    assert all("31" in b["reason"] for b in s["websites_blocked"])
    # shipped Mac apps carry at most 32 sites, so the lock never sends more
    e = exam_row(oj)
    assert len(e.websites) == 31


def test_deny_listed_sites_are_still_refused_among_many(oj, room):
    r = call(oj, "POST", f"{V1}/exams/{KEY}/settings", {"websites": hosts(30) + ["chatgpt.com"]})
    s = r.json()["settings"]
    assert "chatgpt.com" not in s["websites"] and any(b["site"] == "chatgpt.com" for b in s["websites_blocked"])
    assert len(s["websites"]) == 30


def test_lock_sites_cap_is_32_what_shipped_macs_accept():
    pages = build_lock_sites([f"s{i}.example.edu" for i in range(80)])["pages"]
    assert len(pages) == 32


# ---------------------------------------------------------------- 7b / 7c
async def _editors(s):
    return len((await s.execute(select(OjEditor.id))).all())


def test_removed_exam_keeps_its_editors(oj, fake):
    run(oj)
    n = db(oj, _editors)
    assert n == 2
    fake.removed = True
    bump(fake)
    run(oj)
    assert exam_row(oj).external_removed is True
    assert db(oj, _editors) == n
    assert call(oj, "GET", f"{V1}/exams/{KEY}").status_code == 200            # instructors still see it


def test_roster_removal_mid_exam_only_blocks_new_starts(oj, room, fake):
    eid, codes, _, _ = room
    body = sign_in(oj, codes["alice"], DEV_A)
    a = started(oj, body, eid, DEV_A)
    fake.roster = [p for p in fake.roster if p["username"] != "alice"]
    bump(fake)
    run(oj)
    mine = attempts_of(oj)[0]
    assert mine.status == "in_progress" and mine.exited_at is None
    h = {**{"Authorization": f"Bearer {body['token']}"}, "X-FairPlay-Session": a["session"]}
    assert oj.put(f"/api/student/attempts/{a['attempt_id']}/answers", json={"answers": {}, "client_seq": 5},
                  headers=h).status_code == 200
    r = oj.post(f"/api/student/attempts/{a['attempt_id']}/submit", json={"answers": {}, "client_seq": 6}, headers=h)
    assert r.status_code == 200 and r.json()["receipt"]


# ---------------------------------------------------------------- review fixes
def test_a_longer_time_limit_never_passes_the_exam_end(oj, room, fake, monkeypatch):
    eid, codes, _, _ = room
    fake.end = utcnow() + timedelta(minutes=60)               # 90 min limit: the deadline is capped at the end
    bump(fake)
    run(oj)
    body = sign_in(oj, codes["carol"], DEV_A)
    started(oj, body, eid, DEV_A)
    capped = attempts_of(oj)[0].deadline_at
    orig = fake.listing
    monkeypatch.setattr(fake, "listing", lambda: {**orig(), "time_limit_seconds": 7200})
    bump(fake)
    run(oj)
    assert attempts_of(oj)[0].deadline_at == capped


def test_add_time_stays_after_the_next_sync_without_an_end_time(oj, room, fake, monkeypatch):
    eid, codes, _, _ = room
    orig = fake.listing
    monkeypatch.setattr(fake, "listing", lambda: {**orig(), "end_time": None})
    bump(fake)
    run(oj)
    body = sign_in(oj, codes["carol"], DEV_A)
    started(oj, body, eid, DEV_A)
    before = attempts_of(oj)[0].deadline_at
    assert call(oj, "POST", f"{V1}/exams/{KEY}/add-time", {"minutes": 20}).status_code == 200
    bump(fake)
    run(oj)
    assert attempts_of(oj)[0].deadline_at - before == timedelta(minutes=20)


def test_add_time_after_the_clock_closed_gives_the_writers_their_time_back(oj, room):
    eid, codes, _, _ = room
    body = sign_in(oj, codes["carol"], DEV_A)
    started(oj, body, eid, DEV_A)
    closed = utcnow() - timedelta(seconds=5)
    db(oj, lambda s: _upd(s, Exam, {"status": "closed", "auto_closed": True, "closed_at": closed}))
    db(oj, lambda s: _upd(s, Attempt, {"status": "submitted", "auto_submitted": True, "submitted_at": utcnow()}))
    assert call(oj, "POST", f"{V1}/exams/{KEY}/add-time", {"minutes": 15}).status_code == 200
    a = attempts_of(oj)[0]
    assert a.status == "in_progress" and a.auto_submitted is False
    left = a.deadline_at - utcnow()
    assert timedelta(minutes=14) < left <= timedelta(minutes=15)    # given once, not twice


def test_extra_time_for_one_student_after_the_clock_closed(oj, room):
    db(oj, lambda s: _upd(s, Exam, {"status": "closed", "auto_closed": True, "closed_at": utcnow()}))
    r = call(oj, "POST", f"{V1}/exams/{KEY}/students/carol/extra-time", {"minutes": 10})
    assert r.status_code == 200, r.text
    assert exam_row(oj).status == "live"          # the grant reaches into the future: the exam is back, like add-time
    db(oj, lambda s: _upd(s, Exam, {"status": "closed", "auto_closed": False}))
    assert call(oj, "POST", f"{V1}/exams/{KEY}/students/carol/extra-time", {"minutes": 10}).status_code == 409
