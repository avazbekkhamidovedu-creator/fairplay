"""2026-10-30: the teacher sets the Duke Box folder from FairPlay's own dashboard (exam create /
update and PATCH tool-settings), for FairPlay and DKU OJ exams."""
import pytest
from sqlalchemy import create_engine, text

from app.models import Exam
from app.security import hash_password

from .conftest import SAMPLE_EXAM, login
from .test_dkuoj_bridge import KEY, db, run
from .test_dkuoj_bridge_v2 import V1, call, oj  # noqa: F401  (fixtures)
from .test_dkuoj_zero_schema_2026_10_24 import ZeroOJ, exam_row, fake, settings_of  # noqa: F401

TOKEN = "AbC123xyz0AbC123xyz0AbC123xyz012"
CANON = f"https://duke.app.box.com/s/{TOKEN}"
GIVEN = f"https://duke.box.com/s/{TOKEN}/?utm=1"
BAD = ["https://duke.box.com/folder/9", "http://duke.box.com/s/" + TOKEN, "https://evil.example/s/" + TOKEN, 7]


@pytest.fixture
def teacher2(client):
    from app.db import get_database
    eng = create_engine(str(get_database().engine.url).replace("sqlite+aiosqlite", "sqlite"))
    with eng.begin() as c:
        c.execute(text("insert into users (username, name, role, password_hash, created_at, token_version) "
                       "values ('teacher2', 'Other Teacher', 'teacher', :h, CURRENT_TIMESTAMP, 0)"),
                  {"h": hash_password("other-pass-123", 1000)})
    eng.dispose()
    return login(client, "teacher2", "other-pass-123")


def create(client, teacher, **extra):
    r = client.post("/api/exams", json={**SAMPLE_EXAM, **extra}, headers=teacher)
    assert r.status_code == 201, r.text
    return r.json()


def patch(client, h, eid, body):
    return client.patch(f"/api/exams/{eid}/tool-settings", json=body, headers=h)


def open_exam(client, teacher, eid):
    return client.post(f"/api/exams/{eid}/open", headers=teacher).json()


def start(client, h, e):
    r = client.post(f"/api/student/exams/{e['id']}/start", headers=h, json={"start_code": e["start_code"]})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- FairPlay exams, create / PUT
def test_create_normalizes_and_returns_to_teacher(client, teacher):
    e = create(client, teacher, box_link=GIVEN)
    assert e["box_link"] == CANON
    assert client.get(f"/api/exams/{e['id']}", headers=teacher).json()["box_link"] == CANON
    assert [x for x in client.get("/api/exams", headers=teacher).json() if x["id"] == e["id"]][0]["box_link"] == CANON


def test_default_has_no_link(client, teacher):
    assert create(client, teacher)["box_link"] is None


@pytest.mark.parametrize("bad", BAD)
def test_create_and_put_refuse_bad_links_like_the_bridge(client, teacher, bad):
    r = client.post("/api/exams", json={**SAMPLE_EXAM, "box_link": bad}, headers=teacher)
    assert r.status_code == 400 and r.json()["detail"].startswith("box_link: ")
    e = create(client, teacher, box_link=CANON)
    r = client.put(f"/api/exams/{e['id']}", json={**SAMPLE_EXAM, "box_link": bad}, headers=teacher)
    assert r.status_code == 400 and r.json()["detail"].startswith("box_link: ")
    assert client.get(f"/api/exams/{e['id']}", headers=teacher).json()["box_link"] == CANON


def test_put_keeps_sets_and_clears(client, teacher):
    e = create(client, teacher, box_link=CANON)
    r = client.put(f"/api/exams/{e['id']}", json={**SAMPLE_EXAM, "title": "T2"}, headers=teacher).json()
    assert r["box_link"] == CANON                          # not sent: kept
    for clear in (None, ""):
        client.put(f"/api/exams/{e['id']}", json={**SAMPLE_EXAM, "box_link": GIVEN}, headers=teacher)
        r = client.put(f"/api/exams/{e['id']}", json={**SAMPLE_EXAM, "box_link": clear}, headers=teacher).json()
        assert r["box_link"] is None


def test_bad_link_messages_equal_the_bridge(oj, fake):
    run(oj)
    t = login(oj, "teacher")
    msgs = {}
    for bad in BAD:
        a = patch(oj, t, exam_row(oj).id, {"box_link": bad})
        b = call(oj, "POST", f"{V1}/exams/{KEY}/settings", {"box_link": bad})
        assert a.status_code == b.status_code == 400
        assert a.json()["detail"] == b.json()["detail"]
        msgs[bad if isinstance(bad, str) else "n"] = a.json()["detail"]
    assert all(m.startswith("box_link: ") for m in msgs.values())


# ---------------------------------------------------------------- PATCH on an open exam
def test_patch_sets_and_clears_on_a_live_exam(client, teacher):
    e = open_exam(client, teacher, create(client, teacher)["id"])
    r = patch(client, teacher, e["id"], {"box_link": GIVEN})
    assert r.status_code == 200 and r.json()["box_link"] == CANON
    assert patch(client, teacher, e["id"], {"box_link": None}).json()["box_link"] is None
    patch(client, teacher, e["id"], {"box_link": CANON})
    assert patch(client, teacher, e["id"], {"box_link": ""}).json()["box_link"] is None
    assert patch(client, teacher, e["id"], {}).status_code == 400
    assert patch(client, teacher, e["id"], {"websites": ["docs.python.org"]}).status_code == 400


