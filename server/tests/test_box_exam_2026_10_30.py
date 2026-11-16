"""2026-10-30: open an exam with Duke Box: box_link normalization, the DKU OJ settings round
trip (also before the first sync), tools.box only for a running attempt, the profiles, the
box_opened event, no Box companions, scripts/box_trace.py."""
import importlib.util
import json
import os
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import box_profiles as bp
from app.integrations.dkuoj import sync as ojsync  # noqa: F401
from app.models import Attempt, Exam
from app.timeutil import utcnow

from .test_dkuoj_bridge import KEY, db, run
from .test_dkuoj_bridge_v2 import V1, call, oj  # noqa: F401  (fixtures)
from .test_dkuoj_zero_schema_2026_10_24 import ZeroOJ, exam_row, fake, settings_of  # noqa: F401

TOKEN = "AbC123xyz0AbC123xyz0AbC123xyz012"
CANON = f"https://duke.app.box.com/s/{TOKEN}"


# ---------------------------------------------------------------- normalization
@pytest.mark.parametrize("given", [
    f"https://duke.box.com/s/{TOKEN}", f"https://duke.app.box.com/s/{TOKEN}",
    f"  https://DUKE.box.com/s/{TOKEN}/  ", f"https://duke.box.com/s/{TOKEN}?utm_source=x#frag",
    "https://duke.box.com/s/abcdefghij", "https://duke.box.com/s/" + "a" * 64,
])
def test_valid_links_normalize(given):
    token = given.split("/s/")[1].split("/")[0].split("?")[0]
    assert bp.normalize_link(given) == f"https://duke.app.box.com/s/{token}"


@pytest.mark.parametrize("bad", [
    "https://duke.box.com/folder/12345", "https://duke.app.box.com/file/12345",
    f"https://app.box.com/s/{TOKEN}", f"https://other.app.box.com/s/{TOKEN}", f"https://box.com/s/{TOKEN}",
    f"https://evil.example/s/{TOKEN}", f"https://duke.box.com.evil.example/s/{TOKEN}",
    f"http://duke.box.com/s/{TOKEN}", f"//duke.box.com/s/{TOKEN}", f"duke.box.com/s/{TOKEN}",
    f"https://user@duke.box.com/s/{TOKEN}", f"https://user:pw@duke.box.com/s/{TOKEN}",
    f"https://duke.box.com:8443/s/{TOKEN}", f"https://duke.box.com:443/s/{TOKEN}",
    "https://duke.box.com/s/short", "https://duke.box.com/s/" + "a" * 65, "https://duke.box.com/s/ab-cdefghijk",
    f"https://duke.box.com/s/{TOKEN}/folder/1", f"https://duke.box.com/s/{TOKEN}/file/1",
    f"https://duke.box.com/x/s/{TOKEN}", "https://duke.box.com/s/", "https://duke.box.com/",
    f"https://duke.box.com/s/{TOKEN} extra", "https://duke.box.com/s/" + "a" * 3000, 5, ["x"], {"a": 1},
])
def test_bad_links_refused(bad):
    with pytest.raises(bp.BoxLinkError) as e:
        bp.normalize_link(bad)
    assert str(e.value)


def test_stored_link_check():
    assert bp.checked_link(None) == (None, None) and bp.checked_link("") == (None, None)
    assert bp.checked_link(CANON) == (CANON, None)
    link, err = bp.checked_link("https://duke.box.com/folder/1")
    assert link is None and err


def test_without_box_hosts():
    assert bp.without_box_hosts(["docs.python.org", "duke.app.box.com", "*.box.com", "notbox.com"]) \
        == ["docs.python.org", "notbox.com"]


# ---------------------------------------------------------------- profiles
def test_profile_selection(monkeypatch):
    monkeypatch.delenv("BOX_PROFILE", raising=False)
    monkeypatch.delenv("FAIRPLAY_DEV", raising=False)
    assert bp.selected_profile()[0] == "duke"
    with pytest.raises(bp.BoxProfileError):
        bp.selected_profile("fakebox")
    monkeypatch.setenv("BOX_PROFILE", "fakebox")
    with pytest.raises(bp.BoxProfileError):
        bp.selected_profile()
    assert bp.box_tools(CANON) is None            # refused outside dev: no Box pane
    monkeypatch.setenv("FAIRPLAY_DEV", "1")
    assert bp.selected_profile()[0] == "fakebox"
    assert bp.box_tools(CANON)["asset_hosts"] == ["fakebox-assets.test"]
    monkeypatch.setenv("BOX_PROFILE", "nope")
    with pytest.raises(bp.BoxProfileError):
        bp.selected_profile()


