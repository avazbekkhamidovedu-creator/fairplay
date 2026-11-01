from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app import app_client as ac
from app.integrations.dkuoj import access_codes
from app.integrations.dkuoj.models import ClassLoginEvent, ExamAccessCode
from app.main import create_app
from app.models import Attempt, Exam, IntegrityEvent, User
from app.integrations.dkuoj.setup import make_kind_token
from app.timeutil import utcnow

from .conftest import login
from .test_access_codes import KEY, codes_of, db, run, secret
from .test_dkuoj_bridge import oj_exam, oj_settings
from .test_dkuoj_bridge_v2 import V1, call, fake, make_login, oj, set_up  # noqa: F401  (fixtures)

URL = "/api/student/code-login"
NOT_VALID = {"detail": "That code is not valid."}
APP_KEY = "test-app-key"
DEV_A, DEV_B = "DEVICE-AAAA-0001", "DEVICE-BBBB-0002"


def patch_exam(c, **values):
    async def go(s):
        await s.execute(update(Exam).where(Exam.external_key == KEY).values(**values))
        await s.commit()
    db(c, go)


def soon(c, minutes=10):
    patch_exam(c, scheduled_at=utcnow() + timedelta(minutes=minutes))


def proof(c, device):
    nonce = c.get(f"{URL}/challenge").json()["nonce"]
    return {"device_id": device, "nonce": nonce, "signature": ac.sign(APP_KEY, ac.start_message(nonce, device))}


def enter(c, code, device=None, **kw):
    body = {"code": code}
    if device:
        body["client"] = proof(c, device)
    return c.post(URL, json=body, **kw)


def confirm(c, tok):
    return c.post(f"{URL}/confirm", json={"confirm_token": tok})


def sign_in(c, code, device=None):
    r = enter(c, code, device)
    assert r.status_code == 200, r.text
    r = confirm(c, r.json()["confirm_token"])
    assert r.status_code == 200, r.text
    return r.json()


def bearer(body):
    return {"Authorization": f"Bearer {body['token']}"}


def _events(kind):
    async def q(s):
        return [(e.netid, e.detail) for e in (await s.execute(
            select(ClassLoginEvent).where(ClassLoginEvent.kind == kind))).scalars()]
    return q


@pytest.fixture
def ready(oj):
    run(oj)
    soon(oj)
    return codes_of(oj)


def test_right_code_confirms_with_names_then_signs_in(oj, ready):
    r = enter(oj, ready["ax19"].lower())
    assert r.status_code == 200, r.text
    body = r.json()
    assert {k: v for k, v in body.items() if k != "confirm_token"} == {
        "first_name": "Avazbek", "last_name": "Khamidov", "netid": "ax19", "exam_title": "CS 101 Midterm",
        "resume": False}
    assert r.headers["cache-control"] == "no-store"
    assert db(oj, lambda s: s.scalar(select(ExamAccessCode.used_at).where(ExamAccessCode.netid == "ax19"))) is None
    r = confirm(oj, body["confirm_token"])
    assert r.status_code == 200 and set(r.json()) == {"token", "role", "name", "resume"}
    assert r.json()["role"] == "student" and r.json()["name"] == "Avazbek Khamidov"
    assert oj.get("/api/student/exams", headers=bearer(r.json())).status_code == 200
    assert db(oj, lambda s: s.scalar(select(ExamAccessCode.used_at).where(ExamAccessCode.netid == "ax19")))
    assert [n for n, _ in db(oj, _events("access_code_signin"))] == ["ax19"]


def test_typing_is_forgiving(oj, ready):
    code = ready["carol"]
    for typed in (code.lower(), f" {code[:3]}-{code[3:]} ", f"{code[:2]} {code[2:4]}-{code[4:]}"):
        assert enter(oj, typed).status_code == 200, typed


