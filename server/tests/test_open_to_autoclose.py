"""2026-10-23: open to everyone or to selected students; automatic close at the end time.
Both generic: FairPlay exams and external ones (DKU OJ, against the fake from
test_dkuoj_bridge)."""
import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app import closing
from app.main import create_app
from app.models import Attempt, Exam, IntegrityEvent
from app.timeutil import iso, parse_iso, utcnow

from .conftest import SAMPLE_EXAM, login
from .test_dkuoj_bridge import (FakeOJ, deliveries, fake, oj_exam, oj_settings, run,  # noqa: F401
                                start as oj_start)

NOT_OPEN = "This exam isn't open for you yet"


@pytest.fixture
def _manual(monkeypatch):
    """The test calls closing.run_once itself (no background loop)."""
    monkeypatch.setattr(closing, "BACKGROUND", False)


@pytest.fixture
def client(_manual, client):        # noqa: F811 -- conftest's client, loop switched off
    return client


@pytest.fixture
def oj(tmp_path, static_dir, fake, _manual):  # noqa: F811
    with TestClient(create_app(oj_settings(tmp_path, static_dir))) as c:
        yield c


def db(c, fn):
    async def go():
        from app.db import get_database
        async with get_database().sessionmaker() as s:
            return await fn(s)
    return c.portal.call(go)


def set_exam(c, exam_id, **values):
    async def go(s):
        await s.execute(update(Exam).where(Exam.id == exam_id).values(**values))
        await s.commit()
    db(c, go)
    from app.services import invalidate_exam
    from app.store import get_store
    c.portal.call(invalidate_exam, get_store(), exam_id)


def attempt(c, aid):
    return db(c, lambda s: s.get(Attempt, aid))


def auto_close(c, now=None):
    async def go():
        from app.db import get_database
        return await closing.run_once(get_database().sessionmaker, now=now)
    return c.portal.call(go)


def start(c, h, exam_id, code):
    return c.post(f"/api/student/exams/{exam_id}/start", json={"start_code": code}, headers=h)


def new_exam(c, teacher, **kw):
    r = c.post("/api/exams", json={**SAMPLE_EXAM, **kw}, headers=teacher)
    assert r.status_code == 201, r.text
    return r.json()


def ids_by_name(c, teacher, exam_id):
    rows = c.get(f"/api/exams/{exam_id}/students", headers=teacher).json()
    return {r["username"]: r["id"] for r in rows}


# ---------------------------------------------------------------- open to
def test_open_to_all_is_the_default(client, teacher, alice):
    e = new_exam(client, teacher)
    assert e["open_to"] == "all" and e["closes_at"] is None and e["auto_closed"] is False
    e = client.post(f"/api/exams/{e['id']}/open", headers=teacher).json()
    assert e["open_to"] == "all"
    live = client.get(f"/api/exams/{e['id']}/live", headers=teacher).json()
    assert live["open_to"] == "all" and live["selected"] is None and live["closes_at"] is None
    row = next(x for x in client.get("/api/student/exams", headers=alice).json() if x["id"] == e["id"])
    assert row["open_for_me"] is True and row["package_id"]
    assert start(client, alice, e["id"], e["start_code"]).status_code == 200


