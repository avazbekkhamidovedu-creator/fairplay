"""Pilot 0.8.8: an attempt whose own deadline passed long ago is handed in by the periodic sweep."""
from datetime import timedelta

from sqlalchemy import select, update

from app import closing
from app.db import get_database
from app.models import Attempt, IntegrityEvent
from app.timeutil import utcnow

from .conftest import login
from .test_app_session import app_client, exam, start  # noqa: F401  (fixtures)


def _run(c, fn):
    async def go():
        async with get_database().sessionmaker() as s:
            return await fn(s)
    return c.portal.call(go)


def _set_deadline(c, aid, minutes_ago):
    async def go(s):
        await s.execute(update(Attempt).where(Attempt.id == aid).values(
            deadline_at=utcnow() - timedelta(minutes=minutes_ago),
            started_at=utcnow() - timedelta(minutes=minutes_ago + 30)))
        await s.commit()
    _run(c, go)


def _state(c, aid):
    async def go(s):
        a = (await s.execute(select(Attempt).where(Attempt.id == aid))).scalar_one()
        evs = (await s.execute(select(IntegrityEvent.type).where(IntegrityEvent.attempt_id == aid))).scalars().all()
        return a.status, a.auto_submitted, a.late, a.receipt, list(evs)
    return _run(c, go)


def test_attempt_past_its_own_deadline_is_handed_in(app_client, exam):
    h = login(app_client, "alice")
    a = start(app_client, h, exam).json()
    aid = a["attempt_id"]
    # still inside the wait (deadline passed 5 min ago): nothing happens
    _set_deadline(app_client, aid, 5)
    assert app_client.portal.call(lambda: closing.run_once(get_database().sessionmaker)) == []
    assert _state(app_client, aid)[0] == "in_progress"
    # long past: handed in, marked auto-submitted and late, the teacher sees an event
    _set_deadline(app_client, aid, 40)
    app_client.portal.call(lambda: closing.run_once(get_database().sessionmaker))
    status, auto, late, receipt, evs = _state(app_client, aid)
    assert status == "submitted" and auto is True and late is True and receipt
    assert closing.DEADLINE_EVENT in evs
    # the exam itself is untouched and a second pass does nothing more
    app_client.portal.call(lambda: closing.run_once(get_database().sessionmaker))
    assert _state(app_client, aid)[4].count(closing.DEADLINE_EVENT) == 1


def test_attempt_inside_its_time_is_never_touched(app_client, exam):
    h = login(app_client, "bob")
    aid = start(app_client, h, exam).json()["attempt_id"]
    app_client.portal.call(lambda: closing.run_once(get_database().sessionmaker))
    assert _state(app_client, aid)[0] == "in_progress"
