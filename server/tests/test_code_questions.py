"""Phase 4: coding questions. Exam default language, per-question fields and validation,
hidden tests never reaching students (attempt view, exam cache, offline package), teacher
grading (pending until graded, tests on the grading page), results after release, and the
column migration."""
import asyncio
import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.offline import decrypt_package

from .conftest import SAMPLE_EXAM, login

HIDDEN_IN = "999999 123456 hidden-input"
HIDDEN_OUT = "hidden-expected-424242"

CODE_Q = {
    "type": "code", "prompt": "Read n numbers and print the largest.", "points": 10,
    "starter_code": "n = int(input())\n",
    "tests": [
        {"input": "3\n1 5 2\n", "expected": "5\n", "visible": True, "points": 2},
        {"input": "1\n-7\n", "expected": "-7", "visible": True, "points": 3},
        {"input": HIDDEN_IN, "expected": HIDDEN_OUT, "visible": False, "points": 5},
    ],
    "time_limit_ms": 1500,
}


def exam_body(*questions, **extra):
    return {**SAMPLE_EXAM, "questions": [*SAMPLE_EXAM["questions"], *questions], **extra}


def create(client, teacher, body):
    r = client.post("/api/exams", json=body, headers=teacher)
    assert r.status_code == 201, r.text
    return r.json()


def code_of(exam):
    return next(q for q in exam["questions"] if q["type"] == "code")


def open_and_start(client, teacher, alice, body):
    e = create(client, teacher, body)
    e = client.post(f"/api/exams/{e['id']}/open", headers=teacher).json()
    a = client.post(f"/api/student/exams/{e['id']}/start", json={"start_code": e["start_code"]},
                    headers=alice)
    assert a.status_code in (200, 201), a.text
    return e, a.json()


# ---------------------------------------------------------------- teacher: settings + fields
def test_code_question_saved_for_the_teacher(client, teacher):
    e = create(client, teacher, exam_body(CODE_Q, default_language="javascript"))
    assert e["default_language"] == "javascript"
    q = code_of(e)
    assert q["language"] is None            # null = the exam default
    assert q["starter_code"] == "n = int(input())\n" and q["write_only"] is False
    assert q["time_limit_ms"] == 1500 and q["points"] == 10
    assert q["tests"] == CODE_Q["tests"]    # teacher sees hidden tests too
    # other question types carry no coding fields
    for other in e["questions"]:
        if other["type"] != "code":
            assert not {"language", "tests", "starter_code", "write_only", "time_limit_ms"} & set(other)


def test_default_language_defaults_and_is_kept_on_put(client, teacher):
    e = create(client, teacher, SAMPLE_EXAM)
    assert e["default_language"] == "python"
    e = client.put(f"/api/exams/{e['id']}", json={**SAMPLE_EXAM, "default_language": "cpp"},
                   headers=teacher).json()
    assert e["default_language"] == "cpp"
    e = client.put(f"/api/exams/{e['id']}", json={**SAMPLE_EXAM, "title": "Other"}, headers=teacher).json()
    assert e["default_language"] == "cpp"   # not sent: kept


def test_code_defaults(client, teacher):
    q = code_of(create(client, teacher, exam_body({"type": "code", "prompt": "Hello", "points": 1})))
    assert q == {**q, "language": None, "starter_code": "", "tests": [], "write_only": False,
                 "time_limit_ms": 2000}


@pytest.mark.parametrize("patch", [
    {"language": "ruby"},
    {"time_limit_ms": 50},
    {"time_limit_ms": 10_001},
    {"tests": [{"input": "", "expected": "1", "visible": True, "points": 4}]},   # 4 != 10 points
    {"tests": [{"input": "", "expected": "1", "visible": True, "points": -1}]},
    {"tests": [{"input": "", "expected": "", "visible": True, "points": 0}] * 51},
    {"tests": [{"input": "x" * 20_001, "expected": "", "visible": True}]},
    {"starter_code": "x" * 20_001},
])
def test_code_question_validation(client, teacher, patch):
    r = client.post("/api/exams", json=exam_body({**CODE_Q, **patch}), headers=teacher)
    assert r.status_code == 422, patch


def test_tests_without_points_need_no_sum(client, teacher):
    tests = [{"input": "1", "expected": "1", "visible": True}, {"input": "2", "expected": "2"}]
    q = code_of(create(client, teacher, exam_body({**CODE_Q, "tests": tests})))
    assert [t["points"] for t in q["tests"]] == [0, 0] and [t["visible"] for t in q["tests"]] == [True, False]


