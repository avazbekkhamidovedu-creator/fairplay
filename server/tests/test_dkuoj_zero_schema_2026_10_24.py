"""2026-10-24 (contract v2 §11): DKU OJ without schema changes. DKU OJ's exam list carries no
settings (FairPlay keeps every setting, set through POST /exams/<key>/settings, also before the
first sync), lists `active_keys` on its first page (an exam no longer listed there is switched
off), and the hand-in carries FairPlay's close time. Against a fake DKU OJ."""
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.integrations.dkuoj import client as ojclient, sync as ojsync
from app.models import Exam
from app.timeutil import iso, utcnow

from .conftest import login
from .test_dkuoj_bridge import KEY, OJ, TOKEN, db, oj_exam, run, start
from .test_dkuoj_bridge_v2 import V1, RosterOJ, call, oj  # noqa: F401  (fixtures)


class ZeroOJ(RosterOJ):
    """A DKU OJ of §11: no settings in the exam list, active_keys on the first page."""

    def __init__(self):
        super().__init__()
        self.listed = True
        self.active = [KEY]

    def listing(self):
        item = super().listing()
        item.pop("settings")
        return item

    def handle(self, req):
        resp = super().handle(req)
        if req.url.path == "/api/fairplay/v1/exams" and resp.status_code == 200:
            data = resp.json()
            if not self.listed:
                data["exams"] = []
            if self.active is not None and "page=1" in str(req.url.query, "ascii"):
                data["active_keys"] = list(self.active)
                data["active_since"] = iso(utcnow() - timedelta(days=30))
            return httpx.Response(200, json=data)
        return resp


@pytest.fixture
def fake(monkeypatch):
    f = ZeroOJ()
    monkeypatch.setenv("DKUOJ_URL", OJ)
    monkeypatch.setenv("DKUOJ_TOKEN", TOKEN)
    monkeypatch.setattr(ojsync, "BACKGROUND", False)
    ojclient.set_transport_factory(lambda base: httpx.MockTransport(f.handle))

    async def no_sleep(_s):
        return None
    ojclient.set_sleep(no_sleep)
    yield f
    ojclient.set_transport_factory(None)
    ojclient.set_sleep(None)


def settings_of(oj):
    return call(oj, "GET", f"{V1}/exams/{KEY}").json()["settings"]


def exam_row(oj):
    async def q(s):
        return (await s.execute(select(Exam).where(Exam.external_key == KEY))).scalar_one_or_none()
    return db(oj, q)


ALL = {"calculator": "graphing", "open_book": True, "websites": ["Docs https://docs.python.org/3/", "chatgpt.com"],
       "strong_lock": True, "resume_needs_approval": False, "upload_opens_days": 2, "review": "breakdown"}


def test_new_exam_gets_fairplay_defaults_and_list_never_overrides(oj, fake):
    run(oj)
    s = settings_of(oj)
    assert (s["calculator"], s["open_book"], s["websites"], s["strong_lock"], s["resume_needs_approval"],
            s["upload_opens_days"], s["review"]) == ("off", False, [], False, True, 0, "nothing")
    assert s["set_in_fairplay"] == []
    v = call(oj, "POST", f"{V1}/exams/{KEY}/settings", ALL).json()
    s = v["settings"]
    assert s["calculator"] == "graphing" and s["open_book"] and s["strong_lock"] and not s["resume_needs_approval"]
    assert s["websites"] == ["docs.python.org"] and s["website_links"][0]["url"] == "https://docs.python.org/3/"
    assert [b["site"] for b in s["websites_blocked"]] == ["chatgpt.com"]
    assert s["websites_input"] == ["Docs https://docs.python.org/3/", "chatgpt.com"]
    assert s["upload_opens_days"] == 2 and s["review"] == "breakdown"
    assert set(s["set_in_fairplay"]) == {"calculator", "open_book", "websites", "strong_lock",
                                         "resume_needs_approval", "upload_opens_days"}
    # the next syncs (no settings in the list) keep everything
    run(oj)
    run(oj)
    assert settings_of(oj) == s
    e = exam_row(oj)
    assert (e.calculator, e.open_book, e.require_strong_lock, e.resume_needs_approval) == ("graphing", True, True,
                                                                                            False)
    assert e.review_after_handin == "breakdown"


