"""Outbox: leased claiming, 401/403/404 retry window, resend and listing, NetID rule."""
import asyncio
from datetime import timedelta

import httpx
from sqlalchemy import select, update

from app.integrations.dkuoj import client as ojclient
from app.integrations.dkuoj.delivery import claim_due, deliver_due
from app.integrations.dkuoj.source import SOURCE
from app.integrations.models import ExternalDelivery
from app.models import User
from app.security import hash_password
from app.timeutil import utcnow

from .conftest import login
from .test_dkuoj_bridge import db, deliveries, fake, oj, oj_exam, run, start  # noqa: F401


def handed_in(oj, fake, who="alice"):
    teacher = login(oj, "teacher")
    run(oj)
    e = oj.post(f"/api/exams/{oj_exam(oj, teacher)['id']}/open", headers=teacher).json()
    h = login(oj, who)
    a = start(oj, h, e["id"], e["start_code"]).json()
    r = oj.post(f"/api/student/attempts/{a['attempt_id']}/submit", json={"answers": {}, "client_seq": 1}, headers=h)
    assert r.status_code == 200
    return teacher, a["attempt_id"]


def row(oj, aid):
    async def q(s):
        d = (await s.execute(select(ExternalDelivery).where(ExternalDelivery.attempt_id == aid))).scalar_one()
        return {"id": d.id, "status": d.status, "tries": d.tries, "last_status": d.last_status,
                "last_error": d.last_error, "payload": d.payload, "uploads": d.uploads,
                "locked_until": d.locked_until}
    return db(oj, q)


def seed_rows(oj, n):
    async def q(s):
        now = utcnow()
        for i in range(n):
            s.add(ExternalDelivery(source="dkuoj", attempt_id=f"a_claim{i}", exam_id="e1", next_at=now,
                                   created_at=now))
        await s.commit()
    db(oj, q)


async def claim_rest(now):
    from app.db import get_database
    return await claim_due(get_database().sessionmaker, now, limit=50, worker="late")


def test_two_claimers_never_share_a_row(oj, fake):
    seed_rows(oj, 12)
    now = utcnow() + timedelta(seconds=1)

    async def go():
        from app.db import get_database
        sm = get_database().sessionmaker
        return await asyncio.gather(*(claim_due(sm, now, limit=8, worker=f"w{i}") for i in range(3)))
    got = oj.portal.call(go)
    flat = [i for g in got for i in g]
    assert len(flat) == len(set(flat))
    rest = oj.portal.call(claim_rest, now)
    assert sorted(flat + rest) == sorted(set(flat + rest)) and len(flat + rest) == 12


def test_lease_expires_for_a_crashed_worker(oj, fake):
    seed_rows(oj, 2)
    t = utcnow() + timedelta(seconds=1)

    async def claim(now):
        from app.db import get_database
        return await claim_due(get_database().sessionmaker, now, worker="w")
    assert len(oj.portal.call(claim, t)) == 2
    assert oj.portal.call(claim, t + timedelta(minutes=1)) == []
    assert len(oj.portal.call(claim, t + timedelta(minutes=6))) == 2


def test_concurrent_workers_send_a_hand_in_once(oj, fake):
    _, aid = handed_in(oj, fake)
    sent = []
    inner = fake.handle

    def counting(req):
        if req.method == "POST" and req.url.path.endswith("/submissions"):
            sent.append(req)
        return inner(req)
    ojclient.set_transport_factory(lambda base: httpx.MockTransport(counting))

    async def go():
        from app.db import get_database
        sm = get_database().sessionmaker
        cfg = SOURCE.config

        async def worker():
            async with ojclient.DkuojClient.from_config(cfg) as c:
                return await deliver_due(sm, c, cfg, now=utcnow() + timedelta(seconds=1))
        return await asyncio.gather(worker(), worker(), worker())
    counts = oj.portal.call(go)
    assert len(sent) == 1 and sum(c["sent"] for c in counts) == 1
    assert row(oj, aid)["status"] == "sent" and row(oj, aid)["locked_until"] is None


def test_auth_class_refusals_retry_for_six_hours_then_die(oj, fake):
    _, aid = handed_in(oj, fake)
    fake.submit_fail["alice"] = [401, 403, 404, 404]
    t0 = utcnow()
    run(oj, now=t0)
    r = row(oj, aid)
    assert r["status"] == "failed" and r["last_status"] == 401 and "401" in r["last_error"] and r["tries"] == 1
    run(oj, now=t0 + timedelta(hours=1))
    r = row(oj, aid)
    assert r["status"] == "failed" and r["last_status"] == 403
    run(oj, now=t0 + timedelta(hours=5))
    assert row(oj, aid)["status"] == "failed" and row(oj, aid)["last_status"] == 404
    run(oj, now=t0 + timedelta(hours=7))
    r = row(oj, aid)
    assert r["status"] == "dead" and r["last_status"] == 404 and "6 hours" in r["last_error"]


def test_auth_class_refusal_clears_and_sends(oj, fake):
    _, aid = handed_in(oj, fake)
    fake.submit_fail["alice"] = [401]
    t0 = utcnow()
    run(oj, now=t0)
    run(oj, now=t0 + timedelta(minutes=1))
    r = row(oj, aid)
    assert r["status"] == "sent" and r["last_status"] == 200 and r["last_error"] is None