def test_selected_students(client, teacher, alice, bob):
    e = new_exam(client, teacher)
    ids = ids_by_name(client, teacher, e["id"])
    assert set(ids) == {"alice", "bob", "carol"}
    # only students of the exam may be selected
    r = client.post(f"/api/exams/{e['id']}/open", json={"open_to": [ids["alice"], 999999]}, headers=teacher)
    assert r.status_code == 422 and "999999" in r.json()["detail"]
    e = client.post(f"/api/exams/{e['id']}/open", json={"open_to": [ids["alice"]]}, headers=teacher).json()
    assert e["open_to"] == [ids["alice"]]
    code = e["start_code"]

    # bob: refused before the code is looked at -- right or wrong code, never a wrong-code
    # lockout (more tries than MAX_WRONG_CODES)
    for _ in range(7):
        r = start(client, bob, e["id"], "000000")
        assert r.status_code == 404 and r.json()["detail"] == "Exam not found"
    r = start(client, bob, e["id"], code)
    assert r.status_code == 404 and r.json()["detail"] == "Exam not found"
    # private: not listed, no start, no offline package
    assert all(x["id"] != e["id"] for x in client.get("/api/student/exams", headers=bob).json())
    assert client.get(f"/api/student/exams/{e['id']}/package-key", headers=bob).status_code == 404
    assert client.get(f"/api/student/exams/{e['id']}/package", headers=bob).status_code == 404

    a = start(client, alice, e["id"], code)
    assert a.status_code == 200, a.text
    aid = a.json()["attempt_id"]

    # while live: add bob -> he starts at once (no lockout was counted)
    r = client.put(f"/api/exams/{e['id']}/open-to", json={"add": [ids["bob"]]}, headers=teacher)
    assert r.status_code == 200 and r.json()["open_to"] == sorted([ids["alice"], ids["bob"]])
    assert start(client, bob, e["id"], code).status_code == 200

    # remove alice who already started: her attempt goes on, a resume still works
    r = client.put(f"/api/exams/{e['id']}/open-to", json={"remove": [ids["alice"]]}, headers=teacher)
    assert r.json()["open_to"] == [ids["bob"]]
    assert attempt(client, aid).status == "in_progress"
    again = start(client, alice, e["id"], code)
    assert again.status_code == 200 and again.json()["attempt_id"] == aid
    assert client.get(f"/api/student/attempts/{aid}", headers=alice).status_code == 200
    row = next(x for x in client.get("/api/student/exams", headers=alice).json() if x["id"] == e["id"])
    assert row["open_for_me"] is True            # she has an attempt

    live = client.get(f"/api/exams/{e['id']}/live", headers=teacher).json()
    assert live["open_to"] == [ids["bob"]]
    assert [(s["username"], s["started"]) for s in live["selected"]] == [("bob", True)]
    students = client.get(f"/api/exams/{e['id']}/students", headers=teacher).json()
    assert {s["username"]: s["started"] for s in students} == {"alice": True, "bob": True, "carol": False}

    # carol never selected; back to everyone -> she may start
    carol = login(client, "carol")
    assert start(client, carol, e["id"], code).status_code == 404
    r = client.put(f"/api/exams/{e['id']}/open-to", json={"open_to": "all"}, headers=teacher)
    assert r.json()["open_to"] == "all"
    assert start(client, carol, e["id"], code).status_code == 200


def test_remove_from_everyone_and_empty_selection(client, teacher, alice, bob):
    e = new_exam(client, teacher)
    ids = ids_by_name(client, teacher, e["id"])
    e = client.post(f"/api/exams/{e['id']}/open", headers=teacher).json()
    # removing one student from "everyone" = everyone else
    r = client.put(f"/api/exams/{e['id']}/open-to", json={"remove": [ids["bob"]]}, headers=teacher)
    assert r.json()["open_to"] == sorted([ids["alice"], ids["carol"]])
    assert start(client, bob, e["id"], e["start_code"]).status_code == 404
    # an empty selection: nobody new may start
    r = client.put(f"/api/exams/{e['id']}/open-to", json={"open_to": []}, headers=teacher)
    assert r.json()["open_to"] == []
    assert start(client, alice, e["id"], e["start_code"]).status_code == 404
    # other teachers can't change it
    other = client.put(f"/api/exams/{e['id']}/open-to", json={"open_to": "all"}, headers=alice)
    assert other.status_code in (401, 403)


def test_reopen_without_body_opens_to_everyone(client, teacher, bob):
    e = new_exam(client, teacher)
    ids = ids_by_name(client, teacher, e["id"])
    client.post(f"/api/exams/{e['id']}/open", json={"open_to": [ids["alice"]]}, headers=teacher)
    client.post(f"/api/exams/{e['id']}/close", headers=teacher)
    e = client.post(f"/api/exams/{e['id']}/open", headers=teacher).json()
    assert e["open_to"] == "all"
    assert start(client, bob, e["id"], e["start_code"]).status_code == 200


# ---------------------------------------------------------------- closes at (FairPlay exams)
def test_native_closes_at_is_optional(client, teacher):
    e = new_exam(client, teacher)
    assert e["closes_at"] is None
    r = client.put(f"/api/exams/{e['id']}", json={**SAMPLE_EXAM, "closes_at": "2026-12-01T10:00:00"},
                   headers=teacher)
    assert r.status_code == 422 and "time zone" in r.text
    r = client.put(f"/api/exams/{e['id']}", json={**SAMPLE_EXAM, "closes_at": "2026-12-01T18:00:00+08:00"},
                   headers=teacher)
    assert r.status_code == 200 and r.json()["closes_at"] == "2026-12-01T10:00:00.000Z"
    # a PUT that leaves it out keeps it; null clears it
    r = client.put(f"/api/exams/{e['id']}", json={**SAMPLE_EXAM}, headers=teacher)
    assert r.json()["closes_at"] == "2026-12-01T10:00:00.000Z"
    r = client.put(f"/api/exams/{e['id']}", json={**SAMPLE_EXAM, "closes_at": None}, headers=teacher)
    assert r.json()["closes_at"] is None