def test_settings_validation(oj, fake):
    run(oj)
    path = f"{V1}/exams/{KEY}/settings"
    for bad in ({}, {"calculator": "abacus"}, {"open_book": "yes"}, {"strong_lock": 1},
                {"resume_needs_approval": "no"},
                {"review": "everything"}, {"review": None}, {"upload_opens_days": 8}, {"websites": "x.com"}):
        assert call(oj, "POST", path, bad).status_code == 400, bad
    assert call(oj, "POST", path, {"calculator": "off"}, acting="alice").status_code == 403
    # null hands a field back (the list has none: the default)
    call(oj, "POST", path, {"calculator": "graphing"})
    s = call(oj, "POST", path, {"calculator": None}).json()["settings"]
    assert s["calculator"] == "off" and "calculator" not in s["set_in_fairplay"]


def test_pending_settings_before_the_first_sync(oj, fake):
    path = f"{V1}/exams/{KEY}/settings"
    v = call(oj, "GET", f"{V1}/exams/{KEY}").json()
    assert v["synced"] is False and v["settings"] is None
    v = call(oj, "POST", path, ALL).json()
    assert v["synced"] is False
    s = v["settings"]
    assert s["calculator"] == "graphing" and s["websites"] == ["docs.python.org"] and s["review"] == "breakdown"
    assert [b["site"] for b in s["websites_blocked"]] == ["chatgpt.com"]
    # a second call merges (only the fields sent change)
    s = call(oj, "POST", path, {"open_book": False, "review": "score"}).json()["settings"]
    assert s["open_book"] is False and s["calculator"] == "graphing" and s["review"] == "score"
    assert call(oj, "GET", f"{V1}/exams/{KEY}").json()["settings"]["review"] == "score"
    # the first sync applies them, and the pending row goes
    run(oj)
    s = settings_of(oj)
    assert s["calculator"] == "graphing" and not s["open_book"] and s["strong_lock"]
    assert s["upload_opens_days"] == 2 and s["review"] == "score"
    assert s["websites"] == ["docs.python.org"] and s["websites_input"][0] == "Docs https://docs.python.org/3/"

    async def pending(s_):
        from app.integrations.dkuoj.models import OjState
        return (await s_.execute(select(OjState.name).where(OjState.name.like("pending:%")))).scalars().all()
    assert db(oj, pending) == []
    run(oj)
    assert settings_of(oj)["review"] == "score"


def test_pending_needs_a_sane_key_and_is_capped(oj, fake, monkeypatch):
    from app.integrations.dkuoj import routes_v2
    assert call(oj, "POST", f"{V1}/exams/{'k' * 60}/settings", {"calculator": "off"}).status_code == 400
    monkeypatch.setattr(routes_v2, "MAX_PENDING", 2)
    for key in ("p1", "p2"):
        assert call(oj, "POST", f"{V1}/exams/{key}/settings", {"calculator": "off"}).status_code == 200
    assert call(oj, "POST", f"{V1}/exams/p3/settings", {"calculator": "off"}).status_code == 429
    assert call(oj, "POST", f"{V1}/exams/p1/settings", {"calculator": "graphing"}).status_code == 200


