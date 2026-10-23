"""Review of open-to / automatic close (2026-10-23): races, a close that stopped half way,
the teacher's Add time, DKU OJ moving its end time, and edge cases of the settings."""
import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app import closing, routes_student
from app.models import Attempt, Exam
from app.timeutil import iso, parse_iso, utcnow

from .conftest import SAMPLE_EXAM, login
from .test_dkuoj_bridge import deliveries, fake, oj_exam, run, start as oj_start  # noqa: F401
from .test_open_to_autoclose import (NOT_OPEN, _events, _manual, _set_deadline, attempt,  # noqa: F401
                                     auto_close, client, db, ids_by_name, new_exam, oj, set_exam, start)


def _attempts(c, exam_id):
    async def go(s):
        return (await s.execute(select(Attempt).where(Attempt.exam_id == exam_id))).scalars().all()
    return db(c, go)


# ---------------------------------------------------------------- open to
def test_selected_student_wrong_codes_still_lock(client, teacher, alice):
    e = new_exam(client, teacher)
    ids = ids_by_name(client, teacher, e["id"])
    e = client.post(f"/api/exams/{e['id']}/open", json={"open_to": [ids["alice"]]}, headers=teacher).json()
    for _ in range(routes_student.MAX_WRONG_CODES):
        assert start(client, alice, e["id"], "000000").json()["detail"] == "Wrong start code"
    r = start(client, alice, e["id"], e["start_code"])
    assert r.status_code == 429 or "Too many wrong codes" in r.text


def test_selection_removed_during_start(client, teacher, bob, monkeypatch):
    """The teacher removes the student between the first check and the start's locked read:
    the locked read wins, no attempt is made."""
    e = new_exam(client, teacher)
    ids = ids_by_name(client, teacher, e["id"])
    e = client.post(f"/api/exams/{e['id']}/open", json={"open_to": [ids["bob"]]}, headers=teacher).json()
    real = routes_student.require_open_for

    async def check_then_remove(session, exam_id, user_id):
        await real(session, exam_id, user_id)          # passes: bob is selected
        from app.db import get_database
        async with get_database().sessionmaker() as s2:
            await s2.execute(update(Exam).where(Exam.id == exam_id).values(open_to=[]))
            await s2.commit()
    monkeypatch.setattr(routes_student, "require_open_for", check_then_remove)
    r = start(client, bob, e["id"], e["start_code"])
    assert r.status_code == 403 and r.json()["detail"] == NOT_OPEN
    assert _attempts(client, e["id"]) == []


def test_empty_change_keeps_everyone(client, teacher, alice):
    """A change with nothing to add or remove leaves "everyone" as it is (not a frozen list
    of today's class, which would lock out students who join the class later)."""
    e = new_exam(client, teacher)
    e = client.post(f"/api/exams/{e['id']}/open", headers=teacher).json()
    for body in ({}, {"add": [], "remove": []}):
        r = client.put(f"/api/exams/{e['id']}/open-to", json=body, headers=teacher)
        assert r.status_code == 200 and r.json()["open_to"] == "all"


# ---------------------------------------------------------------- settings
def test_draft_with_past_close_time_is_not_opened(client, teacher):
    e = new_exam(client, teacher, closes_at=iso(utcnow() - timedelta(hours=1)))
    r = client.post(f"/api/exams/{e['id']}/open", headers=teacher)
    assert r.status_code == 409 and "automatic close time has passed" in r.json()["detail"]
    assert client.get(f"/api/exams/{e['id']}", headers=teacher).json()["closes_at"] is not None


def test_close_time_must_be_after_the_start(client, teacher):
    r = client.post("/api/exams", json={**SAMPLE_EXAM, "scheduled_at": "2026-12-01T10:00:00Z",
                                        "closes_at": "2026-12-01T09:00:00Z"}, headers=teacher)
    assert r.status_code == 422 and "closes_at" in r.text
    r = client.post("/api/exams", json={**SAMPLE_EXAM, "scheduled_at": "2026-12-01T10:00:00Z",
                                        "closes_at": "2026-12-01T12:00:00+08:00"}, headers=teacher)
    assert r.status_code == 422          # 12:00 in China = 04:00 UTC, before the start
    r = client.post("/api/exams", json={**SAMPLE_EXAM, "scheduled_at": "2026-12-01T10:00:00Z",
                                        "closes_at": "2026-12-01T20:00:00+08:00"}, headers=teacher)
    assert r.status_code == 201 and r.json()["closes_at"] == "2026-12-01T12:00:00.000Z"


# ---------------------------------------------------------------- deadlines
def test_add_time_is_honoured_by_the_automatic_close(client, teacher, alice):
    closes = utcnow().replace(microsecond=0) + timedelta(minutes=5)
    e = new_exam(client, teacher, closes_at=iso(closes))
    e = client.post(f"/api/exams/{e['id']}/open", headers=teacher).json()
    a = start(client, alice, e["id"], e["start_code"]).json()
    assert parse_iso(a["deadline_at"]) == closes
    assert client.post(f"/api/exams/{e['id']}/extend", json={"minutes": 10}, headers=teacher).status_code == 200
    again = client.get(f"/api/student/attempts/{a['attempt_id']}", headers=alice).json()
    assert parse_iso(again["deadline_at"]) == closes + timedelta(minutes=10)
    assert auto_close(client, now=closes + closing.LATE_GRACE + timedelta(seconds=1)) == []
    assert auto_close(client, now=closes + timedelta(minutes=10) + closing.LATE_GRACE + timedelta(seconds=1)) \
        == [e["id"]]
    assert attempt(client, a["attempt_id"]).status == "submitted"


