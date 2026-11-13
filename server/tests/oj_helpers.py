"""Small helpers shared by the DKU OJ tests (no access-code sign-in: that flow is gone)."""
from datetime import timedelta

from sqlalchemy import select, update

from app.integrations.dkuoj.models import ClassLoginEvent
from app.models import Exam
from app.timeutil import utcnow

from .test_dkuoj_bridge import KEY, db, run  # noqa: F401  (re-exported)

APP_KEY = "test-app-key"
DEV_A, DEV_B = "DEVICE-AAAA-0001", "DEVICE-BBBB-0002"


def patch_exam(c, **values):
    async def go(s):
        await s.execute(update(Exam).where(Exam.external_key == KEY).values(**values))
        await s.commit()
    db(c, go)


def soon(c, minutes=10):
    patch_exam(c, scheduled_at=utcnow() + timedelta(minutes=minutes))


def bearer(body):
    return {"Authorization": f"Bearer {body['token']}"}


def _events(kind):
    async def q(s):
        return [(e.netid, e.detail) for e in (await s.execute(
            select(ClassLoginEvent).where(ClassLoginEvent.kind == kind))).scalars()]
    return q


# -- signing a roster student in without the removed access code ------------------------------
# These tests used to type a per-student code; the "code" of a student is now just the NetID and
# sign_in() issues the exam-scoped student token directly, so the start/attempt/device rules that
# come after sign-in are still exercised exactly as before.
import pytest  # noqa: E402

from app import app_client as ac  # noqa: E402
from app.models import User  # noqa: E402
from app.security import make_token, remember_user  # noqa: E402

from .test_dkuoj_bridge_v2 import oj  # noqa: E402,F401  (fixture)


def codes_of(c):
    async def q(s):
        return {n: n for n in (await s.execute(select(User.netid).where(User.netid.is_not(None)))).scalars()}
    return db(c, q)


def sign_in(c, netid, device=None):
    async def go(s):
        u = (await s.execute(select(User).where(User.netid == netid))).scalar_one()
        e = (await s.execute(select(Exam).where(Exam.external_key == KEY))).scalar_one()
        await remember_user(u.id, u.role, int(u.token_version or 0))
        return make_token(c.app.state.settings.secret, u.id, u.role, c.app.state.settings.student_token_ttl_s,
                          version=u.token_version or 0, scope=e.id), u.name
    token, name = db(c, go)
    return {"token": token, "role": "student", "name": name, "resume": False}


def start(c, body, eid, device, start_code=""):
    if not start_code:
        async def code(s):
            return (await s.get(Exam, eid)).start_code
        start_code = db(c, code) or ""
    nonce = c.get(f"/api/student/exams/{eid}/challenge", headers=bearer(body)).json()["nonce"]
    client = {"device_id": device, "nonce": nonce, "signature": ac.sign(APP_KEY, ac.start_message(nonce, device))}
    return c.post(f"/api/student/exams/{eid}/start", json={"start_code": start_code, "client": client},
                  headers=bearer(body))


@pytest.fixture
def ready(oj):
    run(oj)
    soon(oj)
    return codes_of(oj)
