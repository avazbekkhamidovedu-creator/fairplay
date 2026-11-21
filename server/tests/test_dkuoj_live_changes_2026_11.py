"""2026-11 review fixes: what DKU OJ changes while students write reaches them (heartbeat content_rev),
answers of removed questions stay stored, DKU OJ website links up to 32, reopen refreshes the exam cache,
and the sealed replacement of a swept hand-in is coordinated with the outbox (current revision only)."""
from datetime import timedelta

from sqlalchemy import select

from app.integrations.models import ExternalDelivery as OjDelivery
from app.models import Attempt, Exam, IntegrityEvent
from app.services import exam_key, get_exam_cached
from app.store import get_store
from app.timeutil import utcnow

from .oj_helpers import DEV_A, DEV_B, KEY, bearer, sign_in
from .test_dkuoj_bridge import db, package, run
from .test_dkuoj_no_limits_2026_11 import _upd, bump, hosts
from .test_dkuoj_rescue import STU, V1, attempts_of, call, fake, hand_in, kinds, oj, put, qid_of, room, started  # noqa: F401
from .test_s5_sealed_submit import (SECRET, app_client, exam, mcq_answer, offline_past_deadline,  # noqa: F401
                                    post, results_row, seal)


def heartbeat(c, body, a):
    h = {**bearer(body), "X-FairPlay-Session": a["session"]}
    r = c.post(f"/api/student/attempts/{a['attempt_id']}/heartbeat", json={"current_question": 0, "locked": True},
               headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def get_attempt(c, body, a):
    h = {**bearer(body), "X-FairPlay-Session": a["session"]}
    r = c.get(f"/api/student/attempts/{a['attempt_id']}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def stored(c):
    return attempts_of(c)[0].answers


# ---------------------------------------------------------------- P1: the running page hears about changes
def test_heartbeat_content_rev_follows_tools_and_name(oj, room, fake, monkeypatch):
    eid, codes, _, _ = room
    body = sign_in(oj, codes["alice"], DEV_A)
    a = started(oj, body, eid, DEV_A)
    rev0 = heartbeat(oj, body, a)["content_rev"]
    assert rev0 and a["content_rev"] == rev0 and heartbeat(oj, body, a)["content_rev"] == rev0   # stable

    # tools: the calculator and websites change live
    r = call(oj, "POST", f"{V1}/exams/{KEY}/settings", {"calculator": "graphing", "websites": ["docs.python.org"]})
    assert r.status_code == 200, r.text
    rev1 = heartbeat(oj, body, a)["content_rev"]
    assert rev1 != rev0
    view = get_attempt(oj, body, a)
    assert view["content_rev"] == rev1 and view["tools"]["calculator"] == "graphing"
    assert "docs.python.org" in view["tools"]["websites"]

    # the exam name follows DKU OJ too (the questions stay as started)
    orig = fake.listing
    monkeypatch.setattr(fake, "listing", lambda: {**orig(), "name": "Renamed live"})
    bump(fake)
    run(oj)
    rev2 = heartbeat(oj, body, a)["content_rev"]
    assert rev2 not in (rev0, rev1)
    view = get_attempt(oj, body, a)
    assert view["content_rev"] == rev2 and view["title"] == "Renamed live"


# ---------------------------------------------------------------- P2: website links over 20
def test_dkuoj_exam_keeps_up_to_32_website_links_everywhere(oj, room):
    eid, codes, _, _ = room
    urls = [f"https://site{i}.example.edu/start" for i in range(30)]
    r = call(oj, "POST", f"{V1}/exams/{KEY}/settings", {"websites": urls})
    assert r.status_code == 200, r.text
    s = r.json()["settings"]
    assert len(s["website_links"]) == 30 and len(s["lock_sites"]["pages"]) <= 32
    body = sign_in(oj, codes["alice"], DEV_A)
    a = started(oj, body, eid, DEV_A)
    assert len(a["tools"]["website_links"]) == 30
    assert len(get_attempt(oj, body, a)["tools"]["website_links"]) == 30


# ---------------------------------------------------------------- P2: reopen brings the exam back to live
def test_reopen_of_an_auto_closed_exam_drops_the_cached_closed_exam(oj, room):
    eid, codes, _, _ = room
    hand_in(oj, eid, codes, "carol", DEV_A, "answer")
    db(oj, lambda s: _upd(s, Exam, {"status": "closed", "auto_closed": True, "closed_at": utcnow()}))

    async def cache_it(s):
        await get_store().delete(exam_key(eid))
        return (await get_exam_cached(get_store(), s, eid))["status"]
    assert db(oj, cache_it) == "closed"
    r = call(oj, "POST", f"{STU}/carol/reopen", {"minutes": 10})
    assert r.status_code == 200, r.text

    async def cached_now(s):
        raw = await get_store().get(exam_key(eid))
        return None if raw is None else (await get_exam_cached(get_store(), s, eid))["status"]
    assert db(oj, cached_now) in (None, "live")


# ---------------------------------------------------------------- P2: sealed replacement vs the outbox
def seed_deliveries(c, aid, exam_id, reopened, rows):
    async def go(s):
        for _ in range(reopened):
            s.add(IntegrityEvent(attempt_id=aid, exam_id=exam_id, type="attempt_reopened", detail="x",
                                 server_time=utcnow()))
        for rev, payload, locked in rows:
            s.add(OjDelivery(source="dkuoj", attempt_id=aid, revision=rev, exam_id=exam_id, status="pending",
                             payload=payload, locked_until=locked, next_at=utcnow(), created_at=utcnow()))
        await s.commit()
    db(c, go)


def in_time_seal(c, exam, rows, reopened=0):
    h, a, answers, deadline = offline_past_deadline(c, exam)
    aid = a["attempt_id"]
    seed_deliveries(c, aid, exam["id"], reopened, rows)
    return post(c, h, aid, seal(aid, a["session"], answers, sealed_at=deadline - timedelta(seconds=30))), c, exam


def test_replacement_is_judged_on_the_current_revision_only(app_client, exam):
    # revision 1 was built and sent long ago; the attempt was reopened and swept again: revision 2 is not built
    r, c, ex = in_time_seal(app_client, exam, [(1, {"attempt_id": "x"}, None), (2, None, None)], reopened=1)
    assert r.status_code == 200, r.text
    assert results_row(c, ex)["auto_submitted"] is False


def test_replacement_refused_once_the_current_revision_is_built(app_client, exam):
    r, c, ex = in_time_seal(app_client, exam, [(1, None, None), (2, {"attempt_id": "x"}, None)], reopened=1)
    assert r.status_code == 409
    assert results_row(c, ex)["auto_submitted"] is True


def test_replacement_refused_while_a_worker_holds_the_row(app_client, exam):
    r, c, ex = in_time_seal(app_client, exam, [(1, None, utcnow() + timedelta(minutes=5))])
    assert r.status_code == 409
    assert results_row(c, ex)["auto_submitted"] is True


def test_replacement_goes_through_with_an_unbuilt_unclaimed_row(app_client, exam):
    r, c, ex = in_time_seal(app_client, exam, [(1, None, None)])
    assert r.status_code == 200, r.text
    assert results_row(c, ex)["auto_submitted"] is False


# ---------------------------------------------------------------- P2: a failure after the commit never lets a retry add time twice
def test_add_time_retry_after_a_post_commit_failure_adds_nothing_more(oj, room, monkeypatch):
    from app import exam_control

    async def boom(*_a, **_k):
        raise RuntimeError("cache down")
    before = db(oj, lambda s: s.scalar(select(Exam).where(Exam.external_key == KEY))).duration_min
    monkeypatch.setattr(exam_control, "invalidate_exam", boom)
    path = f"{V1}/exams/{KEY}/add-time"
    r = call(oj, "POST", path, {"minutes": 10, "request_id": "rq-1"})
    assert r.status_code == 200, r.text
    monkeypatch.undo()
    r = call(oj, "POST", path, {"minutes": 10, "request_id": "rq-1"})
    assert r.status_code == 200
    assert db(oj, lambda s: s.scalar(select(Exam).where(Exam.external_key == KEY))).duration_min == before + 10


def test_extra_time_retry_after_a_post_commit_failure_adds_nothing_more(oj, room, monkeypatch):
    from app.integrations.dkuoj import routes_rescue

    async def boom(*_a, **_k):
        raise RuntimeError("event store down")
    eid, codes, _, _ = room
    body = sign_in(oj, codes["carol"], DEV_A)
    started(oj, body, eid, DEV_A)
    before = attempts_of(oj)[0].deadline_at
    monkeypatch.setattr(routes_rescue, "_record", boom)
    path = f"{STU}/carol/extra-time"
    assert call(oj, "POST", path, {"minutes": 7, "request_id": "rq-2"}).status_code == 200
    monkeypatch.undo()
    assert call(oj, "POST", path, {"minutes": 7, "request_id": "rq-2"}).json().get("repeated") is True
    assert attempts_of(oj)[0].deadline_at - before == timedelta(minutes=7)


# ---------------------------------------------------------------- running deadlines follow a changed duration from the start
def limit(fake, monkeypatch, seconds):
    orig = fake.listing
    monkeypatch.setattr(fake, "listing", lambda: {**orig(), "time_limit_seconds": seconds})
    bump(fake)


def start_carol(oj, room, fake, end_in_min):
    eid, codes, _, _ = room
    fake.end = utcnow() + timedelta(minutes=end_in_min)
    bump(fake)
    run(oj)
    body = sign_in(oj, codes["carol"], DEV_A)
    started(oj, body, eid, DEV_A)
    return attempts_of(oj)[0]


def test_a_shorter_limit_does_not_take_time_the_close_had_already_withheld(oj, room, fake, monkeypatch):
    a = start_carol(oj, room, fake, 60)                  # 90 min limit, closes in 60: capped at the close
    capped = a.deadline_at
    assert capped - a.started_at < timedelta(minutes=61)
    limit(fake, monkeypatch, 75 * 60)                    # 90 -> 75: still more than the 60 the close allows
    run(oj)
    assert attempts_of(oj)[0].deadline_at == capped      # not capped - 15 min


def test_a_shorter_limit_below_the_cap_shortens_from_the_start(oj, room, fake, monkeypatch):
    a = start_carol(oj, room, fake, 180)                 # no cap in play
    limit(fake, monkeypatch, 45 * 60)
    run(oj)
    assert attempts_of(oj)[0].deadline_at == a.started_at + timedelta(minutes=45)


def test_a_longer_limit_follows_up_to_the_close_and_not_past_it(oj, room, fake, monkeypatch):
    a = start_carol(oj, room, fake, 100)                 # 90 min limit, closes in 100: not capped
    limit(fake, monkeypatch, 120 * 60)
    run(oj)
    after = attempts_of(oj)[0].deadline_at
    assert after - a.started_at < timedelta(minutes=101)  # 120 asked, the close lets 100
    assert after > a.deadline_at


def test_added_minutes_stay_when_the_limit_changes(oj, room, fake, monkeypatch):
    a = start_carol(oj, room, fake, 180)
    assert call(oj, "POST", f"{V1}/exams/{KEY}/add-time", {"minutes": 10}).status_code == 200
    limit(fake, monkeypatch, 60 * 60)                    # 90 -> 60, the 10 added minutes remain
    run(oj)
    assert attempts_of(oj)[0].deadline_at == a.started_at + timedelta(minutes=70)


def test_a_reopened_deadline_is_left_alone_by_a_limit_change(oj, room, fake, monkeypatch):
    start_carol(oj, room, fake, 180)
    mine = utcnow() + timedelta(minutes=7)
    db(oj, lambda s: _upd(s, Attempt, {"deadline_at": mine}))     # set by hand (a reopen)
    limit(fake, monkeypatch, 30 * 60)
    run(oj)
    assert attempts_of(oj)[0].deadline_at == mine


# ---------------------------------------------------------------- reopen events are part of the reopen's transaction
def test_add_time_reopen_writes_its_events_with_the_commit_even_if_the_store_fails(oj, room, monkeypatch):
    from app import exam_control
    eid, codes, _, _ = room
    body = sign_in(oj, codes["carol"], DEV_A)
    started(oj, body, eid, DEV_A)
    closed = utcnow() - timedelta(seconds=5)
    db(oj, lambda s: _upd(s, Exam, {"status": "closed", "auto_closed": True, "closed_at": closed}))
    db(oj, lambda s: _upd(s, Attempt, {"status": "submitted", "auto_submitted": True, "submitted_at": utcnow()}))

    class Down:
        def __getattr__(self, _name):
            async def boom(*_a, **_k):
                raise RuntimeError("redis down")
            return boom
    monkeypatch.setattr(exam_control, "get_store", lambda: Down())
    r = call(oj, "POST", f"{V1}/exams/{KEY}/add-time", {"minutes": 15})
    assert r.status_code == 200, r.text
    monkeypatch.undo()
    assert attempts_of(oj)[0].status == "in_progress"
    assert len(kinds(oj, IntegrityEvent, "attempt_reopened")) == 1       # same transaction: it is there


# ---------------------------------------------------------------- sealed replacement vs teacher grades
def set_grades(c, aid, grades):
    from sqlalchemy import update as _u

    async def go(s):
        await s.execute(_u(Attempt).where(Attempt.id == aid).values(grades=grades))
        await s.commit()
    db(c, go)


def attempt_row(c, aid):
    return db(c, lambda s: s.get(Attempt, aid))


def test_replacement_drops_the_grade_of_an_answer_it_changed(app_client, exam):
    h, a, answers, deadline = offline_past_deadline(app_client, exam)
    aid = a["attempt_id"]
    qid = next(iter(answers))
    set_grades(app_client, aid, {qid: {"points": 0, "comment": "wrong", "graded_at": "x"}})
    r = post(app_client, h, aid, seal(aid, a["session"], answers, sealed_at=deadline - timedelta(seconds=30)))
    assert r.status_code == 200, r.text
    row = attempt_row(app_client, aid)
    assert not row.grades                      # swept answer was {q: 0}, the seal says {q: 1}: graded again
    assert row.score == 2 and row.auto_submitted is False


def test_replacement_keeps_the_grade_of_an_unchanged_answer(app_client, exam):
    h, a, answers, deadline = offline_past_deadline(app_client, exam)
    aid = a["attempt_id"]
    qid = next(iter(answers))
    set_grades(app_client, aid, {qid: {"points": 1, "comment": "partial", "graded_at": "x"}})
    same = {qid: 0}                            # what the server had swept
    r = post(app_client, h, aid, seal(aid, a["session"], same, sealed_at=deadline - timedelta(seconds=30)))
    assert r.status_code == 200, r.text
    row = attempt_row(app_client, aid)
    assert row.grades == {qid: {"points": 1, "comment": "partial", "graded_at": "x"}}
    assert row.score == 1


# ---------------------------------------------------------------- resume compensation and reopen markers
def test_time_given_back_at_a_resume_keeps_following_a_changed_limit(oj, room, fake, monkeypatch):
    a = start_carol(oj, room, fake, 180)
    db(oj, lambda s: _upd(s, Attempt, {"pending_resume": True, "pending_since": utcnow() - timedelta(minutes=10)}))
    assert call(oj, "POST", f"{V1}/exams/{KEY}/approve", {"netid": "carol"}).status_code == 200
    given = attempts_of(oj)[0]
    assert given.deadline_extra_s >= 590 and given.deadline_at - a.deadline_at >= timedelta(minutes=9, seconds=50)
    limit(fake, monkeypatch, 60 * 60)                    # 90 -> 60
    run(oj)
    after = attempts_of(oj)[0].deadline_at
    assert after == a.started_at + timedelta(minutes=60) + timedelta(seconds=given.deadline_extra_s)


def test_a_reopen_marks_the_deadline_as_set_by_hand(oj, room, fake, monkeypatch):
    eid, codes, _, _ = room
    hand_in(oj, eid, codes, "carol", DEV_A, "answer")
    assert call(oj, "POST", f"{STU}/carol/reopen", {"minutes": 10}).status_code == 200
    mine = attempts_of(oj)[0]
    assert mine.deadline_fixed is True
    limit(fake, monkeypatch, 30 * 60)
    run(oj)
    assert attempts_of(oj)[0].deadline_at == mine.deadline_at


# ---------------------------------------------------------------- extra time after the automatic close
def test_extra_time_that_does_not_reach_the_future_is_refused_after_the_clock_closed(oj, room, fake):
    fake.end = utcnow() - timedelta(minutes=30)
    bump(fake)
    run(oj)
    db(oj, lambda s: _upd(s, Exam, {"status": "closed", "auto_closed": True, "closed_at": utcnow()}))
    r = call(oj, "POST", f"{STU}/carol/extra-time", {"minutes": 10, "request_id": "late-1"})
    assert r.status_code == 409 and "add time for everyone or reopen" in r.json()["detail"]
    assert db(oj, lambda s: s.scalar(select(Exam).where(Exam.external_key == KEY))).status == "closed"
    # the refused id is not used up: a bigger grant with the same id goes through
    r = call(oj, "POST", f"{STU}/carol/extra-time", {"minutes": 90, "request_id": "late-1"})
    assert r.status_code == 200, r.text
    assert db(oj, lambda s: s.scalar(select(Exam).where(Exam.external_key == KEY))).status == "live"


def test_extra_time_that_reaches_the_future_brings_the_exam_back_and_start_works(oj, room, fake):
    eid, codes, _, _ = room
    fake.end = utcnow() - timedelta(minutes=5)
    bump(fake)
    run(oj)
    db(oj, lambda s: _upd(s, Exam, {"status": "closed", "auto_closed": True, "closed_at": utcnow()}))

    async def cache_it(s):
        await get_store().delete(exam_key(eid))
        return (await get_exam_cached(get_store(), s, eid))["status"]
    assert db(oj, cache_it) == "closed"
    assert call(oj, "POST", f"{STU}/carol/extra-time", {"minutes": 20}).status_code == 200

    async def cached_now(s):
        raw = await get_store().get(exam_key(eid))
        return None if raw is None else (await get_exam_cached(get_store(), s, eid))["status"]
    assert db(oj, cached_now) in (None, "live")
    body = sign_in(oj, codes["carol"], DEV_A)
    a = started(oj, body, eid, DEV_A)                       # /start no longer reads the closed exam
    assert a["status"] == "in_progress"


# ---------------------------------------------------------------- accommodation changed together with the end time
def test_removing_and_restoring_the_end_time_keeps_the_deadline_following(oj, room, fake):
    a = start_carol(oj, room, fake, 30)                        # 90 min limit, capped by the end in 30
    assert a.deadline_at - a.started_at < timedelta(minutes=31)
    fake.end = None                                            # the cap goes: the full 90 minutes
    bump(fake)
    run(oj)
    assert attempts_of(oj)[0].deadline_at == a.started_at + timedelta(minutes=90)
    fake.end = a.started_at + timedelta(minutes=60)            # a cap again, at 60
    bump(fake)
    run(oj)
    assert attempts_of(oj)[0].deadline_at - a.started_at <= timedelta(minutes=60, seconds=1)
    fake.end = a.started_at + timedelta(minutes=75)            # and extended to 75
    bump(fake)
    run(oj)
    after = attempts_of(oj)[0].deadline_at - a.started_at
    assert timedelta(minutes=74) < after <= timedelta(minutes=75, seconds=1)


def test_a_changed_accommodation_together_with_the_end_time_moves_the_running_attempt(oj, room, fake):
    a = start_carol(oj, room, fake, 180)                       # no accommodation yet, 90 min limit
    assert a.deadline_at == a.started_at + timedelta(minutes=90)
    for r in fake.roster:
        if r["username"] == "carol":
            r["extra_time_seconds"] = 900                      # 15 min accommodation
    fake.end = utcnow() + timedelta(minutes=200)               # and the end moves too
    bump(fake)
    run(oj)
    assert attempts_of(oj)[0].deadline_at == a.started_at + timedelta(minutes=105)


# ---------------------------------------------------------------- a seal only replaces a hand-in the deadline sweep made
def exam_update(c, exam_id, **values):
    from app.models import Exam as _E

    async def go(s):
        from sqlalchemy import update as _u
        await s.execute(_u(_E).where(_E.id == exam_id).values(**values))
        await s.commit()
    db(c, go)


def sealed_after_sweep(c, exam):
    h, a, answers, deadline = offline_past_deadline(c, exam)
    return h, a, answers, deadline


def test_released_then_reopened_exam_keeps_the_swept_hand_in(app_client, exam):
    h, a, answers, deadline = sealed_after_sweep(app_client, exam)
    aid = a["attempt_id"]
    exam_update(app_client, exam["id"], grades_released=True)          # grades released, the exam is live again
    r = post(app_client, h, aid, seal(aid, a["session"], answers, sealed_at=deadline - timedelta(seconds=30)))
    assert r.status_code == 409
    row = results_row(app_client, exam)
    assert row["auto_submitted"] is True and row["score"] == 0


def test_an_automatic_close_hand_in_is_never_replaced(app_client, exam):
    h, a, answers, deadline = sealed_after_sweep(app_client, exam)
    aid = a["attempt_id"]
    from .s_helpers import db_update
    db_update(app_client, aid, deadline_swept=False)                   # handed in by the close, not the sweep
    r = post(app_client, h, aid, seal(aid, a["session"], answers, sealed_at=deadline - timedelta(seconds=30)))
    assert r.status_code == 409
    assert results_row(app_client, exam)["auto_submitted"] is True


def test_a_hand_in_with_an_external_result_is_never_replaced(app_client, exam):
    h, a, answers, deadline = sealed_after_sweep(app_client, exam)
    aid = a["attempt_id"]
    from .s_helpers import db_update
    db_update(app_client, aid, external_result={"released": True, "total": 1})
    r = post(app_client, h, aid, seal(aid, a["session"], answers, sealed_at=deadline - timedelta(seconds=30)))
    assert r.status_code == 409


# ---------------------------------------------------------------- request ids live in the database
def _used(c, eid, scope_prefix="addtime"):
    from app.integrations.dkuoj.models import OjRequestUse

    async def go(s):
        return [(r.scope, r.request_id) for r in (await s.execute(select(OjRequestUse))).scalars()]
    return db(c, go)


def test_an_applied_request_id_is_in_the_database_and_a_repeat_adds_nothing_even_without_redis(oj, room):
    eid = _eid(oj)
    path = f"{V1}/exams/{KEY}/add-time"
    d0 = db(oj, lambda s: s.scalar(select(Exam).where(Exam.external_key == KEY))).duration_min
    assert call(oj, "POST", path, {"minutes": 5, "request_id": "db-1"}).status_code == 200
    assert (f"addtime:{eid}", "db-1") in _used(oj, eid)

    async def wipe():                                            # Redis lost everything (restart / eviction)
        await get_store().delete(f"oj:rid:done:addtime:{eid}:db-1", f"oj:rid:addtime:{eid}:db-1")
    db(oj, lambda s: wipe())
    assert call(oj, "POST", path, {"minutes": 5, "request_id": "db-1"}).status_code == 200
    assert db(oj, lambda s: s.scalar(select(Exam).where(Exam.external_key == KEY))).duration_min == d0 + 5


def test_two_add_time_calls_with_one_id_that_both_passed_the_check_apply_once(oj, room, monkeypatch):
    from app.integrations.dkuoj import routes_v2
    path = f"{V1}/exams/{KEY}/add-time"
    d0 = db(oj, lambda s: s.scalar(select(Exam).where(Exam.external_key == KEY))).duration_min
    assert call(oj, "POST", path, {"minutes": 5, "request_id": "race-1"}).status_code == 200

    async def not_yet_seen(*_a, **_k):                           # the twin read "unused" before the first committed
        return False
    monkeypatch.setattr(routes_v2, "request_used", not_yet_seen)
    r = call(oj, "POST", path, {"minutes": 5, "request_id": "race-1"})
    assert r.status_code == 200                                  # the unique id turns it into a repeat
    assert db(oj, lambda s: s.scalar(select(Exam).where(Exam.external_key == KEY))).duration_min == d0 + 5


def test_two_extra_time_calls_with_one_id_that_both_passed_the_check_apply_once(oj, room, monkeypatch):
    from app.integrations.dkuoj import routes_rescue
    path = f"{STU}/carol/extra-time"
    assert call(oj, "POST", path, {"minutes": 10, "request_id": "race-2"}).status_code == 200

    async def not_yet_seen(*_a, **_k):
        return False
    monkeypatch.setattr(routes_rescue, "request_used", not_yet_seen)
    r = call(oj, "POST", path, {"minutes": 10, "request_id": "race-2"})
    assert r.status_code == 200 and r.json().get("repeated") is True
    from app.integrations.dkuoj.models import OjStudentGrant
    secs = db(oj, lambda s: s.scalar(select(OjStudentGrant.extra_seconds)))
    assert secs == 600


def test_a_crash_after_the_grant_cannot_apply_it_twice(oj, room, monkeypatch):
    """The grant and its request id commit together: whatever fails afterwards (the cache, Redis), a retry is a repeat."""
    from app import exam_control
    path = f"{V1}/exams/{KEY}/add-time"
    d0 = db(oj, lambda s: s.scalar(select(Exam).where(Exam.external_key == KEY))).duration_min

    async def broken_store(*_a, **_k):
        raise RuntimeError("redis down")
    monkeypatch.setattr(exam_control, "invalidate_exam", broken_store)
    call(oj, "POST", path, {"minutes": 10, "request_id": "crash-2"})
    monkeypatch.undo()
    assert call(oj, "POST", path, {"minutes": 10, "request_id": "crash-2"}).status_code == 200
    assert db(oj, lambda s: s.scalar(select(Exam).where(Exam.external_key == KEY))).duration_min == d0 + 10


def _eid(c):
    from sqlalchemy import select as _s
    holder = {}

    async def go(s):
        holder["id"] = (await s.scalar(_s(Exam).where(Exam.external_key == KEY))).id
    db(c, go)
    return holder["id"]


def test_a_failed_request_frees_its_id_and_a_done_one_answers_idempotently(oj, room, monkeypatch):
    from app import exam_control
    path = f"{V1}/exams/{KEY}/add-time"
    before = db(oj, lambda s: s.scalar(select(Exam).where(Exam.external_key == KEY))).duration_min
    real = exam_control.extend_exam

    async def boom(*_a, **_k):
        raise RuntimeError("db down")
    monkeypatch.setattr(exam_control, "extend_exam", boom)
    try:
        call(oj, "POST", path, {"minutes": 5, "request_id": "f-1"})
    except Exception:  # noqa: BLE001
        pass
    monkeypatch.setattr(exam_control, "extend_exam", real)
    assert call(oj, "POST", path, {"minutes": 5, "request_id": "f-1"}).status_code == 200      # freed by the failure
    assert call(oj, "POST", path, {"minutes": 5, "request_id": "f-1"}).status_code == 200      # done: idempotent
    assert db(oj, lambda s: s.scalar(select(Exam).where(Exam.external_key == KEY))).duration_min == before + 5


def close_behind_the_app(c, exam_id):
    """A teacher's close on its own connection (the app's loop is busy running the request)."""
    import asyncio
    from sqlalchemy import update as _u
    from sqlalchemy.ext.asyncio import create_async_engine

    async def go():
        eng = create_async_engine(c.app.state.settings.database_url)
        async with eng.begin() as conn:
            await conn.execute(_u(Exam).where(Exam.id == exam_id).values(status="closed", closed_at=utcnow()))
        await eng.dispose()
    asyncio.run(go())


def test_a_close_between_the_check_and_the_replacement_gives_no_receipt(app_client, exam, monkeypatch):
    """The teacher closes the exam after _replaces_deadline_handin checked it and before its UPDATE:
    the conditional UPDATE (and the row lock on PostgreSQL) keeps the swept hand-in, no replacement."""
    import threading
    from app import routes_sealed
    h, a, answers, deadline = sealed_after_sweep(app_client, exam)
    aid = a["attempt_id"]
    before = results_row(app_client, exam)
    real = routes_sealed.handin_grade

    def grade_then_close(*args, **kw):
        out = real(*args, **kw)
        t = threading.Thread(target=close_behind_the_app, args=(app_client, exam["id"]))
        t.start()
        t.join()
        return out
    monkeypatch.setattr(routes_sealed, "handin_grade", grade_then_close)
    r = post(app_client, h, aid, seal(aid, a["session"], answers, sealed_at=deadline - timedelta(seconds=30)))
    assert r.status_code == 409, r.text                          # a late seal, not a replacement
    row = results_row(app_client, exam)
    assert row["auto_submitted"] is True and row["receipt"] == before["receipt"] and row["score"] == 0


def test_a_grade_saved_after_the_seal_loaded_the_attempt_is_kept_by_the_replacement(app_client, exam, monkeypatch):
    """The teacher grades (own session, locks the attempt row) after sealed_submit loaded the attempt and before
    the replacement: the replacement re-reads the attempt, so the new grade survives for an unchanged answer."""
    import asyncio
    import threading
    from sqlalchemy import update as _u
    from sqlalchemy.ext.asyncio import create_async_engine
    from app import routes_sealed
    from app.models import Attempt as _A
    h, a, answers, deadline = sealed_after_sweep(app_client, exam)
    aid = a["attempt_id"]
    qid = next(iter(answers))
    swept = results_row(app_client, exam)
    assert swept["score"] == 0
    real_seen = routes_sealed._seen

    def teacher_grades():
        async def go():
            eng = create_async_engine(app_client.app.state.settings.database_url)
            async with eng.begin() as conn:
                await conn.execute(_u(_A).where(_A.id == aid).values(
                    grades={qid: {"points": 2, "comment": "ok"}}, score=2, updated_at=utcnow()))
            await eng.dispose()
        asyncio.run(go())

    async def seen_after_teacher(*args, **kw):
        t = threading.Thread(target=teacher_grades)
        t.start()
        t.join()
        return await real_seen(*args, **kw)
    monkeypatch.setattr(routes_sealed, "_seen", seen_after_teacher)
    same = {qid: 0}                       # the swept copy holds {qid: 0}: unchanged answer keeps the grade
    r = post(app_client, h, aid, seal(aid, a["session"], same, sealed_at=deadline - timedelta(seconds=30)))
    assert r.status_code == 200, r.text
    row = results_row(app_client, exam)
    assert row["score"] == 2 and row["auto_submitted"] is False


# ---------------------------------------------------------------- a retried grant repairs a cache step the first call lost
def _cached(c, aid):
    from app.services import attempt_key
    holder = {}

    async def go(_s):
        holder["v"] = await get_store().get(attempt_key(aid))
    db(c, go)
    return holder["v"]


def _plant(c, aid):
    from app.services import attempt_key

    async def go(_s):
        await get_store().set(attempt_key(aid), "stale-deadline", 3600)
    db(c, go)


def test_a_retried_add_time_clears_the_cached_deadline_a_redis_failure_left(oj, room, monkeypatch):
    eid, codes, _, _ = room
    body = sign_in(oj, codes["carol"], DEV_A)
    aid = started(oj, body, eid, DEV_A)["attempt_id"]
    path = f"{V1}/exams/{KEY}/add-time"
    d0 = db(oj, lambda s: s.scalar(select(Exam).where(Exam.external_key == KEY))).duration_min
    _plant(oj, aid)
    store = get_store()
    real_delete = store.delete

    async def down(*_a, **_k):
        raise RuntimeError("redis down")
    monkeypatch.setattr(store, "delete", down)
    assert call(oj, "POST", path, {"minutes": 10, "request_id": "cache-1"}).status_code == 200
    monkeypatch.setattr(store, "delete", real_delete)
    assert _cached(oj, aid) == "stale-deadline"                   # the first call could not clear it
    assert call(oj, "POST", path, {"minutes": 10, "request_id": "cache-1"}).status_code == 200
    assert _cached(oj, aid) is None                               # the retry did
    assert db(oj, lambda s: s.scalar(select(Exam).where(Exam.external_key == KEY))).duration_min == d0 + 10


def test_a_retried_extra_time_clears_the_cached_deadline_a_redis_failure_left(oj, room, monkeypatch):
    eid, codes, _, _ = room
    body = sign_in(oj, codes["carol"], DEV_A)
    aid = started(oj, body, eid, DEV_A)["attempt_id"]
    before = attempts_of(oj)[0].deadline_at
    path = f"{STU}/carol/extra-time"
    _plant(oj, aid)
    store = get_store()
    real_delete = store.delete

    async def down(*_a, **_k):
        raise RuntimeError("redis down")
    monkeypatch.setattr(store, "delete", down)
    assert call(oj, "POST", path, {"minutes": 7, "request_id": "cache-2"}).status_code == 200
    monkeypatch.setattr(store, "delete", real_delete)
    assert _cached(oj, aid) == "stale-deadline"
    assert call(oj, "POST", path, {"minutes": 7, "request_id": "cache-2"}).json().get("repeated") is True
    assert _cached(oj, aid) is None
    assert attempts_of(oj)[0].deadline_at - before == timedelta(minutes=7)