def test_changed_link_is_409_after_a_student_started_same_is_accepted(client, teacher, alice):
    e = open_exam(client, teacher, create(client, teacher, box_link=CANON)["id"])
    start(client, alice, e)
    r = patch(client, teacher, e["id"], {"box_link": "https://duke.box.com/s/" + "b" * 20})
    assert r.status_code == 409
    assert r.json()["detail"] == "The Duke Box folder cannot be changed after students have started."
    assert patch(client, teacher, e["id"], {"box_link": None}).status_code == 409
    assert patch(client, teacher, e["id"], {"box_link": GIVEN}).status_code == 200
    assert client.get(f"/api/exams/{e['id']}", headers=teacher).json()["box_link"] == CANON


def test_link_set_before_start_reaches_the_attempt_only(client, teacher, alice):
    e = open_exam(client, teacher, create(client, teacher)["id"])
    patch(client, teacher, e["id"], {"box_link": CANON})
    view = start(client, alice, e)
    assert view["tools"]["box"]["start_url"] == CANON
    assert "box_link" not in view and "box_link" not in view["tools"]
    again = client.get(f"/api/student/attempts/{view['attempt_id']}", headers=alice)
    assert again.json()["tools"]["box"]["start_url"] == CANON and "box_link" not in again.text


def test_students_never_see_box_link(client, teacher, alice):
    e = open_exam(client, teacher, create(client, teacher, box_link=CANON)["id"])
    listing = client.get("/api/student/exams", headers=alice)
    assert listing.status_code == 200 and "box_link" not in listing.text
    assert "box_link" not in str(start(client, alice, e))
    assert client.get("/api/exams", headers=alice).status_code == 403
    assert patch(client, alice, e["id"], {"box_link": CANON}).status_code == 403


def test_other_teacher_cannot_read_or_set(client, teacher, teacher2):
    e = create(client, teacher, box_link=CANON)
    assert client.get(f"/api/exams/{e['id']}", headers=teacher2).status_code == 404
    assert patch(client, teacher2, e["id"], {"box_link": GIVEN}).status_code == 404
    r = client.put(f"/api/exams/{e['id']}", json={**SAMPLE_EXAM, "box_link": None}, headers=teacher2)
    assert r.status_code == 404
    assert all(x["id"] != e["id"] for x in client.get("/api/exams", headers=teacher2).json())
    assert client.get(f"/api/exams/{e['id']}", headers=teacher).json()["box_link"] == CANON


def test_website_exam_has_no_box(client, teacher):
    r = client.post("/api/exams", json={"title": "Site", "duration_min": 30, "mode": "external", "external_url": "https://example.com/q",
                                        "allowed_domains": ["example.com"], "box_link": CANON}, headers=teacher)
    assert r.status_code == 201, r.text
    e = r.json()
    assert e["box_link"] is None


def test_set_and_clear_are_logged_with_the_host_only(client, teacher, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="proctora.teacher")
    e = open_exam(client, teacher, create(client, teacher)["id"])
    patch(client, teacher, e["id"], {"box_link": CANON})
    patch(client, teacher, e["id"], {"box_link": None})
    text_ = "\n".join(r.getMessage() for r in caplog.records)
    assert "Duke Box folder set" in text_ and "Duke Box folder cleared" in text_ and "duke.app.box.com" in text_
    assert TOKEN not in text_


# ---------------------------------------------------------------- DKU OJ exams
def test_dkuoj_exam_put_is_still_read_only(oj, fake):
    run(oj)
    t = login(oj, "teacher")
    r = oj.put(f"/api/exams/{exam_row(oj).id}", json={**SAMPLE_EXAM, "box_link": CANON}, headers=t)
    assert r.status_code == 409


def test_dkuoj_patch_round_trips_with_the_bridge(oj, fake):
    run(oj)
    t = login(oj, "teacher")
    eid = exam_row(oj).id
    r = patch(oj, t, eid, {"box_link": GIVEN, "websites": ["docs.python.org"]})
    assert r.status_code == 200, r.text
    assert r.json()["box_link"] == CANON and r.json()["websites"] == ["docs.python.org"]
    s = settings_of(oj)
    assert s["box_link"] == CANON and "box_link" in s["set_in_fairplay"] and "websites" in s["set_in_fairplay"]
    run(oj)
    assert exam_row(oj).box_link == CANON and exam_row(oj).websites == ["docs.python.org"]
    # last write per field: the bridge changes the link, the dashboard's websites stay
    call(oj, "POST", f"{V1}/exams/{KEY}/settings", {"box_link": "https://duke.box.com/s/" + "c" * 20})
    assert patch(oj, t, eid, {"websites": ["docs.python.org"]}).json()["box_link"] == "https://duke.app.box.com/s/" + "c" * 20
    assert patch(oj, t, eid, {"box_link": ""}).json()["box_link"] is None
    s = settings_of(oj)
    assert s["box_link"] is None and "box_link" not in s["set_in_fairplay"]
    run(oj)
    assert exam_row(oj).box_link is None and exam_row(oj).websites == ["docs.python.org"]


def test_dkuoj_has_no_freeze_rules(oj, fake):
    run(oj)
    t = login(oj, "teacher")
    eid = exam_row(oj).id
    patch(oj, t, eid, {"box_link": CANON, "websites": ["docs.python.org"]})

    async def freeze(s):
        e = await s.get(Exam, eid)
        e.external_frozen_version = "v1"
        await s.commit()
    db(oj, freeze)
    assert patch(oj, t, eid, {"box_link": GIVEN, "websites": ["docs.python.org"]}).status_code == 200
    assert patch(oj, t, eid, {"websites": ["en.cppreference.com"]}).status_code == 200
    assert exam_row(oj).websites == ["en.cppreference.com"]
