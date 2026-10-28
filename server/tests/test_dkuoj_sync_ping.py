"""POST /api/integrations/dkuoj/v1/sync: a saved DKU OJ assignment shows up in FairPlay
without waiting for the periodic loop. Against a FAKE DKU OJ (httpx.MockTransport)."""
import time

import pytest
from sqlalchemy import select

from app.integrations.dkuoj import guard, ping
from app.models import Exam

from .test_dkuoj_bridge import KEY
from .test_dkuoj_bridge_v2 import V1, call, fake, oj  # noqa: F401

SYNC = f"{V1}/sync"


def exams(c):
    async def q(s):
        return (await s.execute(select(Exam).where(Exam.external_key == KEY, Exam.source == "dkuoj"))).scalars().all()
    from .test_dkuoj_bridge import db
    return db(c, q)


def wait_for(fn, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        got = fn()
        if got:
            return got
        time.sleep(0.05)
    return fn()


def test_ping_creates_the_exam_without_the_loop(oj, fake):
    assert exams(oj) == []
    r = call(oj, "POST", SYNC, {"exam_key": KEY})
    assert r.status_code == 200 and r.json() == {"ok": True, "queued": False}
    found = wait_for(lambda: exams(oj))
    assert len(found) == 1 and found[0].title == "CS 101 Midterm"


def test_repeated_pings_do_not_duplicate(oj, fake):
    for _ in range(3):
        assert call(oj, "POST", SYNC, {"exam_key": KEY}).status_code == 200
        wait_for(lambda: exams(oj))
        time.sleep(0.3)
    assert len(exams(oj)) == 1
    assert call(oj, "POST", SYNC, {}, acting="").status_code == 200
    time.sleep(0.5)
    assert len(exams(oj)) == 1


def test_package_is_applied_inside_the_lead_window(oj, fake):
    from datetime import timedelta
    from app.timeutil import utcnow
    fake.start, fake.end = utcnow() + timedelta(hours=2), utcnow() + timedelta(hours=4)
    call(oj, "POST", SYNC, {"exam_key": KEY})
    got = wait_for(lambda: [e for e in exams(oj) if e.questions])
    assert got and got[0].question_count == 7


def test_ping_while_the_exam_is_busy_queues_a_follow_up(oj, fake):
    assert oj.portal.call(guard.take, KEY, "loop") is True
    r = call(oj, "POST", SYNC, {"exam_key": KEY})
    assert r.status_code == 200 and r.json() == {"ok": True, "queued": True}
    time.sleep(0.3)
    assert exams(oj) == []

    async def loop_lets_go():
        if await guard.release(KEY, "loop"):
            await ping.start(KEY)
    oj.portal.call(loop_lets_go)
    assert len(wait_for(lambda: exams(oj))) == 1


def test_rate_limit_total_and_per_exam(oj, fake):
    for _ in range(ping.PER_MINUTE_EXAM):
        assert call(oj, "POST", SYNC, {"exam_key": KEY}).status_code == 200
    r = call(oj, "POST", SYNC, {"exam_key": KEY})
    assert r.status_code == 429 and r.headers["retry-after"] == "60"
    for i in range(ping.PER_MINUTE - ping.PER_MINUTE_EXAM - 1):     # the refused ping counted too
        assert call(oj, "POST", SYNC, {"exam_key": f"other-{i}"}).status_code == 200
    assert call(oj, "POST", SYNC, {"exam_key": "one-more"}).status_code == 429


def test_unsigned_and_bad_signature_are_401(oj, fake):
    assert oj.post(SYNC, json={"exam_key": KEY}).status_code == 401
    assert call(oj, "POST", SYNC, {"exam_key": KEY}, token="x" * 40).status_code == 401
    assert exams(oj) == []


@pytest.mark.parametrize("body", [{"exam_key": 5}, {"exam_key": ""}, {"class_id": "31"}, {"class_id": 0},
                                  {"class_id": True}, {"exam_key": KEY, "extra": 1}, {"exam_key": "k" * 201}])
def test_bad_body_is_422(oj, fake, body):
    assert call(oj, "POST", SYNC, body).status_code == 422


def test_bad_body_does_not_count_against_the_limit(oj, fake):
    for _ in range(ping.PER_MINUTE + 2):
        assert call(oj, "POST", SYNC, {"nope": 1}).status_code == 422
    assert call(oj, "POST", SYNC, {"exam_key": KEY}).status_code == 200


def test_ping_is_404_when_the_bridge_is_off(tmp_path, static_dir, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from .test_dkuoj_bridge import oj_settings
    monkeypatch.delenv("DKUOJ_URL", raising=False)
    monkeypatch.delenv("DKUOJ_TOKEN", raising=False)
    with TestClient(create_app(oj_settings(tmp_path, static_dir))) as c:
        assert call(c, "POST", SYNC, {"exam_key": KEY}).status_code == 404


def test_default_sync_interval_is_30_seconds(monkeypatch):
    from app.integrations.dkuoj.config import DkuojConfig
    monkeypatch.delenv("DKUOJ_SYNC_SECONDS", raising=False)
    assert DkuojConfig.from_env().sync_seconds == 30
    monkeypatch.setenv("DKUOJ_SYNC_SECONDS", "90")
    assert DkuojConfig.from_env().sync_seconds == 90