def test_bad_default_language_and_paper_refused(client, teacher):
    assert client.post("/api/exams", json=exam_body(CODE_Q, default_language="go"),
                       headers=teacher).status_code == 422
    r = client.post("/api/exams", json=exam_body(CODE_Q, delivery="paper"), headers=teacher)
    assert r.status_code == 422 and "paper" in r.text


def test_code_fields_dropped_from_other_types(client, teacher):
    mcq = {**SAMPLE_EXAM["questions"][0], "language": "java", "tests": [{"input": "", "expected": "x"}],
           "starter_code": "x", "write_only": True}
    e = create(client, teacher, {**SAMPLE_EXAM, "questions": [mcq]})
    assert not {"language", "tests", "starter_code", "write_only"} & set(e["questions"][0])


# ---------------------------------------------------------------- students never get hidden tests
def test_student_view_has_only_visible_tests(client, teacher, alice):
    over = {**CODE_Q, "prompt": "Second", "language": "cpp", "write_only": True,
            "tests": [{**t, "points": 0} for t in CODE_Q["tests"]]}
    e, a = open_and_start(client, teacher, alice, exam_body(CODE_Q, over, default_language="javascript"))
    for view in (a, client.get(f"/api/student/attempts/{a['attempt_id']}", headers=alice).json()):
        raw = json.dumps(view)
        assert HIDDEN_IN not in raw and HIDDEN_OUT not in raw
        qs = [q for q in view["questions"] if q["type"] == "code"]
        assert len(qs) == 2
        first = next(q for q in qs if q["prompt"] == CODE_Q["prompt"])
        second = next(q for q in qs if q["prompt"] == "Second")
        assert first["language"] == "javascript" and second["language"] == "cpp"   # resolved
        assert first["tests"] == [{"input": "3\n1 5 2\n", "expected": "5\n"},
                                  {"input": "1\n-7\n", "expected": "-7"}]
        assert second["write_only"] is True and first["time_limit_ms"] == 1500
        assert first["starter_code"] == "n = int(input())\n"


def test_offline_package_has_only_visible_tests(client, teacher, alice):
    e = create(client, teacher, exam_body(CODE_Q))
    e = client.post(f"/api/exams/{e['id']}/open", headers=teacher).json()
    pkg = client.get(f"/api/student/exams/{e['id']}/package", headers=alice).json()
    pk = client.get(f"/api/student/exams/{e['id']}/package-key", headers=alice).json()["key"]
    plain = decrypt_package(pkg, e["start_code"], package_key=pk)
    raw = json.dumps(plain)
    assert HIDDEN_IN not in raw and HIDDEN_OUT not in raw
    q = code_of(plain)
    assert q["language"] == "python" and len(q["tests"]) == 2
    assert all(set(t) == {"input", "expected"} for t in q["tests"])


def test_student_question_strips_hidden_tests():
    from app.services import student_question
    stored = {"id": "q_1", "type": "code", "prompt": "p", "points": 3, "language": None,
              "tests": [{"input": "a", "expected": "b", "visible": False, "points": 3},
                        {"input": "c", "expected": "d", "visible": True, "points": 0}]}
    sq = student_question(stored, "java")
    assert sq["tests"] == [{"input": "c", "expected": "d"}] and sq["language"] == "java"
    assert student_question(stored)["language"] == "python"     # no exam default: python
    assert "tests" not in student_question({"id": "q_2", "type": "short", "prompt": "p",
                                            "accepted_answers": ["x"]})


# ---------------------------------------------------------------- answers + grading
def submit(client, alice, aid, answers):
    r = client.post(f"/api/student/attempts/{aid}/submit", json={"answers": answers, "client_seq": 5},
                    headers=alice)
    assert r.status_code == 200, r.text
    return r.json()


