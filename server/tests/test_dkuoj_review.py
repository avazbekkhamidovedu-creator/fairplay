"""2026-10-24: "Student review after hand-in" (app.review, docs/dkuoj-bridge-v2.md §3).

The professor sets it in DKU OJ at any time; it reaches FairPlay through the exam list's
settings and the signed POST /exams/<key>/review (the newer DKU OJ value wins). FairPlay
enforces it on every student endpoint that shows a handed-in exam."""
from datetime import timedelta

import pytest

from app.review import ALL, ANSWERS, NOTHING, REVIEW_CLOSED, review_level
from app.timeutil import iso, utcnow

from .conftest import login
from .test_dkuoj_bridge import KEY, PNG, oj_exam, run, start
from .test_dkuoj_bridge_v2 import V1, call, fake, oj  # noqa: F401  (fixtures)


def _hand_in(oj, fake):
    """Synced exam, opened from DKU OJ; alice answers Q1 and Q5 and hands in."""
    run(oj)
    teacher, alice = login(oj, "teacher"), login(oj, "alice")
    v = call(oj, "POST", f"{V1}/exams/{KEY}/open", {"open_to": "all"}).json()
    e = oj_exam(oj, teacher)
    a = start(oj, alice, e["id"], v["start_code"]).json()
    aid = a["attempt_id"]
    qid = {q["label"]: q["id"] for q in a["questions"]}
    answers = {qid["Q1"]: 1, qid["Q5"]: "Buckets."}
    r = oj.post(f"/api/student/attempts/{aid}/submit", json={"answers": answers, "client_seq": 1}, headers=alice)
    assert r.status_code == 200, r.text
    return e["id"], aid, qid, alice


def _release(oj, fake):
    fake.results = [{"username": "alice", "released": True, "total": 12, "total_points": 100,
                     "updated_at": iso(utcnow()),
                     "questions": [{"question_id": "p000123", "points": 10, "feedback_md": "Good"},
                                   {"question_id": "p000126", "points": 2, "feedback_md": "Say *more*."}]}]
    run(oj, now=fake.end + timedelta(minutes=1))


def _set(oj, review, updated_at=None, acting="teacher"):
    body = {"review": review}
    if updated_at is not None:
        body["updated_at"] = updated_at
    return call(oj, "POST", f"{V1}/exams/{KEY}/review", body, acting=acting)


def test_levels_default_and_native():
    assert review_level("fairplay", None) == ALL          # native exams: unchanged
    assert review_level(None, NOTHING) == ALL
    assert review_level("dkuoj", None) == NOTHING         # DKU OJ: safest until told
    assert review_level("dkuoj", "bogus") == NOTHING
    assert review_level("dkuoj", ANSWERS) == ANSWERS
    assert review_level("otherlms", None) == ALL          # an uninstalled source: as before


def test_each_level_on_each_endpoint(oj, fake):
    fake.review = None                                    # DKU OJ sent nothing yet -> "nothing"
    eid, aid, qid, alice = _hand_in(oj, fake)
    _release(oj, fake)
    assert call(oj, "GET", f"{V1}/exams/{KEY}").json()["settings"]["review"] == NOTHING

    # nothing: "Handed in" only
    g = next(x for x in oj.get("/api/student/grades", headers=alice).json() if x["exam_id"] == eid)
    assert g["review"] == NOTHING and g["released"] is False and g["score"] is None and g["percent"] is None
    ex = next(x for x in oj.get("/api/student/exams", headers=alice).json() if x["id"] == eid)
    assert ex["attempt"]["review"] == NOTHING and ex["attempt"]["percent"] is None
    assert ex["attempt"]["released"] is False and ex["attempt"]["receipt"]
    r = oj.get(f"/api/student/grades/{eid}", headers=alice)
    assert r.status_code == 403 and r.json()["detail"] == REVIEW_CLOSED
    att = oj.get(f"/api/student/attempts/{aid}", headers=alice).json()
    assert att["status"] == "submitted" and att["questions"] == [] and att["answers"] == {}
    assert "Buckets" not in str(att)
    assert oj.get(f"/api/student/grades/{eid}/assets/cell.png", headers=alice).status_code == 404

    # answers: own answers, read-only; no points, feedback, correct answers or average
    v = _set(oj, ANSWERS, iso(utcnow()))
    assert v.status_code == 200 and v.json()["settings"]["review"] == ANSWERS
    d = oj.get(f"/api/student/grades/{eid}", headers=alice).json()
    assert d["review"] == ANSWERS and d["released"] is False
    assert d["score"] is None and d["percent"] is None and d["class_average_percent"] is None
    by = {q["label"]: q for q in d["questions"]}
    assert by["Q1"]["given"] == 1 and by["Q5"]["given"] == "Buckets." and by["Q1"]["status"] == "answered"
    assert by["Q2"]["status"] == "unanswered"
    assert all(q["points_awarded"] is None and q["comment"] == "" and q["correct_answer"] is None
               for q in d["questions"])
    assert "Good" not in str(d) and "more" not in str(d) and "external_ref" not in str(d)
    g = next(x for x in oj.get("/api/student/grades", headers=alice).json() if x["exam_id"] == eid)
    assert g["review"] == ANSWERS and g["released"] is False and g["score"] is None
    ex = next(x for x in oj.get("/api/student/exams", headers=alice).json() if x["id"] == eid)
    assert ex["attempt"]["percent"] is None
    att = oj.get(f"/api/student/attempts/{aid}", headers=alice).json()
    assert att["answers"][qid["Q5"]] == "Buckets."
    r = oj.get(f"/api/student/grades/{eid}/assets/cell.png", headers=alice)
    assert r.status_code == 200 and r.content == PNG

    # answers_and_results: the released result with points and feedback
    _set(oj, ALL, iso(utcnow() + timedelta(seconds=1)))
    d = oj.get(f"/api/student/grades/{eid}", headers=alice).json()
    assert d["review"] == ALL and d["released"] is True and d["score"] == 12
    by = {q["label"]: q for q in d["questions"]}
    assert by["Q1"]["points_awarded"] == 10 and by["Q5"]["comment"] == "Say *more*."
    assert all(q["correct_answer"] is None for q in d["questions"])
    g = next(x for x in oj.get("/api/student/grades", headers=alice).json() if x["exam_id"] == eid)
    assert g["released"] is True and g["score"] == 12
    ex = next(x for x in oj.get("/api/student/exams", headers=alice).json() if x["id"] == eid)
    assert ex["attempt"]["percent"] == 12.0

    # back to nothing (any time): closed again at once
    _set(oj, NOTHING, iso(utcnow() + timedelta(seconds=2)))
    assert oj.get(f"/api/student/grades/{eid}", headers=alice).status_code == 403
    g = next(x for x in oj.get("/api/student/grades", headers=alice).json() if x["exam_id"] == eid)
    assert g["score"] is None and g["released"] is False


