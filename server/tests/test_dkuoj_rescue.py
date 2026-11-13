"""Professor rescue actions from DKU OJ (routes_rescue): release a device,
reopen a handed-in attempt, add time for one student, offline exit code. Against a FAKE DKU OJ."""
import logging
import json
import sqlite3
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
import pytest
from sqlalchemy import create_engine, delete, select, text, update

from app.integrations import outbox
from app.integrations.dkuoj import routes_rescue as rescue
from app.integrations.dkuoj.models import ClassLoginEvent, OjEditor, OjStudentGrant
from app.integrations.models import ExternalDelivery
from app.models import Attempt, Exam, IntegrityEvent, User
from app.timeutil import iso, parse_iso, utcnow

from .conftest import login
from .oj_helpers import DEV_A, DEV_B, bearer, sign_in, soon, start
from .oj_helpers import KEY, codes_of
from .test_dkuoj_bridge import db, oj_exam, run
from .test_dkuoj_bridge_v2 import RosterOJ, V1, call, oj  # noqa: F401  (fixtures)

STU = f"{V1}/exams/{KEY}/students"


class RevisionOJ(RosterOJ):
    """Keeps every submission body (the plain fake refuses a second body for one attempt)."""

    def __init__(self):
        super().__init__()
        self.bodies: list[dict] = []

    def handle(self, req):
        if urlsplit(str(req.url)).path == f"/api/fairplay/v1/exams/{KEY}/submissions":
            body = json.loads(req.content)
            supersedes = body.get("supersedes")
            if supersedes is not None and (not isinstance(supersedes, str) or len(supersedes) > 128):
                return httpx.Response(422, json={"detail": "supersedes must be a string of at most 128 characters."})
            fails = self.submit_fail.get(body["username"]) or []
            if fails:
                return httpx.Response(fails.pop(0), json={"detail": "busy"})
            self.bodies.append(body)
            return httpx.Response(200, json={"receipt": f"oj_rcpt_{body['attempt_id']}_{body.get('revision', 1)}",
                                             "submissions": []})
        return super().handle(req)


@pytest.fixture
def fake(monkeypatch):
    from .test_dkuoj_bridge import OJ, TOKEN, ojclient, ojsync
    f = RevisionOJ()
    monkeypatch.setenv("DKUOJ_URL", OJ)
    monkeypatch.setenv("DKUOJ_TOKEN", TOKEN)
    monkeypatch.setenv("DKUOJ_INSTALLATION_ID", "626c995f-840b-4ddc-9774-df8b5cf10df8")
    monkeypatch.setenv("DKUOJ_CONFIRM_EXISTING_INSTALLATION_ID", "626c995f-840b-4ddc-9774-df8b5cf10df8")
    monkeypatch.setenv("FAIRPLAY_CLASS_LOGIN", "1")
    monkeypatch.setattr(ojsync, "BACKGROUND", False)
    ojclient.set_transport_factory(lambda base: httpx.MockTransport(f.signed_handle))

    async def no_sleep(_s):
        return None
    ojclient.set_sleep(no_sleep)
    yield f
    ojclient.set_transport_factory(None)
    ojclient.set_sleep(None)


@pytest.fixture
def room(oj):
    """The exam is synced, live, codes made. Returns (exam id, codes, start code)."""
    run(oj)
    soon(oj)
    codes = codes_of(oj)
    teacher = login(oj, "teacher")
    eid = oj_exam(oj, teacher)["id"]
    opened = oj.post(f"/api/exams/{eid}/open", headers=teacher).json()
    return eid, codes, opened["start_code"], teacher


def attempts_of(c, eid=None):
    async def q(s):
        return (await s.execute(select(Attempt))).scalars().all()
    return db(c, q)


def started(c, body, eid, device):
    r = start(c, body, eid, device)
    assert r.status_code == 200, r.text
    return r.json()


def put(c, body, a, answers, seq):
    h = {**bearer(body), "X-FairPlay-Session": a["session"]}
    r = c.put(f"/api/student/attempts/{a['attempt_id']}/answers", json={"answers": answers, "client_seq": seq},
              headers=h)
    assert r.status_code == 200, r.text


