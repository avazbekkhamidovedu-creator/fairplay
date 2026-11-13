from datetime import timedelta

from sqlalchemy import select

from app.models import Exam
from app.timeutil import iso, utcnow

from .conftest import login
from .oj_helpers import _events
from .test_dkuoj_bridge import KEY, db, run
from .test_dkuoj_bridge_v2 import fake, oj  # noqa: F401  (fixtures)


def state(c):
    async def q(s):
        e = (await s.execute(select(Exam).where(Exam.external_key == KEY))).scalar_one()
        return e.status, e.start_code, dict(e.external_meta or {})
    return db(c, q)


def test_not_opened_before_the_scheduled_start(oj, fake):
    run(oj, now=fake.start - timedelta(minutes=1))
    assert state(oj)[0] == "draft"
    assert db(oj, _events("auto_opened")) == []


def test_opens_at_the_scheduled_start_once(oj, fake):
    run(oj, now=fake.start + timedelta(seconds=1))
    status, code, _ = state(oj)
    assert status == "live" and code
    assert [d for _, d in db(oj, _events("auto_opened"))] == [f"exam {KEY}: scheduled start reached"]
    run(oj, now=fake.start + timedelta(minutes=5))
    assert state(oj)[:2] == ("live", code)
    assert len(db(oj, _events("auto_opened"))) == 1


def test_students_see_it_live_without_anyone_pressing_open(oj, fake):
    run(oj, now=fake.start + timedelta(seconds=1))
    alice = login(oj, "alice")
    listed = [x for x in oj.get("/api/student/exams", headers=alice).json() if x["status"] == "live"]
    assert len(listed) == 1 and listed[0]["title"] == "CS 101 Midterm"


def test_a_closed_exam_is_never_reopened(oj, fake):
    run(oj, now=fake.start + timedelta(seconds=1))
    teacher = login(oj, "teacher")
    eid = next(e for e in oj.get("/api/exams", headers=teacher).json() if e.get("external"))["id"]
    assert oj.post(f"/api/exams/{eid}/close", headers=teacher).status_code == 200
    run(oj, now=fake.start + timedelta(minutes=10))
    assert state(oj)[0] == "closed"
    assert len(db(oj, _events("auto_opened"))) == 1


def test_a_start_moved_into_the_future_waits(oj, fake):
    run(oj, now=fake.start - timedelta(minutes=5))
    old_start = fake.start
    fake.start = old_start + timedelta(hours=3)
    fake.end = fake.start + timedelta(hours=2)
    fake.listing_updated = iso(utcnow() + timedelta(seconds=5))
    run(oj, now=old_start + timedelta(minutes=1))
    assert state(oj)[0] == "draft"
    run(oj, now=fake.start + timedelta(seconds=1))
    assert state(oj)[0] == "live"


def test_needs_the_package(oj, fake):
    fake.too_early = True
    run(oj, now=fake.start + timedelta(seconds=1))
    assert state(oj)[0] == "draft"
    fake.too_early = False
    run(oj, now=fake.start + timedelta(minutes=70))
    assert state(oj)[0] == "live"


def test_an_ended_exam_stays_draft_and_says_why_once(oj, fake):
    run(oj, now=fake.start - timedelta(minutes=5))
    fake.end = utcnow() - timedelta(minutes=30)
    fake.listing_updated = iso(utcnow() + timedelta(seconds=5))
    run(oj, now=fake.start + timedelta(minutes=1))
    status, _, meta = state(oj)
    assert status == "draft" and "end time" in meta["auto_open_error"]
    run(oj, now=fake.start + timedelta(minutes=2))
    assert state(oj)[0] == "draft"
    assert db(oj, _events("auto_opened")) == []


def test_the_manual_open_still_works(oj, fake):
    run(oj)
    teacher = login(oj, "teacher")
    eid = next(e for e in oj.get("/api/exams", headers=teacher).json() if e.get("external"))["id"]
    r = oj.post(f"/api/exams/{eid}/open", headers=teacher)
    assert r.status_code == 200 and r.json()["status"] == "live"