def test_after_the_first_start_only_review_changes(oj, fake):
    run(oj)
    path = f"{V1}/exams/{KEY}/settings"
    call(oj, "POST", path, {"calculator": "scientific", "websites": ["docs.python.org"]})
    teacher, alice = login(oj, "teacher"), login(oj, "alice")
    v = call(oj, "POST", f"{V1}/exams/{KEY}/open", {"open_to": "all"}).json()
    e = oj_exam(oj, teacher)
    assert start(oj, alice, e["id"], v["start_code"]).status_code == 200
    assert call(oj, "POST", path, {"calculator": "graphing"}).status_code == 409
    assert call(oj, "POST", path, {"websites": ["en.cppreference.com"]}).status_code == 409
    # the same values (DKU OJ sends the whole form): no change, accepted; review changes any time
    r = call(oj, "POST", path, {"calculator": "scientific", "websites": ["docs.python.org"], "open_book": False,
                                "strong_lock": False, "resume_needs_approval": True, "upload_opens_days": 0,
                                "review": "answers"})
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["review"] == "answers"
    assert call(oj, "POST", path, {"review": "nothing"}).json()["settings"]["review"] == "nothing"


def test_active_keys_switch_an_exam_off_and_on(oj, fake):
    run(oj)
    assert exam_row(oj).external_removed is False
    fake.listed, fake.active = False, []
    run(oj)
    assert exam_row(oj).external_removed is True
    # listed again (an unchanged entry is not re-sent: the active key brings it back)
    fake.active = [KEY]
    run(oj)
    assert exam_row(oj).external_removed is False


def test_exams_that_ended_before_the_window_are_left_alone(oj, fake):
    fake.end = utcnow() - timedelta(days=40)
    fake.start = fake.end - timedelta(hours=2)
    run(oj)
    fake.listed, fake.active = False, []
    run(oj)
    assert exam_row(oj).external_removed is False


def test_no_active_keys_from_an_older_dkuoj_changes_nothing(oj, fake):
    run(oj)
    fake.listed, fake.active = False, None
    run(oj)
    assert exam_row(oj).external_removed is False


def test_hand_in_carries_fairplays_close_time(oj, fake):
    run(oj)
    teacher, alice = login(oj, "teacher"), login(oj, "alice")
    v = call(oj, "POST", f"{V1}/exams/{KEY}/open", {"open_to": "all"}).json()
    call(oj, "POST", f"{V1}/exams/{KEY}/add-time", {"minutes": 15})
    e = oj_exam(oj, teacher)
    a = start(oj, alice, e["id"], v["start_code"]).json()
    r = oj.post(f"/api/student/attempts/{a['attempt_id']}/submit", json={"answers": {}, "client_seq": 1},
                headers=alice)
    assert r.status_code == 200, r.text
    run(oj)
    body = fake.submissions[a["attempt_id"]]
    closes = body["exam_closes_at"]
    assert closes.endswith("Z")
    assert abs((ojsync._time(closes) - (fake.end + timedelta(minutes=15))).total_seconds()) < 2


def test_take_pending_checks_the_saver_like_the_editors(oj, caplog):
    import json
    from types import SimpleNamespace
    from app.integrations.dkuoj.models import OjState
    e = SimpleNamespace(external_key="K1", external_frozen_version=None)

    async def take(s, saved, editors):
        value = {"calculator": "graphing"}
        if saved is not False:
            value["saved_by"] = saved
        s.add(OjState(name="pending:K1", value=json.dumps(value)))
        await s.commit()
        meta = {}
        await ojsync.take_pending(s, e, meta, editors)
        await s.commit()
        left = await s.get(OjState, "pending:K1")
        if left is not None:
            await s.delete(left)
            await s.commit()
        return meta.get("fairplay_settings", {}).get("calculator"), left is not None

    assert db(oj, lambda s: take(s, " Alice ", {"alice"})) == ("graphing", False)
    assert db(oj, lambda s: take(s, "mallory", {"alice"})) == (None, False)
    assert "rejected" in caplog.text and "mallory" in caplog.text
    assert db(oj, lambda s: take(s, False, {"alice"})) == ("graphing", False)
    assert db(oj, lambda s: take(s, False, set())) == (None, True)