def test_tools_box_shape(monkeypatch):
    monkeypatch.delenv("BOX_PROFILE", raising=False)
    t = bp.box_tools(CANON)
    assert set(t) == {"start_url", "app_hosts", "path_prefix", "sign_in_hosts", "asset_hosts", "blocked_requests"}
    assert t["start_url"] == CANON and t["app_hosts"] == ["duke.app.box.com", "duke.box.com"]
    assert t["path_prefix"] == "/s/" + TOKEN
    assert {"host": "duke.account.box.com", "paths": ["/login*"]} in t["sign_in_hosts"]
    assert {"host": "*.duosecurity.com", "paths": None} in t["sign_in_hosts"]
    assert "*.boxcloud.com" in t["asset_hosts"] and t["blocked_requests"]
    assert all({"host", "path"} <= set(x) for x in t["blocked_requests"])


def _account_paths(t):
    return {x["host"]: x["paths"] for x in t["sign_in_hosts"] if x["host"].endswith("account.box.com")}


def test_sso_return_path_only_for_apps_that_understand_it(monkeypatch):
    """2026-11-16: duke.account.box.com/sso/ping_federate* only for apps >= 0.8.9; older, unknown or no version
    keep /login* only (0.8.7/0.8.8 refuse a policy with other account paths)."""
    monkeypatch.delenv("BOX_PROFILE", raising=False)
    old = {"duke.account.box.com": ["/login*"], "account.box.com": ["/login*"]}
    new = {h: ["/login*", "/sso/ping_federate*"] for h in old}
    for v in (None, "", "0.8.7", "0.8.8", "0.8.8.9", "garbage"):
        assert _account_paths(bp.box_tools(CANON, v)) == old, v
    for v in ("0.8.9", "0.9", "0.10.0", "1.0"):
        assert _account_paths(bp.box_tools(CANON, v)) == new, v
    assert _account_paths(bp.box_tools(CANON)) == old
    # nothing else changes, and the profile itself is never mutated
    t_old, t_new = bp.box_tools(CANON, "0.8.8"), bp.box_tools(CANON, "0.8.9")
    assert [x for x in t_old["sign_in_hosts"] if "account" not in x["host"]] == \
        [x for x in t_new["sign_in_hosts"] if "account" not in x["host"]]
    assert bp.PROFILES["duke"]["sign_in_hosts"][0]["paths"] == ["/login*"]


# ---------------------------------------------------------------- companions
def test_box_is_no_companion():
    from app.site_companions import allowed_sites, companions, companions_for
    for host in ("duke.app.box.com", "duke.box.com", "duke.account.box.com", "app.box.com"):
        assert companions_for(host) == (None, [])
    sites = ["duke.app.box.com", "docs.python.org"]
    assert companions(sites) == [] and allowed_sites(sites) == sites


# ---------------------------------------------------------------- the bridge
def post(oj, body, key=KEY):
    return call(oj, "POST", f"{V1}/exams/{key}/settings", body)


def test_settings_round_trip(oj, fake):
    run(oj)
    r = post(oj, {"box_link": "https://duke.box.com/s/" + TOKEN + "?x=1"})
    assert r.status_code == 200, r.text
    s = r.json()["settings"]
    assert s["box_link"] == CANON and s["box_link_error"] is None and "box_link" in s["set_in_fairplay"]
    assert settings_of(oj)["box_link"] == CANON
    assert exam_row(oj).box_link == CANON
    run(oj)                                           # a later sync keeps it
    assert exam_row(oj).box_link == CANON
    assert post(oj, {"box_link": ""}).json()["settings"]["box_link"] is None
    assert exam_row(oj).box_link is None
    post(oj, {"box_link": CANON})
    s = post(oj, {"box_link": None}).json()["settings"]
    assert s["box_link"] is None and "box_link" not in s["set_in_fairplay"]


def test_refused_link_is_a_400_and_changes_nothing(oj, fake):
    run(oj)
    post(oj, {"box_link": CANON})
    for bad in ("https://duke.box.com/folder/9", "http://duke.box.com/s/" + TOKEN, "https://evil.example/s/" + TOKEN, 7):
        r = post(oj, {"box_link": bad})
        assert r.status_code == 400 and "box_link" in r.json()["detail"], bad
    assert settings_of(oj)["box_link"] == CANON


def test_stored_invalid_link_is_echoed_as_error(oj, fake):
    run(oj)

    async def bad(s):
        e = (await s.execute(select(Exam).where(Exam.external_key == KEY))).scalar_one()
        e.box_link = "https://duke.box.com/folder/1"
        await s.commit()
    db(oj, bad)
    s = settings_of(oj)
    assert s["box_link"] is None and "shared link" in s["box_link_error"]