def test_deadline_capped_at_close_time(client, teacher, alice, bob):
    closes = utcnow().replace(microsecond=0) + timedelta(minutes=10)
    e = new_exam(client, teacher, closes_at=iso(closes))           # 30 min exam, closes in 10
    e = client.post(f"/api/exams/{e['id']}/open", headers=teacher).json()
    assert e["closes_at"] == iso(closes)
    assert client.get(f"/api/exams/{e['id']}/live", headers=teacher).json()["closes_at"] == iso(closes)
    a = start(client, alice, e["id"], e["start_code"]).json()
    assert parse_iso(a["deadline_at"]) == closes                   # the late starter's countdown
    again = client.get(f"/api/student/attempts/{a['attempt_id']}", headers=alice).json()
    assert parse_iso(again["deadline_at"]) == closes
    # after the close time nobody new starts
    set_exam(client, e["id"], closes_at=utcnow() - timedelta(seconds=1))
    r = start(client, bob, e["id"], e["start_code"])
    assert r.status_code == 409 and r.json()["detail"] == "This exam has ended"


def test_auto_close_native(client, teacher, alice, bob):
    e = new_exam(client, teacher)
    e = client.post(f"/api/exams/{e['id']}/open", headers=teacher).json()
    a = start(client, alice, e["id"], e["start_code"]).json()
    aid = a["attempt_id"]
    closes = utcnow() - timedelta(minutes=1)
    set_exam(client, e["id"], closes_at=closes)

    async def deadline(s):
        await s.execute(update(Attempt).where(Attempt.id == aid).values(deadline_at=closes))
        await s.commit()
    db(client, deadline)
    # not before close time + LATE_GRACE
    assert auto_close(client, now=closes + timedelta(seconds=30)) == []
    assert client.get(f"/api/exams/{e['id']}", headers=teacher).json()["status"] == "live"
    # an extended student (deadline later) is waited for
    db(client, lambda s: _set_deadline(s, aid, closes + timedelta(minutes=5)))
    assert auto_close(client, now=closes + timedelta(minutes=2)) == []
    db(client, lambda s: _set_deadline(s, aid, closes))

    now = utcnow()
    assert auto_close(client, now=now) == [e["id"]]
    ex = client.get(f"/api/exams/{e['id']}", headers=teacher).json()
    assert ex["status"] == "closed" and ex["auto_closed"] is True and ex["start_code"] is None
    at = attempt(client, aid)
    assert at.status == "submitted" and at.auto_submitted and at.receipt
    evs = db(client, lambda s: _events(s, aid))
    assert closing.AUTO_CLOSE_EVENT in evs
    # not a flag for the teacher
    assert client.get(f"/api/exams/{e['id']}/results", headers=teacher).json()["flags_open"] == 0
    # closes once
    assert auto_close(client, now=now) == []
    # the teacher's Close afterwards is a no-op
    assert client.post(f"/api/exams/{e['id']}/close", headers=teacher).json()["auto_closed"] is True
    # re-open after the close time: the close time is cleared, the teacher closes by hand
    ex = client.post(f"/api/exams/{e['id']}/open", headers=teacher).json()
    assert ex["status"] == "live" and ex["closes_at"] is None and ex["auto_closed"] is False
    assert auto_close(client) == []


async def _set_deadline(s, aid, when):
    await s.execute(update(Attempt).where(Attempt.id == aid).values(deadline_at=when))
    await s.commit()


async def _events(s, aid):
    return [t for t, in (await s.execute(select(IntegrityEvent.type).where(IntegrityEvent.attempt_id == aid))).all()]


def test_auto_close_two_runners_close_once(client, teacher, alice):
    e = new_exam(client, teacher)
    e = client.post(f"/api/exams/{e['id']}/open", headers=teacher).json()
    aid = start(client, alice, e["id"], e["start_code"]).json()["attempt_id"]
    past = utcnow() - timedelta(minutes=5)
    set_exam(client, e["id"], closes_at=past)
    db(client, lambda s: _set_deadline(s, aid, past))

    async def two():
        from app.db import get_database
        sm = get_database().sessionmaker
        return await asyncio.gather(closing.run_once(sm), closing.run_once(sm))
    a, b = client.portal.call(two)
    assert sorted(a + b) == [e["id"]]
    assert len(db(client, lambda s: _events(s, aid))) == 1
    at = attempt(client, aid)
    assert at.status == "submitted" and at.auto_submitted