# ---------------------------------------------------------------- the close itself
def test_close_stopped_half_way_is_finished_by_the_loop(client, teacher, alice):
    """A worker that died after flipping the status (before the hand-in) leaves attempts
    writing in a closed exam: the loop hands them in once."""
    e = new_exam(client, teacher)
    e = client.post(f"/api/exams/{e['id']}/open", headers=teacher).json()
    aid = start(client, alice, e["id"], e["start_code"]).json()["attempt_id"]
    closed = utcnow() - timedelta(minutes=2)
    set_exam(client, e["id"], status="closed", closed_at=closed, auto_closed=True, start_code=None)
    # not while the close may still be running
    assert auto_close(client, now=closed + timedelta(seconds=5)) == []
    assert attempt(client, aid).status == "in_progress"

    async def any_(s):
        return await closing.any_candidate(s)
    assert db(client, any_) is True
    auto_close(client)
    at = attempt(client, aid)
    assert at.status == "submitted" and at.auto_submitted and at.receipt
    assert db(client, lambda s: _events(s, aid)).count(closing.AUTO_CLOSE_EVENT) == 1
    auto_close(client)
    assert db(client, lambda s: _events(s, aid)).count(closing.AUTO_CLOSE_EVENT) == 1
    assert db(client, any_) is False


def test_old_closed_exams_are_not_rescanned(client, teacher, alice):
    e = new_exam(client, teacher)
    e = client.post(f"/api/exams/{e['id']}/open", headers=teacher).json()
    aid = start(client, alice, e["id"], e["start_code"]).json()["attempt_id"]
    set_exam(client, e["id"], status="closed", closed_at=utcnow() - timedelta(days=30))
    auto_close(client)
    assert attempt(client, aid).status == "in_progress"


def test_teacher_close_and_automatic_close_at_the_same_moment(client, teacher, alice):
    e = new_exam(client, teacher)
    e = client.post(f"/api/exams/{e['id']}/open", headers=teacher).json()
    aid = start(client, alice, e["id"], e["start_code"]).json()["attempt_id"]
    past = utcnow() - timedelta(minutes=5)
    set_exam(client, e["id"], closes_at=past)
    db(client, lambda s: _set_deadline(s, aid, past))

    async def both():
        from app.db import get_database
        sm = get_database().sessionmaker

        async def by_teacher():
            async with sm() as s:
                ex = await s.get(Exam, e["id"])
                return await closing.close_exam_now(s, ex)
        return await asyncio.gather(by_teacher(), closing.run_once(sm))
    teacher_won, auto = client.portal.call(both)
    assert (1 if teacher_won else 0) + len(auto) == 1
    at = attempt(client, aid)
    assert at.status == "submitted" and at.auto_submitted
    ex = client.get(f"/api/exams/{e['id']}", headers=teacher).json()
    assert ex["status"] == "closed" and ex["auto_closed"] is (not teacher_won)


# ---------------------------------------------------------------- DKU OJ
def test_dkuoj_end_time_moved_later_while_live(oj, fake):  # noqa: F811
    teacher, alice = (login(oj, u) for u in ("teacher", "alice"))
    fake.end = utcnow().replace(microsecond=0) + timedelta(minutes=30)
    run(oj)
    e = oj_exam(oj, teacher)
    e = oj.post(f"/api/exams/{e['id']}/open", headers=teacher).json()
    a = oj_start(oj, alice, e["id"], e["start_code"]).json()
    assert parse_iso(a["deadline_at"]) == fake.end + timedelta(seconds=600)
    # the teacher gives 20 more minutes in DKU OJ
    fake.end = fake.end + timedelta(minutes=20)
    fake.listing_updated = iso(utcnow() + timedelta(seconds=1))
    run(oj)
    assert oj_exam(oj, teacher)["closes_at"] == iso(fake.end)
    again = oj.get(f"/api/student/attempts/{a['attempt_id']}", headers=alice).json()
    assert parse_iso(again["deadline_at"]) == fake.end + timedelta(seconds=600)
    # moved earlier again: a running deadline is never cut here
    fake.end = fake.end - timedelta(minutes=10)
    fake.listing_updated = iso(utcnow() + timedelta(seconds=2))
    run(oj)
    assert parse_iso(attempt(oj, a["attempt_id"]).deadline_at.isoformat()) \
        == fake.end + timedelta(minutes=10, seconds=600)


def test_dkuoj_close_stopped_half_way_still_delivers(oj, fake):  # noqa: F811
    teacher, alice = (login(oj, u) for u in ("teacher", "alice"))
    fake.end = utcnow().replace(microsecond=0) + timedelta(minutes=30)
    run(oj)
    e = oj_exam(oj, teacher)
    e = oj.post(f"/api/exams/{e['id']}/open", headers=teacher).json()
    aid = oj_start(oj, alice, e["id"], e["start_code"]).json()["attempt_id"]
    set_exam(oj, e["id"], status="closed", closed_at=utcnow() - timedelta(minutes=1), auto_closed=True)
    auto_close(oj)
    assert attempt(oj, aid).status == "submitted"
    assert deliveries(oj)[aid][0] == "pending"
    auto_close(oj)
    assert len([k for k in deliveries(oj) if k == aid]) == 1