def test_pending_settings_carry_the_link(oj, fake):
    r = post(oj, {"box_link": "https://duke.box.com/s/" + TOKEN}, key="later-key")
    s = r.json()["settings"]
    assert r.json()["synced"] is False and s["box_link"] == CANON and "box_link" in s["set_in_fairplay"]
    assert post(oj, {"box_link": "https://duke.box.com/file/1"}, key="later-key").status_code == 400
    assert call(oj, "GET", f"{V1}/exams/later-key").json()["settings"]["box_link"] == CANON
    # the exam arrives under that key: the pending link is applied
    post(oj, {"box_link": CANON}, key=KEY)
    run(oj)
    assert exam_row(oj).box_link == CANON


def test_box_host_in_websites_is_not_framed(oj, fake):
    run(oj)
    post(oj, {"box_link": CANON, "websites": ["https://duke.app.box.com/folder/0", "docs.python.org"]})
    s = settings_of(oj)
    assert s["websites"] == ["docs.python.org"] and s["companions"] == []
    assert any(b["site"] == "duke.app.box.com" and "Duke Box" in b["reason"] for b in s["websites_blocked"])


def test_link_is_fixed_after_the_first_start(oj, fake):
    run(oj)
    post(oj, {"box_link": CANON})

    async def freeze(s):
        e = (await s.execute(select(Exam).where(Exam.external_key == KEY))).scalar_one()
        e.external_frozen_version = "v1"
        await s.commit()
    db(oj, freeze)
    assert post(oj, {"box_link": CANON}).status_code == 200
    assert post(oj, {"box_link": "https://duke.box.com/s/" + "b" * 20}).status_code == 409


# ---------------------------------------------------------------- tools.box on the attempt view
def set_link(client, exam_id, link):
    from app.db import get_database
    from app.services import invalidate_exam
    from app.store import get_store

    async def go():
        async with get_database().sessionmaker() as s:
            (await s.get(Exam, exam_id)).box_link = link
            await s.commit()
        await invalidate_exam(get_store(), exam_id)
    client.portal.call(go)


def start(client, h, e):
    return client.post(f"/api/student/exams/{e['id']}/start", headers=h, json={"start_code": e["start_code"]}).json()


def test_tools_box_only_while_running(client, teacher, alice, live_exam, monkeypatch):
    monkeypatch.delenv("BOX_PROFILE", raising=False)
    eid = live_exam["id"]
    set_link(client, eid, CANON)
    lst = client.get("/api/student/exams", headers=alice).json()
    assert "box" not in next(x for x in lst if x["id"] == eid)["tools"]          # not started
    a = start(client, alice, live_exam)
    assert a["tools"]["box"]["start_url"] == CANON and a["tools"]["box"]["path_prefix"] == "/s/" + TOKEN
    aid = a["attempt_id"]
    got = client.get(f"/api/student/attempts/{aid}", headers=alice).json()
    assert got["tools"]["box"] == a["tools"]["box"]
    assert "box" not in next(x for x in client.get("/api/student/exams", headers=alice).json()
                             if x["id"] == eid)["tools"]
    # the teacher's view has the link
    assert client.get(f"/api/exams/{eid}", headers=teacher).json()["box_link"] == CANON
    # exam closed
    assert client.post(f"/api/exams/{eid}/close", headers=teacher).status_code == 200
    r = client.get(f"/api/student/attempts/{aid}", headers=alice)
    assert r.status_code != 200 or "box" not in r.json()["tools"]


def test_tools_box_absent_when_submitted_or_no_link(client, teacher, alice, bob, live_exam):
    eid = live_exam["id"]
    a = start(client, alice, live_exam)
    assert "box" not in a["tools"]                                              # no link
    set_link(client, eid, CANON)
    assert "box" in client.get(f"/api/student/attempts/{a['attempt_id']}", headers=alice).json()["tools"]
    assert client.post(f"/api/student/attempts/{a['attempt_id']}/submit", headers=alice, json={}).status_code == 200
    r = client.get(f"/api/student/attempts/{a['attempt_id']}", headers=alice)
    assert r.status_code == 200 and "box" not in r.json()["tools"]
    set_link(client, eid, "https://duke.box.com/folder/1")                      # invalid stored value
    b = start(client, bob, live_exam)
    assert "box" not in b["tools"]