def test_lease_one_holder(client):
    t0 = utcnow()

    async def go(s):
        first = await closing.acquire_lease(s, "x", "w1", t0)
        second = await closing.acquire_lease(s, "x", "w2", t0)
        renew = await closing.acquire_lease(s, "x", "w1", t0 + timedelta(seconds=10))
        taken = await closing.acquire_lease(s, "x", "w2", t0 + timedelta(minutes=5))   # expired
        return first, second, renew, taken
    assert db(client, go) == (True, False, True, True)


def test_exams_without_close_time_never_auto_close(client, teacher, alice):
    e = new_exam(client, teacher)
    e = client.post(f"/api/exams/{e['id']}/open", headers=teacher).json()
    start(client, alice, e["id"], e["start_code"])
    assert auto_close(client, now=utcnow() + timedelta(days=3)) == []
    assert client.get(f"/api/exams/{e['id']}", headers=teacher).json()["status"] == "live"


# ---------------------------------------------------------------- external exams (DKU OJ)
def test_external_selection_and_auto_close(oj, fake):  # noqa: F811
    teacher, alice, carol = (login(oj, u) for u in ("teacher", "alice", "carol"))
    fake.end = utcnow().replace(microsecond=0) + timedelta(minutes=30)   # 90 min limit, ends in 30
    run(oj)
    e = oj_exam(oj, teacher)
    assert e["closes_at"] == iso(fake.end)                 # from the source, read-only
    # the external roster is the list to pick from (bob is in the demo class, not listed)
    ids = ids_by_name(oj, teacher, e["id"])
    # (bridge v2: a synced student who never set up has the placeholder username oj:<netid>)
    assert set(ids) == {"alice", "carol", "oj:ax19"}
    bob_id = db(oj, lambda s: _user_id(s, "bob"))
    r = oj.post(f"/api/exams/{e['id']}/open", json={"open_to": [bob_id]}, headers=teacher)
    assert r.status_code == 422
    e = oj.post(f"/api/exams/{e['id']}/open", json={"open_to": [ids["alice"]]}, headers=teacher).json()
    assert e["status"] == "live" and e["open_to"] == [ids["alice"]]
    r = oj_start(oj, carol, e["id"], e["start_code"])
    assert r.status_code == 404
    a = oj_start(oj, alice, e["id"], e["start_code"])
    assert a.status_code == 200, a.text
    a = a.json()
    # 90 min + 600 s extra, capped at the end time + her 600 s
    assert parse_iso(a["deadline_at"]) == fake.end + timedelta(seconds=600)
    aid = a["attempt_id"]

    # end time passes: waits for the largest extra time (600 s) + grace
    after_end = fake.end + timedelta(seconds=120)
    assert auto_close(oj, now=after_end) == []
    later = fake.end + timedelta(seconds=600) + closing.LATE_GRACE + timedelta(seconds=1)
    assert auto_close(oj, now=later) == [e["id"]]
    x = oj_exam(oj, teacher)
    assert x["status"] == "closed" and x["auto_closed"] is True
    at = attempt(oj, aid)
    assert at.status == "submitted" and at.auto_submitted
    assert deliveries(oj)[aid][0] == "pending"            # outbox row, like the teacher's Close
    assert auto_close(oj, now=later) == []
    # the source's end time has passed: re-opening is refused (changed there, not here)
    fake.end = utcnow() - timedelta(hours=1)
    fake.listing_updated = iso(utcnow())
    run(oj)
    r = oj.post(f"/api/exams/{e['id']}/open", headers=teacher)
    assert r.status_code == 409 and "DKU OJ" in r.json()["detail"]


async def _user_id(s, username):
    from app.models import User
    return (await s.execute(select(User.id).where(User.username == username))).scalar_one()


def test_package_carries_close_time(client, teacher, alice):
    closes = utcnow().replace(microsecond=0) + timedelta(minutes=10)
    e = new_exam(client, teacher, closes_at=iso(closes))
    e = client.post(f"/api/exams/{e['id']}/open", headers=teacher).json()
    from app.offline import decrypt_package
    pkg = client.get(f"/api/student/exams/{e['id']}/package", headers=alice).json()
    key = client.get(f"/api/student/exams/{e['id']}/package-key", headers=alice).json()["key"]
    plain = decrypt_package(pkg, e["start_code"], package_key=key)
    assert plain["closes_at"] == iso(closes)