def submit(c, body, a, answers, seq):
    h = {**bearer(body), "X-FairPlay-Session": a["session"]}
    r = c.post(f"/api/student/attempts/{a['attempt_id']}/submit", json={"answers": answers, "client_seq": seq},
               headers=h)
    assert r.status_code == 200, r.text
    return r.json()["receipt"]


def qid_of(a, label="Q5"):
    return next(q["id"] for q in a["questions"] if q["label"] == label)


def kinds(c, model, kind):
    async def q(s):
        col = model.kind if model is ClassLoginEvent else model.type
        return [(e.netid, e.actor, e.detail) if model is ClassLoginEvent else e.detail
                for e in (await s.execute(select(model).where(col == kind))).scalars()]
    return db(c, q)


def view(c):
    r = call(c, "GET", f"{V1}/exams/{KEY}")
    assert r.status_code == 200, r.text
    return {s["netid"]: s for s in r.json()["students"]}


# ---------------------------------------------------------------- release-device
def test_release_device_then_resume_on_a_second_device(oj, room):
    eid, codes, _, _ = room
    body = sign_in(oj, codes["alice"], DEV_A)
    assert body["resume"] is False
    a = started(oj, body, eid, DEV_A)
    q5 = qid_of(a)
    put(oj, body, a, {q5: "kept answer"}, 1)
    assert view(oj)["alice"]["code_state"] == "bound"
    deadline = attempts_of(oj)[0].deadline_at

    r = call(oj, "POST", f"{STU}/alice/release-device", {"reason": "laptop died"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "released": "attempt"} and r.headers["cache-control"] == "no-store"
    mine = [x for x in attempts_of(oj) if x.id == a["attempt_id"]][0]
    assert mine.device_id is None and mine.status == "in_progress" and mine.answers == {q5: "kept answer"}
    assert mine.deadline_at == deadline
    assert view(oj)["alice"]["code_state"] == "unused"

    stale = oj.put(f"/api/student/attempts/{a['attempt_id']}/answers", json={"answers": {q5: "old"}, "client_seq": 2},
                   headers={**bearer(body), "X-FairPlay-Session": a["session"]})
    assert stale.status_code == 409

    body2 = sign_in(oj, codes["alice"], DEV_B)
    b = started(oj, body2, eid, DEV_B)
    assert b["attempt_id"] == a["attempt_id"] and b["answers"] == {q5: "kept answer"}
    assert b["session"] != a["session"] and b["needs_approval"] is False
    assert len(attempts_of(oj)) == 1
    assert attempts_of(oj)[0].device_id == DEV_B

    cls = kinds(oj, ClassLoginEvent, "device_released_oj")
    assert len(cls) == 1 and cls[0][0] == "alice" and cls[0][1] == "teacher" and "laptop died" in cls[0][2]
    att = kinds(oj, IntegrityEvent, "device_released_oj")
    assert len(att) == 1 and "teacher" in att[0] and "laptop died" in att[0]


def test_same_device_restart_is_a_resume(oj, room):
    eid, codes, _, _ = room
    body = sign_in(oj, codes["carol"], DEV_A)
    a = started(oj, body, eid, DEV_A)
    assert started(oj, sign_in(oj, codes["carol"], DEV_A), eid, DEV_A)["attempt_id"] == a["attempt_id"]
    assert len(attempts_of(oj)) == 1


def test_release_without_an_attempt_changes_nothing(oj, room):
    eid, codes, _, _ = room
    r = call(oj, "POST", f"{STU}/carol/release-device", {"reason": "wrong laptop"})
    assert r.json() == {"ok": True, "released": "code"}
    assert attempts_of(oj) == []


def test_release_with_a_pending_swap_request_and_waiting_resume(oj, room):
    eid, codes, _, _ = room
    body = sign_in(oj, codes["alice"], DEV_A)
    a = started(oj, body, eid, DEV_A)
    asked = oj.post(f"/api/student/attempts/{a['attempt_id']}/swap-request", json={"device_id": DEV_B},
                    headers=bearer(body))
    assert asked.status_code == 202
    assert view(oj)["alice"]["swap_requested"] is True

    async def waiting(s):
        await s.execute(update(Attempt).values(pending_resume=True, pending_since=utcnow() - timedelta(minutes=2)))
        await s.commit()
    db(oj, waiting)
    row = view(oj)["alice"]
    assert row["status"] == "waiting" and row["waiting_since"]
    assert call(oj, "POST", f"{STU}/alice/release-device", {"reason": "moved"}).status_code == 200
    row = view(oj)["alice"]
    assert row["swap_requested"] is False and row["waiting_since"] is None and row["status"] != "waiting"
    mine = attempts_of(oj)[0]
    assert mine.swap_old_session == a["session"] and mine.swap_approved_at is not None
    assert "moved to the computer that asked" in kinds(oj, IntegrityEvent, "device_released_oj")[0]


def test_release_refuses_handed_in_and_limits_to_three(oj, room):
    eid, codes, _, _ = room
    body = sign_in(oj, codes["alice"], DEV_A)
    a = started(oj, body, eid, DEV_A)
    for _ in range(3):
        assert call(oj, "POST", f"{STU}/alice/release-device", {"reason": "again"}).status_code == 200
    r = call(oj, "POST", f"{STU}/alice/release-device", {"reason": "again"})
    assert r.status_code == 429
    assert call(oj, "POST", f"{STU}/carol/release-device", {"reason": "other student"}).status_code == 200

    body = sign_in(oj, codes["ax19"], DEV_A)
    b = started(oj, body, eid, DEV_A)
    submit(oj, body, b, {}, 1)
    r = call(oj, "POST", f"{STU}/ax19/release-device", {"reason": "too late"})
    assert r.status_code == 409


@pytest.mark.parametrize("data", [{}, {"reason": ""}, {"reason": "   "}, {"reason": 5}, {"reason": "x" * 201}])
def test_reason_is_required_and_bounded(oj, room, data):
    sign_in(oj, room[1]["carol"], DEV_A)
    assert call(oj, "POST", f"{STU}/carol/release-device", data).status_code == 400
    assert call(oj, "POST", f"{STU}/carol/extra-time", {"minutes": 5, **data}).status_code == 400
    assert call(oj, "POST", f"{STU}/carol/reopen", {"minutes": 5, **data}).status_code == 400


def test_reason_of_200_characters_is_kept(oj, room):
    reason = "r" * 200
    assert call(oj, "POST", f"{STU}/carol/release-device", {"reason": f"  {reason}  "}).status_code == 200
    assert reason in kinds(oj, ClassLoginEvent, "device_released_oj")[0][2]


# ---------------------------------------------------------------- authorization
ACTIONS = [("release-device", {"reason": "r"}), ("reopen", {"minutes": 10, "reason": "r"}),
           ("extra-time", {"minutes": 10, "reason": "r"}), ("offline-exit", {"mac_code": "K7QM-3XPA"})]


def second_exam_with_editor(c):
    async def go(s):
        t = User(username="t2", name="Other Teacher", role="teacher", password_hash="x", netid="t2")
        s.add(t)
        await s.flush()
        e0 = (await s.execute(select(Exam).where(Exam.external_key == KEY))).scalar_one()
        s.add(Exam(id="e_other", teacher_id=t.id, title="Other", duration_min=10, status="live", questions=[],
                   question_count=0, total_points=0, source="dkuoj", external_key="other-exam"))
        await s.flush()
        s.add(OjEditor(exam_id="e_other", user_id=t.id))
        s.add(Exam(id="e_native", teacher_id=e0.teacher_id, title="Native", duration_min=10, status="live",
                   questions=[], question_count=0, total_points=0, source="fairplay", external_key=KEY + "-native"))
        await s.commit()
    db(c, go)


@pytest.mark.parametrize("action, data", ACTIONS)
def test_only_an_editor_of_the_exam_may_act(oj, room, action, data):
    eid, codes, _, _ = room
    second_exam_with_editor(oj)
    body = sign_in(oj, codes["alice"], DEV_A)
    a = started(oj, body, eid, DEV_A)
    before = [(x.device_id, x.deadline_at, x.status) for x in attempts_of(oj)]
    url = f"{STU}/alice/{action}"
    assert oj.post(url, json=data).status_code == 401
    for who in ("nobody", "alice", "ax19", "", "t2"):         # unknown, a student, a student, none, editor of another exam
        r = call(oj, "POST", url, data, acting=who)
        assert r.status_code == 403, (who, r.status_code, r.text)
    assert call(oj, "POST", f"{V1}/exams/{KEY}-native/students/alice/{action}", data).status_code == 409
    assert [(x.device_id, x.deadline_at, x.status) for x in attempts_of(oj)] == before
    assert db(oj, lambda s: s.scalar(select(OjStudentGrant.id))) is None
    assert a["attempt_id"]


def test_a_native_exam_is_out_of_reach_even_for_a_real_editor(oj, room):
    eid, codes, _, _ = room
    second_exam_with_editor(oj)

    async def native(s):
        u = (await s.execute(select(User).where(User.netid == "alice"))).scalar_one()
        e = (await s.execute(select(Exam).where(Exam.id == "e_native"))).scalar_one()
        s.add(Attempt(id="a_native", exam_id=e.id, student_id=u.id, status="submitted", started_at=utcnow(),
                      deadline_at=utcnow() + timedelta(hours=1), submitted_at=utcnow(), receipt="NATIVE",
                      answers={"q": "x"}, client_seq=1))
        await s.commit()
    db(oj, native)
    for action, data in ACTIONS:
        r = call(oj, "POST", f"{V1}/exams/{KEY}-native/students/alice/{action}", data)
        assert r.status_code == 409, (action, r.status_code)
    a = db(oj, lambda s: s.get(Attempt, "a_native"))
    assert a.status == "submitted" and a.receipt == "NATIVE" and a.answers == {"q": "x"}


def test_the_student_must_be_on_this_exam(oj, room):
    for action, data in ACTIONS:
        assert call(oj, "POST", f"{STU}/bob/{action}", data).status_code == 404, action
        assert call(oj, "POST", f"{STU}/zz-unknown/{action}", data).status_code == 404, action


# ---------------------------------------------------------------- reopen and revisions
def hand_in(c, eid, codes, who, device, text, seq=1):
    body = sign_in(c, codes[who], device)
    a = started(c, body, eid, device)
    put(c, body, a, {qid_of(a): text}, seq)
    return body, a, submit(c, body, a, {qid_of(a): text}, seq + 1)


def revisions(c):
    async def q(s):
        return [(d.revision, d.status, d.receipt) for d in (await s.execute(
            select(ExternalDelivery).order_by(ExternalDelivery.revision))).scalars()]
    return db(c, q)


def test_reopen_gives_a_second_hand_in_and_both_are_delivered(oj, room, fake):
    eid, codes, _, _ = room
    body, a, receipt1 = hand_in(oj, eid, codes, "carol", DEV_A, "first answer")
    aid, q5 = a["attempt_id"], qid_of(a)
    run(oj)
    assert [r[:2] for r in revisions(oj)] == [(1, "sent")] and len(fake.bodies) == 1
    first = fake.bodies[0]
    assert "revision" not in first and first["answers"]

    r = call(oj, "POST", f"{STU}/carol/reopen", {"minutes": 10, "reason": "network died at hand-in"})
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store" and set(r.json()) == {"ok", "deadline_at"}
    dl = parse_iso(r.json()["deadline_at"])
    assert timedelta(minutes=9) < dl - utcnow() <= timedelta(minutes=10)
    mine = attempts_of(oj)[0]
    assert (mine.status, mine.answers, mine.submitted_at, mine.device_id) == ("in_progress", {q5: "first answer"}, None, None)
    ev = kinds(oj, IntegrityEvent, "attempt_reopened")
    assert len(ev) == 1 and receipt1 in ev[0] and "teacher" in ev[0] and "network died at hand-in" in ev[0]
    row = view(oj)["carol"]
    assert (row["reopened"], row["hand_in_count"], row["code_state"]) == (True, 1, "unused")

    body2 = sign_in(oj, codes["carol"], DEV_B)
    b = started(oj, body2, eid, DEV_B)
    assert b["attempt_id"] == aid and b["answers"] == {q5: "first answer"} and len(attempts_of(oj)) == 1
    put(oj, body2, b, {q5: "second answer"}, 10)
    receipt2 = submit(oj, body2, b, {q5: "second answer"}, 11)
    assert receipt2 != receipt1
    assert view(oj)["carol"]["hand_in_count"] == 2
    assert [r[:2] for r in revisions(oj)] == [(1, "sent"), (2, "pending")]

    run(oj)
    assert [r[0:2] for r in revisions(oj)] == [(1, "sent"), (2, "sent")]
    rcpts = [r[2] for r in revisions(oj)]
    assert rcpts == [f"oj_rcpt_{aid}_1", f"oj_rcpt_{aid}_2"]
    one, two = fake.bodies
    assert one["answers"] != two["answers"] and one["attempt_id"] == two["attempt_id"] == aid
    assert "revision" not in one and "supersedes" not in one
    assert two["revision"] == 2 and two["supersedes"] == aid
    assert "first answer" in str(one["answers"]) and "second answer" in str(two["answers"])
    assert call(oj, "POST", f"{STU}/carol/reopen", {"minutes": 10, "reason": "again"}).status_code == 409


def test_a_hand_in_not_sent_yet_keeps_its_own_answers_and_waits_for_the_earlier_one(oj, room, fake):
    eid, codes, _, _ = room
    body, a, _ = hand_in(oj, eid, codes, "carol", DEV_A, "first answer")
    q5 = qid_of(a)
    assert call(oj, "POST", f"{STU}/carol/reopen", {"minutes": 5, "reason": "r"}).status_code == 200
    body2 = sign_in(oj, codes["carol"], DEV_B)
    b = started(oj, body2, eid, DEV_B)
    put(oj, body2, b, {q5: "second answer"}, 10)
    submit(oj, body2, b, {q5: "second answer"}, 11)
    fake.submit_fail["carol"] = [503]
    t0 = utcnow()
    run(oj, now=t0)
    assert fake.bodies == [] and [r[:2] for r in revisions(oj)] == [(1, "failed"), (2, "pending")]
    run(oj, now=t0 + timedelta(seconds=40))
    run(oj, now=t0 + timedelta(seconds=80))
    assert [x.get("revision", 1) for x in fake.bodies] == [1, 2]
    assert "first answer" in str(fake.bodies[0]["answers"]) and "second answer" not in str(fake.bodies[0]["answers"])
    assert "second answer" in str(fake.bodies[1]["answers"])


def test_resend_repairs_a_cached_revision_without_rebuilding_its_answers(oj, room, fake):
    eid, codes, _, teacher = room
    _, a, _ = hand_in(oj, eid, codes, "carol", DEV_A, "first answer")
    run(oj)
    assert call(oj, "POST", f"{STU}/carol/reopen", {"minutes": 5, "reason": "r"}).status_code == 200
    body = sign_in(oj, codes["carol"], DEV_B)
    b = started(oj, body, eid, DEV_B)
    put(oj, body, b, {qid_of(b): "second answer"}, 10)
    submit(oj, body, b, {qid_of(b): "second answer"}, 11)
    fake.submit_fail["carol"] = [503]
    run(oj)

    async def legacy_payload(s):
        d = (await s.execute(select(ExternalDelivery).where(
            ExternalDelivery.attempt_id == a["attempt_id"], ExternalDelivery.revision == 2))).scalar_one()
        d.payload = {**d.payload, "supersedes": 1}
        d.status, d.last_status = "dead", 422
        d.uploads = {"cached-file": "existing-upload"}
        attempt = await s.get(Attempt, a["attempt_id"])
        attempt.answers = {qid_of(b): "not the handed-in answer"}
        await s.commit()
        return d.id, json.loads(json.dumps(d.payload))

    did, cached = db(oj, legacy_payload)
    r = oj.post(f"/api/deliveries/{did}/resend", headers=teacher)
    assert r.status_code == 200, r.text
    run(oj)
    assert [r[:2] for r in revisions(oj)] == [(1, "sent"), (2, "sent")]
    expected = {**cached, "supersedes": a["attempt_id"]}
    assert fake.bodies[-1] == expected

    async def stored(s):
        d = await s.get(ExternalDelivery, did)
        return d.payload, d.uploads
    assert db(oj, stored) == (expected, {"cached-file": "existing-upload"})


def test_reopen_deadline_is_capped_at_the_close_time(oj, room):
    eid, codes, _, _ = room
    hand_in(oj, eid, codes, "carol", DEV_A, "x")

    async def soon_over(s):
        e = (await s.execute(select(Exam).where(Exam.external_key == KEY))).scalar_one()
        meta = dict(e.external_meta)
        meta["end_time"] = iso(utcnow() + timedelta(minutes=1))
        e.external_meta = meta
        await s.commit()
    db(oj, soon_over)
    r = call(oj, "POST", f"{STU}/carol/reopen", {"minutes": 60, "reason": "r"})
    assert r.status_code == 200, r.text
    left = parse_iso(r.json()["deadline_at"]) - utcnow()
    assert timedelta(minutes=10) < left < timedelta(minutes=11, seconds=5)      # close + alice's 10 min extra


def test_reopen_refusals(oj, room):
    eid, codes, _, _ = room
    for bad in (4, 61, 0, True, "10", None, 10.5):
        assert call(oj, "POST", f"{STU}/carol/reopen", {"minutes": bad, "reason": "r"}).status_code == 400, bad
    assert call(oj, "POST", f"{STU}/carol/reopen", {"minutes": 10, "reason": "r"}).status_code == 409   # no attempt
    body = sign_in(oj, codes["carol"], DEV_A)
    a = started(oj, body, eid, DEV_A)
    assert call(oj, "POST", f"{STU}/carol/reopen", {"minutes": 10, "reason": "r"}).status_code == 409   # still writing
    submit(oj, body, a, {}, 1)

    for values in ({"external_result": {"released": True, "total": 3}}, {"grades": {"q": {"points": 1}}}):
        db(oj, lambda s, v=values: _upd(s, Attempt, v))
        assert call(oj, "POST", f"{STU}/carol/reopen", {"minutes": 10, "reason": "r"}).status_code == 409, values
        db(oj, lambda s: _upd(s, Attempt, {"external_result": None, "grades": {}}))
    db(oj, lambda s: _upd(s, Exam, {"grades_released": True}))
    assert call(oj, "POST", f"{STU}/carol/reopen", {"minutes": 10, "reason": "r"}).status_code == 409
    db(oj, lambda s: _upd(s, Exam, {"grades_released": False, "status": "closed"}))
    assert call(oj, "POST", f"{STU}/carol/reopen", {"minutes": 10, "reason": "r"}).status_code == 409
    assert attempts_of(oj)[0].status == "submitted" and attempts_of(oj)[0].receipt


async def _upd(s, model, values):
    await s.execute(update(model).values(**values))
    await s.commit()


def test_old_outbox_table_with_a_unique_attempt_id_is_upgraded(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE external_deliveries (id INTEGER PRIMARY KEY, source VARCHAR(32) NOT NULL,
            attempt_id VARCHAR(32) NOT NULL UNIQUE, exam_id VARCHAR(32) NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'pending',
            tries INTEGER NOT NULL DEFAULT 0, next_at DATETIME NOT NULL, last_error VARCHAR(500), receipt VARCHAR(200),
            payload JSON, uploads JSON, created_at DATETIME NOT NULL, sent_at DATETIME, locked_until DATETIME,
            claimed_by VARCHAR(64), last_status INTEGER, auth_failed_at DATETIME, revision INTEGER NOT NULL DEFAULT 1);
        CREATE INDEX ix_external_deliveries_due ON external_deliveries (source, status, next_at);
        CREATE INDEX ix_external_deliveries_exam_id ON external_deliveries (exam_id);
        INSERT INTO external_deliveries (source, attempt_id, exam_id, status, next_at, created_at, receipt)
            VALUES ('dkuoj', 'a1', 'e1', 'sent', '2026-10-01 00:00:00', '2026-10-01 00:00:00', 'R1');
    """)
    con.commit()
    con.close()
    eng = create_engine(f"sqlite:///{path}")
    with eng.begin() as conn:
        outbox._upgrade_unique(conn)
    with eng.begin() as conn:
        conn.execute(text("INSERT INTO external_deliveries (source, attempt_id, revision, exam_id, status, next_at, "
                          "created_at, tries) VALUES ('dkuoj', 'a1', 2, 'e1', 'pending', '2026-10-01', '2026-10-01', 0)"))
        rows = conn.execute(text("SELECT attempt_id, revision, receipt FROM external_deliveries ORDER BY revision")).all()
        assert [tuple(r) for r in rows] == [("a1", 1, "R1"), ("a1", 2, None)]
        with pytest.raises(Exception):
            conn.execute(text("INSERT INTO external_deliveries (source, attempt_id, revision, exam_id, status, "
                              "next_at, created_at, tries) VALUES ('dkuoj', 'a1', 2, 'e1', 'pending', '2026-10-01', "
                              "'2026-10-01', 0)"))
    with eng.begin() as conn:
        outbox._upgrade_unique(conn)           # already current: nothing to do


# ---------------------------------------------------------------- extra time
def test_extra_time_before_the_start_and_it_survives_a_sync(oj, room, fake):
    eid, codes, _, _ = room
    r = call(oj, "POST", f"{STU}/carol/extra-time", {"minutes": 30, "reason": "accommodation"})
    assert r.status_code == 200 and r.json() == {"ok": True, "deadline_at": None}
    assert r.headers["cache-control"] == "no-store"
    fake.roster[1]["extra_time_seconds"] = 300          # DKU OJ's own extra time for carol changes
    run(oj)
    run(oj)
    body = sign_in(oj, codes["carol"], DEV_A)
    a = started(oj, body, eid, DEV_A)
    assert (parse_iso(a["deadline_at"]) - parse_iso(a["started_at"])).total_seconds() == (90 + 30) * 60 + 300
    grant = db(oj, lambda s: s.scalar(select(OjStudentGrant.extra_seconds)))
    assert grant == 1800
    ev = kinds(oj, ClassLoginEvent, "extra_time_granted")
    assert ev[0][0] == "carol" and "accommodation" in ev[0][2]


def test_extra_time_for_a_running_attempt_applies_at_once_and_is_capped(oj, room, fake):
    eid, codes, _, _ = room
    body = sign_in(oj, codes["carol"], DEV_A)
    a = started(oj, body, eid, DEV_A)
    before = attempts_of(oj)[0].deadline_at
    r = call(oj, "POST", f"{STU}/carol/extra-time", {"minutes": 120, "reason": "r"})
    assert r.status_code == 200
    assert abs(parse_iso(r.json()["deadline_at"]) - before - timedelta(minutes=120)) < timedelta(milliseconds=1)
    live = oj.get(f"/api/student/attempts/{a['attempt_id']}", headers={**bearer(body), "X-FairPlay-Session": a["session"]})
    assert abs(parse_iso(live.json()["deadline_at"]) - before - timedelta(minutes=120)) < timedelta(milliseconds=1)
    assert call(oj, "POST", f"{STU}/carol/extra-time", {"minutes": 121, "reason": "r"}).status_code == 400
    assert call(oj, "POST", f"{STU}/carol/extra-time", {"minutes": 0, "reason": "r"}).status_code == 400
    assert call(oj, "POST", f"{STU}/carol/extra-time", {"minutes": 120, "reason": "r"}).status_code == 200
    r = call(oj, "POST", f"{STU}/carol/extra-time", {"minutes": 1, "reason": "r"})
    assert r.status_code == 409 and "240" in r.json()["detail"]
    assert attempts_of(oj)[0].deadline_at - before == timedelta(minutes=240)

    fake.end = fake.end + timedelta(hours=1)          # DKU OJ moves the end later: the sync never shortens it
    fake.listing_updated = iso(utcnow())
    run(oj)
    assert attempts_of(oj)[0].deadline_at - before >= timedelta(minutes=240)


def test_extra_time_widens_the_window_the_exam_stays_open(oj, room):
    from app.integrations.dkuoj.source import SOURCE

    async def extra(s):
        e = (await s.execute(select(Exam).where(Exam.external_key == KEY))).scalar_one()
        return await SOURCE.max_extra_time(s, e.id)
    assert db(oj, extra) == timedelta(seconds=600)
    assert call(oj, "POST", f"{STU}/carol/extra-time", {"minutes": 60, "reason": "r"}).status_code == 200
    assert db(oj, extra) == timedelta(minutes=60)


def test_extra_time_refusals(oj, room):
    eid, codes, _, _ = room
    body = sign_in(oj, codes["ax19"], DEV_A)
    a = started(oj, body, eid, DEV_A)
    submit(oj, body, a, {}, 1)
    assert call(oj, "POST", f"{STU}/ax19/extra-time", {"minutes": 5, "reason": "r"}).status_code == 409
    db(oj, lambda s: _upd(s, Exam, {"status": "closed"}))
    assert call(oj, "POST", f"{STU}/carol/extra-time", {"minutes": 5, "reason": "r"}).status_code == 409
    assert db(oj, lambda s: s.scalar(select(OjStudentGrant.id))) is None


# ---------------------------------------------------------------- offline exit
def test_offline_exit_gives_the_teacher_route_code(oj, room, monkeypatch):
    eid, codes, _, teacher = room
    body = sign_in(oj, codes["alice"], DEV_A)
    a = started(oj, body, eid, DEV_A)
    r = call(oj, "POST", f"{STU}/alice/offline-exit", {"mac_code": "k7qm-3xpa"})
    assert r.status_code == 200 and set(r.json()) == {"code"} and r.headers["cache-control"] == "no-store"
    via_teacher = oj.post(f"/api/exams/{eid}/attempts/{a['attempt_id']}/offline-exit",
                          json={"mac_code": "K7QM-3XPA"}, headers=teacher)
    assert via_teacher.status_code == 200 and via_teacher.json() == r.json()
    ev = kinds(oj, IntegrityEvent, "offline_exit_code_issued")
    assert any("teacher" in d and "K7QM-3XPA" in d for d in ev)
    assert kinds(oj, ClassLoginEvent, "offline_exit_code_issued")[0][0] == "alice"

    assert call(oj, "POST", f"{STU}/alice/offline-exit", {"mac_code": "nope"}).status_code == 422
    assert call(oj, "POST", f"{STU}/alice/offline-exit", {}).status_code == 400
    assert call(oj, "POST", f"{STU}/carol/offline-exit", {"mac_code": "K7QM-3XPA"}).status_code == 404
    monkeypatch.setattr(rescue, "OFFLINE_EXITS_PER_STUDENT_HOUR", 2)
    assert call(oj, "POST", f"{STU}/alice/offline-exit", {"mac_code": "K7QM-3XPA"}).status_code == 200
    assert call(oj, "POST", f"{STU}/alice/offline-exit", {"mac_code": "K7QM-3XPA"}).status_code == 429


# ---------------------------------------------------------------- student view
def test_exam_view_student_fields(oj, room):
    eid, codes, _, _ = room
    v = view(oj)
    assert {k: v["carol"][k] for k in
            ("swap_requested", "code_state", "reopened", "hand_in_count", "waiting_since")} == {
        "swap_requested": False, "code_state": "unused", "reopened": False, "hand_in_count": 0,
        "waiting_since": None}
    started(oj, sign_in(oj, codes["carol"], DEV_A), eid, DEV_A)
    assert view(oj)["carol"]["code_state"] == "bound"
    assert view(oj)["ax19"]["code_state"] == "unused"
    body, a, _ = hand_in(oj, eid, codes, "alice", DEV_A, "x")
    assert view(oj)["alice"]["hand_in_count"] == 1 and view(oj)["alice"]["reopened"] is False