def test_code_answer_is_pending_until_the_teacher_grades(client, teacher, alice):
    e, a = open_and_start(client, teacher, alice, exam_body(CODE_Q))
    qid = code_of(a)["id"]
    code = "n = int(input())\nprint(max(map(int, input().split())))\n"
    r = client.put(f"/api/student/attempts/{a['attempt_id']}/answers",
                   json={"answers": {qid: code}, "client_seq": 1}, headers=alice)
    assert r.status_code == 200, r.text
    submit(client, alice, a["attempt_id"], {qid: code})
    client.post(f"/api/exams/{e['id']}/close", headers=teacher)

    g = client.get(f"/api/exams/{e['id']}/grading", headers=teacher).json()
    gq = next(q for q in g["questions"] if q["question_id"] == qid)
    assert gq["type"] == "code" and gq["language"] == "python" and gq["time_limit_ms"] == 1500
    assert gq["tests"] == CODE_Q["tests"] and gq["to_grade"] == 1      # hidden tests for the teacher
    item = next(i for i in g["items"] if i["question_id"] == qid)
    assert item["answer"] == code and item["auto_points"] is None and item["grade"] is None

    res = client.get(f"/api/exams/{e['id']}/results", headers=teacher).json()
    assert res["pending_grading"] == 1
    r = client.put(f"/api/exams/{e['id']}/attempts/{a['attempt_id']}/grades/{qid}",
                   json={"points": 7, "comment": "Passed 2 of 3 tests."}, headers=teacher)
    assert r.status_code == 200, r.text
    assert r.json()["needs_grading"] == 0
    assert client.put(f"/api/exams/{e['id']}/attempts/{a['attempt_id']}/grades/{qid}",
                      json={"points": 11, "comment": ""}, headers=teacher).status_code == 422

    client.post(f"/api/exams/{e['id']}/release", headers=teacher)
    fb = client.get(f"/api/student/grades/{e['id']}", headers=alice).json()
    fq = next(q for q in fb["questions"] if q["question_id"] == qid)
    assert fq["given"] == code and fq["points_awarded"] == 7 and fq["status"] == "partial"
    assert fq["language"] == "python" and fq["comment"] == "Passed 2 of 3 tests."
    assert fq["correct_answer"] is None
    raw = json.dumps(fb)
    assert HIDDEN_IN not in raw and HIDDEN_OUT not in raw


def test_blank_code_answer_is_zero_not_pending(client, teacher, alice):
    e, a = open_and_start(client, teacher, alice, exam_body(CODE_Q))
    submit(client, alice, a["attempt_id"], {})
    res = client.get(f"/api/exams/{e['id']}/results", headers=teacher).json()
    assert res["pending_grading"] == 0


def test_code_answer_size_limit(client, teacher, alice):
    e, a = open_and_start(client, teacher, alice, exam_body(CODE_Q))
    qid = code_of(a)["id"]
    r = client.put(f"/api/student/attempts/{a['attempt_id']}/answers",
                   json={"answers": {qid: "x" * 50_001}, "client_seq": 1}, headers=alice)
    assert r.status_code == 422


def test_grade_attempt_code_rules():
    from app.grading import grade_attempt
    qs = [{"id": "q_c", "type": "code", "points": 4}]
    _, pending, rows = grade_attempt(qs, {"q_c": "print(1)"}, {})
    assert pending == 1 and rows[0]["pending"] and rows[0]["points_awarded"] == 0
    _, pending, rows = grade_attempt(qs, {"q_c": "  "}, {})
    assert pending == 0 and not rows[0]["pending"]
    score, pending, rows = grade_attempt(qs, {"q_c": "print(1)"}, {"q_c": {"points": 4}})
    assert score == 4 and pending == 0 and rows[0]["correct"]


# ---------------------------------------------------------------- migration
def test_old_database_gets_default_language(tmp_path):
    from app.config import Settings
    from app.db import Database

    url = f"sqlite+aiosqlite:///{tmp_path / 'old.db'}"

    async def go():
        db = Database(Settings(database_url=url))
        await db.create_all()
        await db.dispose()
        eng = create_async_engine(url)
        async with eng.begin() as conn:
            await conn.execute(text("ALTER TABLE exams DROP COLUMN default_language"))
            await conn.execute(text("INSERT INTO users (id, username, name, role, password_hash, created_at) "
                                    "VALUES (1, 't', 'T', 'teacher', 'x', '2026-01-01 00:00:00')"))
            await conn.execute(text("INSERT INTO exams (id, teacher_id, title, duration_min, status, questions, "
                                    "question_count, total_points, created_at, updated_at) VALUES "
                                    "('e_old', 1, 'Old', 10, 'draft', '[]', 0, 0, '2026-01-01 00:00:00', "
                                    "'2026-01-01 00:00:00')"))
        await eng.dispose()
        db = Database(Settings(database_url=url))
        await db.create_all()
        await db.dispose()
        eng = create_async_engine(url)
        async with eng.connect() as conn:
            value = (await conn.execute(text("SELECT default_language FROM exams"))).scalar_one()
        await eng.dispose()
        return value

    assert asyncio.run(go()) == "python"


def test_wasm_served_with_its_type(client, static_dir):
    (static_dir / "pyodide").mkdir(exist_ok=True)
    (static_dir / "pyodide" / "pyodide.asm.wasm").write_bytes(b"\x00asm\x01\x00\x00\x00")
    r = client.get("/pyodide/pyodide.asm.wasm")
    assert r.status_code == 200 and r.headers["content-type"] == "application/wasm"