def test_every_bad_code_gets_the_same_answer(oj, ready):
    wrong = "AAAAAA" if ready["alice"] != "AAAAAA" else "BBBBBB"
    cases = {"wrong": wrong, "malformed": "12", "empty": "", "long": "A" * 40}

    async def revoke(s):
        await s.execute(update(ExamAccessCode).where(ExamAccessCode.netid == "carol")
                        .values(revoked_at=utcnow()))
        await s.commit()
    db(oj, revoke)
    cases["revoked"] = ready["carol"]
    answers = {}
    for name, code in cases.items():
        r = enter(oj, code)
        answers[name] = (r.status_code, r.json())
    assert all(v == (400, NOT_VALID) for v in answers.values()), answers

    patch_exam(oj, status="closed")
    r = enter(oj, ready["alice"])
    assert (r.status_code, r.json()) == (400, NOT_VALID)


def test_too_early_code_says_when_the_exam_starts(oj, ready):
    start = (utcnow() + timedelta(hours=2)).replace(microsecond=0)
    patch_exam(oj, scheduled_at=start)
    r = enter(oj, ready["alice"], DEV_A)
    assert r.status_code == 400
    assert r.json() == {"detail": "too_early", "starts_at": start.isoformat(timespec="milliseconds") + "Z"}
    assert r.headers["cache-control"] == "no-store"
    assert db(oj, lambda s: s.scalar(select(ExamAccessCode.used_at).where(ExamAccessCode.netid == "alice"))) is None
    audit = db(oj, _events("access_code_failed"))
    assert audit and all(n == "alice" and d.endswith(": outside_window") for n, d in audit)
    soon(oj, 29)
    assert enter(oj, ready["alice"], DEV_A).status_code == 200


def test_too_early_is_counted_like_a_failure(oj, ready):
    patch_exam(oj, scheduled_at=utcnow() + timedelta(hours=2))
    for _ in range(5):
        assert enter(oj, ready["alice"], DEV_A).json()["detail"] == "too_early"
    r = enter(oj, ready["alice"], DEV_A)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert enter(oj, ready["alice"], DEV_B).json()["detail"] == "too_early"


def test_too_early_has_no_name_or_netid(oj, ready):
    patch_exam(oj, scheduled_at=utcnow() + timedelta(hours=2))
    r = enter(oj, ready["alice"], DEV_A)
    assert set(r.json()) == {"detail", "starts_at"}
    assert "alice" not in r.text.lower()


def test_too_early_on_another_device_is_generic_and_flagged(oj, ready):
    assert sign_in(oj, ready["alice"], DEV_A)
    patch_exam(oj, scheduled_at=utcnow() + timedelta(hours=2), status="draft")
    r = enter(oj, ready["alice"], DEV_B)
    assert (r.status_code, r.json()) == (400, NOT_VALID)
    assert db(oj, _events("access_code_second_device"))
    assert enter(oj, ready["alice"], DEV_A).json()["detail"] == "too_early"


def test_only_a_valid_code_hears_the_start_time(oj, ready):
    patch_exam(oj, scheduled_at=utcnow() + timedelta(hours=2))

    async def revoke(s):
        await s.execute(update(ExamAccessCode).where(ExamAccessCode.netid == "carol").values(revoked_at=utcnow()))
        await s.commit()
    db(oj, revoke)
    for code in ("AAAAAA", "12", ready["carol"]):
        r = enter(oj, code)
        assert (r.status_code, r.json()) == (400, NOT_VALID), code
    patch_exam(oj, scheduled_at=None)
    r = enter(oj, ready["alice"])
    assert (r.status_code, r.json()) == (400, NOT_VALID)
    patch_exam(oj, scheduled_at=utcnow() - timedelta(days=2), status="closed")
    r = enter(oj, ready["alice"])
    assert (r.status_code, r.json()) == (400, NOT_VALID)
    patch_exam(oj, scheduled_at=utcnow() + timedelta(hours=2), status="live")
    assert enter(oj, ready["alice"]).status_code == 200