def test_running_box_tools_conditions():
    from app.services import running_box_tools
    now = utcnow()
    a = Attempt(id="a", exam_id="e", student_id=1, status="in_progress", deadline_at=now + timedelta(minutes=5))
    exam = {"status": "live", "box_link": CANON}
    assert running_box_tools(a, exam, now)["start_url"] == CANON
    assert running_box_tools(a, exam, now + timedelta(minutes=6)) is None       # past the deadline
    assert running_box_tools(a, {**exam, "status": "closed"}, now) is None
    assert running_box_tools(a, {**exam, "status": "draft"}, now) is None
    assert running_box_tools(a, {**exam, "box_link": None}, now) is None
    assert running_box_tools(a, {"status": "live"}, now) is None               # cached before the field
    a.status = "submitted"
    assert running_box_tools(a, exam, now) is None


def test_attempt_view_sends_sso_path_by_app_version(client, alice, live_exam):
    set_link(client, live_exam["id"], CANON)
    a = start(client, alice, live_exam)
    url = f"/api/student/attempts/{a['attempt_id']}"
    paths = lambda r: _account_paths(r.json()["tools"]["box"])["duke.account.box.com"]
    assert paths(client.get(url, headers=alice)) == ["/login*"]
    assert paths(client.get(url, headers={**alice, "X-FairPlay-Version": "0.8.8"})) == ["/login*"]
    assert paths(client.get(url, headers={**alice, "X-FairPlay-Version": "0.8.9"})) == ["/login*", "/sso/ping_federate*"]


def test_fakebox_profile_outside_dev_gives_no_pane(client, alice, live_exam, monkeypatch):
    set_link(client, live_exam["id"], CANON)
    monkeypatch.setenv("BOX_PROFILE", "fakebox")
    monkeypatch.delenv("FAIRPLAY_DEV", raising=False)
    assert "box" not in start(client, alice, live_exam)["tools"]


def test_offline_tools_have_no_box(live_exam):
    from app.offline import offline_tools
    assert "box" not in offline_tools({"mode": "questions", "status": "live", "box_link": CANON})


# ---------------------------------------------------------------- events
def test_box_opened_event_is_accepted_as_is(client, teacher, alice, live_exam):
    from app.app_client import CLIENT_EVENT_TYPES, client_event_type
    assert "box_opened" in CLIENT_EVENT_TYPES and client_event_type("box_opened") == "box_opened"
    a = start(client, alice, live_exam)
    r = client.post(f"/api/student/attempts/{a['attempt_id']}/events", headers=alice,
                    json={"events": [{"type": "box_opened", "detail": "duke.app.box.com", "client_time": "t"},
                                     {"type": "blocked_navigation", "detail": "box duke.app.box.com /upload",
                                      "client_time": "t"}]})
    assert r.status_code == 200
    res = client.get(f"/api/exams/{live_exam['id']}/results", headers=teacher).json()
    types = [ev["type"] for x in res["attempts"] for ev in x["events"]]
    assert "box_opened" in types and "client_box_opened" not in types and "blocked_navigation" in types


# ---------------------------------------------------------------- scripts/box_trace.py
def load_trace():
    path = os.path.join(os.path.dirname(__file__), "..", "scripts", "box_trace.py")
    spec = importlib.util.spec_from_file_location("box_trace", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HAR = {"log": {"entries": [
    {"request": {"url": "https://duke.app.box.com/s/SECRETTOKEN?sid=abc", "headers": [{"name": "Cookie", "value": "SESSIONCOOKIE"}],
                 "postData": {"text": "BODYSECRET"}}},
    {"request": {"url": "https://duke.app.box.com/api/2.0/files?token=QSECRET"}},
    {"request": {"url": "https://duke.app.box.com/api/2.0/folders"}},
    {"request": {"url": "https://cdn01.boxcdn.net/a.js"}},
    {"request": {"url": "https://api-1234.duosecurity.com/frame/web/v1/auth"}},
    {"request": {"url": "https://Duke.account.box.com/login"}},
    {"request": {}}, {"request": {"url": 5}}, {},
]}}


def test_box_trace_summary_and_output(tmp_path, capsys):
    mod = load_trace()
    s = mod.summarize(HAR)
    assert sorted(s) == ["api-1234.duosecurity.com", "cdn01.boxcdn.net", "duke.account.box.com", "duke.app.box.com"]
    assert s["duke.app.box.com"]["requests"] == 3 and s["duke.app.box.com"]["paths"] == {"/s": 1, "/api": 2}
    f = tmp_path / "t.har"
    f.write_text(json.dumps(HAR))
    assert mod.main(["box_trace", str(f)]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("api-1234.duosecurity.com\t1\t/frame x1")
    for secret in ("SECRETTOKEN", "SESSIONCOOKIE", "BODYSECRET", "QSECRET", "sid="):
        assert secret not in out
    assert mod.main(["box_trace", str(tmp_path / "missing.har")]) == 1
    assert mod.main(["box_trace"]) == 2
    assert [p.name for p in tmp_path.iterdir()] == ["t.har"]                      # wrote nothing