def test_other_4xx_is_dead_at_once(oj, fake):
    _, aid = handed_in(oj, fake)
    fake.submit_fail["alice"] = [400]
    run(oj, now=utcnow())
    r = row(oj, aid)
    assert r["status"] == "dead" and r["last_status"] == 400 and r["tries"] == 1


def test_resend_keeps_payload_and_is_owner_only(oj, fake):
    teacher, aid = handed_in(oj, fake)
    fake.submit_fail["alice"] = [400]
    run(oj, now=utcnow())
    before = row(oj, aid)
    assert before["status"] == "dead" and before["payload"]

    async def make_teacher2(s):
        s.add(User(username="teacher2", name="Other", role="teacher", password_hash=hash_password("other-pass-123", 1000)))
        await s.commit()
    db(oj, make_teacher2)
    t2 = login(oj, "teacher2", "other-pass-123")
    alice = login(oj, "alice")
    url = f"/api/deliveries/{before['id']}/resend"
    assert oj.post(url, headers=t2).status_code == 404
    assert oj.post(url, headers=alice).status_code == 403
    assert oj.post(url).status_code in (401, 403)
    assert oj.post("/api/deliveries/999999/resend", headers=teacher).status_code == 404
    assert row(oj, aid)["status"] == "dead"

    dead = oj.get("/api/deliveries?status=dead", headers=teacher).json()["deliveries"]
    assert [d["attempt_id"] for d in dead] == [aid] and dead[0]["last_status"] == 400
    assert oj.get("/api/deliveries?status=dead", headers=t2).json() == {"deliveries": []}
    assert oj.get("/api/deliveries?status=sent", headers=teacher).status_code == 422
    assert oj.get("/api/deliveries?status=dead", headers=alice).status_code == 403

    r = oj.post(url, headers=teacher)
    assert r.status_code == 200 and r.json()["status"] == "pending" and r.json()["tries"] == 0
    after = row(oj, aid)
    assert after["payload"] == before["payload"] and after["uploads"] == before["uploads"]
    assert oj.get("/api/deliveries?status=pending", headers=teacher).json()["deliveries"][0]["attempt_id"] == aid
    assert oj.get("/api/deliveries?status=dead", headers=teacher).json() == {"deliveries": []}
    assert oj.get("/api/deliveries?status=pending", headers=t2).json() == {"deliveries": []}
    run(oj, now=utcnow() + timedelta(seconds=1))
    assert row(oj, aid)["status"] == "sent" and fake.submissions[aid]["attempt_id"] == aid
    assert oj.post(url, headers=teacher).status_code == 409
    assert row(oj, aid)["status"] == "sent"


def deliver(oj, now):
    async def go():
        from app.db import get_database
        cfg = SOURCE.config
        async with ojclient.DkuojClient.from_config(cfg) as c:
            return await deliver_due(get_database().sessionmaker, c, cfg, now=now)
    return oj.portal.call(go)


def test_no_netid_is_dead_and_not_sent(oj, fake):
    teacher, aid = handed_in(oj, fake)

    async def unlink(s):
        await s.execute(update(User).where(User.username == "alice").values(netid=None))
        await s.commit()
    db(oj, unlink)
    deliver(oj, utcnow() + timedelta(seconds=1))
    r = row(oj, aid)
    assert r["status"] == "dead" and "NetID" in r["last_error"]
    assert aid not in fake.submissions

    async def link(s):
        await s.execute(update(User).where(User.username == "alice").values(netid="alice"))
        await s.commit()
    db(oj, link)
    assert oj.post(f"/api/deliveries/{r['id']}/resend", headers=teacher).status_code == 200
    run(oj, now=utcnow() + timedelta(seconds=1))
    assert row(oj, aid)["status"] == "sent" and fake.submissions[aid]["username"] == "alice"


def test_results_are_not_matched_by_username_without_netid(oj, fake):
    teacher, aid = handed_in(oj, fake)
    from app.integrations.dkuoj.results import _sync_exam
    from app.timeutil import iso
    fake.results = [{"username": "alice", "released": True, "total": 82, "total_points": 100,
                     "updated_at": iso(utcnow()), "questions": []}]

    async def unlink(s):
        await s.execute(update(User).where(User.username == "alice").values(netid=None))
        await s.commit()
    db(oj, unlink)

    exam_id = oj_exam(oj, teacher)["id"]

    async def go():
        from app.db import get_database
        sm = get_database().sessionmaker
        async with ojclient.DkuojClient.from_config(SOURCE.config) as c:
            return await _sync_exam(sm, c, exam_id, "cs101a-midterm", None)
    assert oj.portal.call(go) == 0


def test_short_bridge_token_refuses_to_start():
    import pytest
    from app.integrations.dkuoj.config import DkuojConfig
    with pytest.raises(RuntimeError, match="under 32"):
        DkuojConfig(url="https://oj.example.edu", token="a" * 16).check()
    DkuojConfig(url="https://oj.example.edu", token="a" * 32).check()