def test_code_of_another_exam_is_refused(oj, ready):
    async def second(s):
        e = (await s.execute(select(Exam).where(Exam.external_key == KEY))).scalar_one()
        other = Exam(id="e_other", teacher_id=e.teacher_id, title="Other", duration_min=10, status="live",
                     questions=[], question_count=0, total_points=0, source=e.source)
        s.add(other)
        await s.flush()
        s.add(ExamAccessCode(exam_id="e_other", netid="alice", first_name="A", last_name="C",
                             code_hmac=access_codes.code_hmac(secret(oj), "ZZZZZZ"), code_sealed="x",
                             created_at=utcnow()))
        await s.commit()
    db(oj, second)
    r = enter(oj, "ZZZZZZ")
    assert r.status_code == 200 and r.json()["exam_title"] == "Other"
    async def close(s):
        await s.execute(update(Exam).values(status="closed"))
        await s.commit()
    db(oj, close)
    assert enter(oj, "ZZZZZZ").json() == NOT_VALID


def test_failures_are_limited_per_device_with_retry_after(oj, ready):
    for _ in range(5):
        assert enter(oj, "AAAAAA", DEV_A).status_code == 400
    r = enter(oj, "AAAAAA", DEV_A)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert r.json()["detail"]["code"] == "too_many_codes"
    assert enter(oj, ready["alice"], DEV_A).status_code == 429       # even the right code waits
    assert enter(oj, ready["alice"], DEV_B).status_code == 200       # another device is not locked


def test_failures_are_limited_per_ip(oj, ready):
    oj.app.state.settings.codelogin_ip_fails = 30
    for _ in range(30):
        assert enter(oj, "AAAAAA").status_code == 400
    r = enter(oj, ready["alice"])
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1


def test_right_codes_do_not_count_as_failures(oj, ready):
    for _ in range(40):
        assert enter(oj, ready["alice"], DEV_A).status_code == 200


def test_confirm_token_is_single_use_and_expires(oj, ready):
    tok = enter(oj, ready["alice"]).json()["confirm_token"]
    assert confirm(oj, tok).status_code == 200
    again = confirm(oj, tok)
    assert again.status_code == 400 and "expired" in again.json()["detail"]
    s = oj.app.state.settings.secret
    old = make_kind_token(s, "codeconfirm", {"row": 1, "dev": "", "bound": ""}, 300, now=utcnow().timestamp() - 400)
    assert confirm(oj, old).status_code == 400
    assert confirm(oj, "junk").status_code == 400
    setup_kind = make_kind_token(s, "setup", {"row": 1}, 300)
    assert confirm(oj, setup_kind).status_code == 400


def test_confirm_checks_the_code_again(oj, ready):
    tok = enter(oj, ready["alice"]).json()["confirm_token"]
    async def commit(s):
        await s.execute(update(ExamAccessCode).values(revoked_at=utcnow()))
        await s.commit()
    db(oj, commit)
    r = confirm(oj, tok)
    assert (r.status_code, r.json()) == (400, NOT_VALID)


def test_account_made_by_a_code_has_no_password(oj, ready):
    assert db(oj, lambda s: s.scalar(select(User.id).where(User.netid == "ax19"))) is not None
    sign_in(oj, ready["ax19"])
    u = db(oj, lambda s: s.scalar(select(User).where(User.netid == "ax19")))
    assert not u.password_hash.startswith("pbkdf2_")
    for name in (u.username, "ax19", "oj:ax19"):
        for pw in ("!dkuoj-import", "ax19", "", "password", ready["ax19"]):
            assert oj.post("/api/auth/login", json={"username": name, "password": pw}).status_code == 401


def test_a_new_netid_gets_an_account_without_a_password(oj, ready):
    async def add(s):
        from app.integrations.dkuoj.models import OjRosterEntry
        u = User(username="oj:zz9", name="Zed Nine", role="student", password_hash="!dkuoj-import",
                 netid="zz9", created_at=utcnow())
        e = (await s.execute(select(Exam).where(Exam.external_key == KEY))).scalar_one()
        s.add(u)
        await s.flush()
        s.add(OjRosterEntry(exam_id=e.id, user_id=u.id, first_name="Zed", last_name="Nine"))
        await s.commit()
    db(oj, add)
    assert codes_of(oj)["zz9"]
    body = sign_in(oj, codes_of(oj)["zz9"])
    assert body["name"] == "Zed Nine"
    assert oj.post("/api/auth/login", json={"username": "oj:zz9", "password": "!dkuoj-import"}).status_code == 401