def test_results_level_before_release_shows_answers_only(oj, fake):
    fake.review = ALL
    eid, _aid, _qid, alice = _hand_in(oj, fake)
    d = oj.get(f"/api/student/grades/{eid}", headers=alice).json()
    assert d["review"] == ANSWERS and d["released"] is False and d["score"] is None
    _release(oj, fake)
    assert oj.get(f"/api/student/grades/{eid}", headers=alice).json()["score"] == 12


def test_direct_call_checks_and_newer_value_wins(oj, fake):
    # not synced yet
    assert _set(oj, ANSWERS).status_code == 409
    fake.review, fake.review_updated_at = ANSWERS, iso(utcnow() - timedelta(minutes=5))
    run(oj)
    v = call(oj, "GET", f"{V1}/exams/{KEY}").json()
    assert v["settings"]["review"] == ANSWERS
    # editors only, valid values only, signed only
    for who in ("alice", "nobody", ""):
        assert _set(oj, ALL, acting=who).status_code == 403
    for bad in ("all", "", None, 3):
        assert call(oj, "POST", f"{V1}/exams/{KEY}/review", {"review": bad}).status_code == 400
    assert call(oj, "POST", f"{V1}/exams/{KEY}/review", {"review": ALL, "updated_at": 5}).status_code == 400
    assert oj.post(f"{V1}/exams/{KEY}/review", json={"review": ALL}).status_code == 401
    assert call(oj, "GET", f"{V1}/exams/{KEY}").json()["settings"]["review"] == ANSWERS

    # the professor opens results now; a listing fetched before that (older time) can't undo it
    changed = utcnow()
    assert _set(oj, ALL, iso(changed)).json()["settings"]["review"] == ALL
    fake.review, fake.review_updated_at = ANSWERS, iso(changed - timedelta(seconds=30))
    run(oj)
    assert call(oj, "GET", f"{V1}/exams/{KEY}").json()["settings"]["review"] == ALL
    # an older direct call is ignored too
    assert _set(oj, NOTHING, iso(changed - timedelta(seconds=1))).json()["settings"]["review"] == ALL
    # a newer DKU OJ value (from the sync) wins
    fake.review, fake.review_updated_at = NOTHING, iso(changed + timedelta(seconds=30))
    run(oj)
    assert call(oj, "GET", f"{V1}/exams/{KEY}").json()["settings"]["review"] == NOTHING
    # the sync keeps applying it once a student started (frozen exam) and after the close
    eid, _aid, _qid, alice = _hand_in(oj, fake)
    call(oj, "POST", f"{V1}/exams/{KEY}/close")
    fake.review, fake.review_updated_at = ANSWERS, iso(changed + timedelta(minutes=1))
    run(oj)
    assert oj.get(f"/api/student/grades/{eid}", headers=alice).json()["review"] == ANSWERS


@pytest.mark.parametrize("level", [NOTHING, ANSWERS])
def test_teacher_views_unchanged(oj, fake, level):
    """The setting is about students: the professor's views keep everything."""
    fake.review = level
    eid, aid, _qid, _alice = _hand_in(oj, fake)
    teacher = login(oj, "teacher")
    res = oj.get(f"/api/exams/{eid}/results", headers=teacher)
    assert res.status_code == 200
    assert any(x["username"] == "alice" for x in res.json()["attempts"])