def test_a_second_device_is_refused_and_flagged(oj, ready):
    sign_in(oj, ready["alice"], DEV_A)
    bound = db(oj, lambda s: s.scalar(select(ExamAccessCode.bound_device).where(ExamAccessCode.netid == "alice")))
    assert bound == DEV_A
    r = enter(oj, ready["alice"], DEV_B)
    assert (r.status_code, r.json()) == (400, NOT_VALID)
    flagged = db(oj, _events("access_code_second_device"))
    assert len(flagged) == 1 and flagged[0][0] == "alice" and ready["alice"] not in flagged[0][1]
    assert enter(oj, ready["alice"], DEV_A).status_code == 200       # app restart on the same device
    sign_in(oj, ready["alice"], DEV_A)


def test_two_devices_confirming_at_once_only_one_binds(oj, ready):
    ta = enter(oj, ready["carol"], DEV_A).json()["confirm_token"]
    tb = enter(oj, ready["carol"], DEV_B).json()["confirm_token"]
    assert confirm(oj, ta).status_code == 200
    assert confirm(oj, tb).status_code == 400


def test_second_device_is_flagged_on_the_attempt_too(oj, ready):
    teacher = login(oj, "teacher")
    eid = oj_exam(oj, teacher)["id"]
    oj.post(f"/api/exams/{eid}/open", headers=teacher)
    body = sign_in(oj, ready["alice"], DEV_A)
    started = start(oj, body, eid, DEV_A)
    assert started.status_code == 200, started.text
    assert enter(oj, ready["alice"], DEV_B).status_code == 400

    async def q(s):
        return (await s.execute(select(IntegrityEvent.type).where(
            IntegrityEvent.type == "access_code_second_device"))).scalars().all()
    assert db(oj, q) == ["access_code_second_device"]
    assert oj.post(f"/api/exams/{eid}/attempts/{started.json()['attempt_id']}/release-device",
                   headers=teacher).status_code == 200
    assert enter(oj, ready["alice"], DEV_B).status_code == 200          # the teacher freed it


def start(c, body, eid, device, start_code=""):
    nonce = c.get(f"/api/student/exams/{eid}/challenge", headers=bearer(body)).json()["nonce"]
    client = {"device_id": device, "nonce": nonce, "signature": ac.sign(APP_KEY, ac.start_message(nonce, device))}
    return c.post(f"/api/student/exams/{eid}/start", json={"start_code": start_code, "client": client},
                  headers=bearer(body))


@pytest.mark.parametrize("fields, ok", [
    ({"scheduled_at": "plus", "minutes": 45}, False),
    ({"scheduled_at": "plus", "minutes": 25}, True),
    ({"scheduled_at": None}, False),
    ({"scheduled_at": None, "status": "live"}, True),
    ({"scheduled_at": "plus", "minutes": -600, "status": "live"}, True),
    ({"scheduled_at": "plus", "minutes": 5, "status": "closed"}, False),
    ({"scheduled_at": "plus", "minutes": 5, "status": "live", "closes_at": "past"}, False),
])
def test_code_window(oj, fields, ok):
    run(oj)
    code = codes_of(oj)["alice"]
    values = {k: v for k, v in fields.items() if k != "minutes"}
    if values.get("scheduled_at") == "plus":
        values["scheduled_at"] = utcnow() + timedelta(minutes=fields["minutes"])
    if values.get("closes_at") == "past":
        values.pop("closes_at")
        meta = db(oj, lambda s: s.scalar(select(Exam.external_meta).where(Exam.external_key == KEY)))
        values["external_meta"] = {**meta, "end_time": (utcnow() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")}
    patch_exam(oj, **values)
    r = enter(oj, code)
    assert r.status_code == (200 if ok else 400), r.text
    if not ok:
        too_early = fields.get("scheduled_at") == "plus" and fields["minutes"] > 30 and "status" not in fields
        assert r.json() == ({"detail": "too_early", "starts_at": r.json().get("starts_at")} if too_early else NOT_VALID)


def test_start_needs_no_start_code_for_the_bound_device_and_only_when_live(oj, ready):
    teacher = login(oj, "teacher")
    eid = oj_exam(oj, teacher)["id"]
    body = sign_in(oj, ready["alice"], DEV_A)

    waiting = [x for x in oj.get("/api/student/exams", headers=bearer(body)).json() if x["id"] == eid]
    assert len(waiting) == 1 and waiting[0]["status"] == "draft"
    assert waiting[0]["access_code"] is True and waiting[0]["question_count"] is None
    assert waiting[0]["package_id"] is None
    r = start(oj, body, eid, DEV_A)
    assert r.status_code == 409 and r.json()["detail"] == "Exam is not open"

    opened = oj.post(f"/api/exams/{eid}/open", headers=teacher).json()
    live = [x for x in oj.get("/api/student/exams", headers=bearer(body)).json() if x["id"] == eid][0]
    assert live["status"] == "live" and live["package_id"] is None and live["access_code"] is True
    assert oj.get(f"/api/student/exams/{eid}/package", headers=bearer(body)).status_code == 409
    assert oj.get(f"/api/student/exams/{eid}/package-key", headers=bearer(body)).status_code == 409

    other = start(oj, body, eid, DEV_B)
    assert other.status_code == 403 and other.json()["detail"] == "Wrong start code"
    assert start(oj, body, eid, DEV_B, opened["start_code"]).status_code == 200      # the room's code still works


def test_start_for_code_student_on_bound_device(oj, ready):
    teacher = login(oj, "teacher")
    eid = oj_exam(oj, teacher)["id"]
    oj.post(f"/api/exams/{eid}/open", headers=teacher)
    body = sign_in(oj, ready["carol"], DEV_A)
    r = start(oj, body, eid, DEV_A)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "in_progress"

    async def q(s):
        return (await s.execute(select(Attempt.device_id))).scalars().all()
    assert db(oj, q) == [DEV_A]


def test_password_student_still_needs_the_start_code(oj, ready):
    teacher = login(oj, "teacher")
    eid = oj_exam(oj, teacher)["id"]
    oj.post(f"/api/exams/{eid}/open", headers=teacher)
    alice = {"token": login(oj, "alice")["Authorization"].split()[1]}
    r = start(oj, alice, eid, DEV_A)
    assert r.status_code == 403 and r.json()["detail"] == "Wrong start code"


def test_revoked_code_stops_the_start_too(oj, ready):
    teacher = login(oj, "teacher")
    eid = oj_exam(oj, teacher)["id"]
    oj.post(f"/api/exams/{eid}/open", headers=teacher)
    body = sign_in(oj, ready["alice"], DEV_A)

    async def revoke(s):
        await s.execute(update(ExamAccessCode).values(revoked_at=utcnow()))
        await s.commit()
    db(oj, revoke)
    assert start(oj, body, eid, DEV_A).status_code == 403


def test_the_class_login_flow_is_unchanged(oj):
    run(oj)
    cl = make_login(oj)
    out = set_up(oj, cl)
    assert out["role"] == "student" and out["token"]
    again = oj.post("/api/auth/login", json={"username": "bobli", "password": "Correct-horse-9"})
    assert again.status_code == 200 and again.json()["role"] == "student"


def test_the_app_only_gate_covers_the_code_endpoints(tmp_path, static_dir, fake):
    with TestClient(create_app(oj_settings(tmp_path, static_dir, browser_gate="headers"))) as c:
        browser = {"User-Agent": "Mozilla/5.0 Chrome/130.0 Safari/537.36"}
        app = {"X-FairPlay-Version": "0.7.2", "X-FairPlay-Platform": "mac"}
        for method, path in (("GET", f"{URL}/challenge"), ("POST", URL), ("POST", f"{URL}/confirm")):
            r = c.request(method, path, headers=browser, json={})
            assert (r.status_code, r.json()) == (404, {"detail": "Not Found"}), path
            assert c.request(method, path, headers=app, json={}).status_code != 404, path


def test_network_of_groups_ipv6_by_64():
    from app.integrations.dkuoj.code_login import network_of
    assert network_of("2001:db8:1:2:aaaa:bbbb:cccc:dddd") == network_of("2001:db8:1:2::1") == "2001:db8:1:2::/64"
    assert network_of("2001:db8:1:3::1") != network_of("2001:db8:1:2::1")
    assert network_of("::ffff:10.1.2.3") == "10.1.2.3"
    assert network_of("10.1.2.3") == "10.1.2.3" and network_of("testclient") == "testclient"


def test_ipv6_64_shares_one_failure_limit(oj, ready, monkeypatch):
    from app.integrations.dkuoj import code_login
    oj.app.state.settings.codelogin_ip_fails = 30
    n = iter(range(1, 1000))
    monkeypatch.setattr(code_login, "client_ip", lambda request: f"2001:db8:5:6:{next(n):x}::1")
    for _ in range(30):
        assert enter(oj, "AAAAAA").status_code == 400
    assert enter(oj, ready["alice"]).status_code == 429
    monkeypatch.setattr(code_login, "client_ip", lambda request: "2001:db8:7:7::1")
    assert enter(oj, ready["alice"]).status_code == 200


def test_global_failure_budget_spans_all_addresses(oj, ready, monkeypatch):
    from app.integrations.dkuoj import code_login
    monkeypatch.setattr(code_login, "GLOBAL_LIMITS", ((12, 600, 60), (50, 3600, 300)))
    n = iter(range(1, 1000))
    monkeypatch.setattr(code_login, "client_ip", lambda request: f"10.0.{next(n) // 250}.{next(n) % 250}")
    for _ in range(12):
        assert enter(oj, "AAAAAA").status_code == 400
    r = enter(oj, ready["alice"])
    assert r.status_code == 429 and 1 <= int(r.headers["Retry-After"]) <= 600
    assert r.json()["detail"]["code"] == "too_many_codes"


def test_a_big_class_with_typos_stays_under_the_global_budget(oj, ready, monkeypatch):
    from app.integrations.dkuoj import code_login
    n = iter(range(1, 100000))
    monkeypatch.setattr(code_login, "client_ip", lambda request: f"10.{next(n) % 250}.{next(n) % 250}.1")
    for _ in range(40):
        assert enter(oj, "AAAAAA").status_code == 400
    assert enter(oj, ready["alice"]).status_code == 200
    assert code_login.GLOBAL_LIMITS[0][0] >= 300 and code_login.GLOBAL_LIMITS[1][0] >= 1000


def test_global_window_slides(oj, ready, monkeypatch):
    import asyncio
    from app.integrations.dkuoj import code_login
    from app.store import MemoryStore
    monkeypatch.setattr(code_login, "GLOBAL_LIMITS", ((3, 600, 60),))
    store = MemoryStore()

    async def go():
        t = [1_000_000.0]
        monkeypatch.setattr(code_login.time, "time", lambda: t[0])
        for _ in range(3):
            await code_login._count_failure(store, [])
        assert await code_login._global_wait(store, t[0]) >= 1
        t[0] += 601
        assert await code_login._global_wait(store, t[0]) == 0
    asyncio.run(go())


def test_confirm_token_dies_when_the_code_is_reissued(oj, ready):
    tok = enter(oj, ready["alice"], DEV_A).json()["confirm_token"]

    async def reissue(s):
        e = (await s.execute(select(Exam).where(Exam.external_key == KEY))).scalar_one()
        await access_codes.reissue(s, secret(oj), e, "alice")
    db(oj, reissue)
    assert confirm(oj, tok).status_code == 400
    bound = db(oj, lambda s: s.scalar(select(ExamAccessCode.bound_device).where(ExamAccessCode.netid == "alice")))
    assert bound is None


def test_one_network_cannot_fill_the_global_failure_window():
    from app.integrations.dkuoj import code_login as cl
    limit, window, _ = cl.GLOBAL_LIMITS[0]
    from app.config import Settings
    assert Settings().codelogin_ip_fails * (window // cl.IP_WINDOW_S) * 10 <= limit
