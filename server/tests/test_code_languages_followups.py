import json

from app.services import exam_code_languages, student_question

from .conftest import SAMPLE_EXAM
from .test_code_questions import HIDDEN_IN, HIDDEN_OUT, create, exam_body, open_and_start

TESTS = [
    {"input": "", "expected": "1", "visible": True, "points": 2},
    {"input": HIDDEN_IN, "expected": HIDDEN_OUT, "visible": False, "points": 8},
]


def q(**kw):
    return {"type": "code", "prompt": "Write it.", "points": 10, "tests": TESTS, **kw}


def listed(client, teacher, alice, body):
    e = create(client, teacher, body)
    client.post(f"/api/exams/{e['id']}/open", headers=teacher)
    rows = client.get("/api/student/exams", headers=alice).json()
    return e, next(r for r in rows if r["id"] == e["id"])


def test_mixed_exam_lists_sorted_unique_languages(client, teacher, alice):
    body = exam_body(q(language="python"), q(language="sql", sql_setup="create table t(a);"),
                     q(language="python", prompt="Again"))
    _, row = listed(client, teacher, alice, body)
    assert row["code_languages"] == ["python", "sql"]


def test_null_language_uses_the_exam_default(client, teacher, alice):
    _, row = listed(client, teacher, alice, exam_body(q(), q(language="java"), default_language="sql"))
    assert row["code_languages"] == ["java", "sql"]


def test_no_coding_questions_omits_the_field(client, teacher, alice):
    _, row = listed(client, teacher, alice, {**SAMPLE_EXAM})
    assert "code_languages" not in row


def test_default_language_alone_does_not_count_for_a_non_code_exam(client, teacher, alice):
    _, row = listed(client, teacher, alice, {**SAMPLE_EXAM, "default_language": "sql"})
    assert "code_languages" not in row


def test_summary_reveals_nothing_else(client, teacher, alice):
    _, row = listed(client, teacher, alice, exam_body(q(language="sql", sql_setup="SECRET_SETUP")))
    raw = json.dumps(row)
    for secret in (HIDDEN_IN, HIDDEN_OUT, "SECRET_SETUP", "Write it."):
        assert secret not in raw
    assert "questions" not in row and "tests" not in row


def test_dkuoj_questions_are_not_counted():
    qs = [{"type": "code", "language": "cpp", "external_ref": {"x": 1}}, {"type": "mcq"}]
    assert exam_code_languages(qs, "python") == []
    assert exam_code_languages(qs + [{"type": "code"}], "sql") == ["sql"]
    assert exam_code_languages(None, "sql") == []


def test_grading_payload_has_sql_fields_only_for_sql(client, teacher, alice):
    body = exam_body(q(language="sql", sql_setup="create table t(a);", sql_compare="ordered"),
                     q(language="python", prompt="py"), q(prompt="default sql"),
                     default_language="sql")
    e, a = open_and_start(client, teacher, alice, body)
    client.post(f"/api/student/attempts/{a['attempt_id']}/submit", json={"answers": {}, "client_seq": 2},
                headers=alice)
    client.post(f"/api/exams/{e['id']}/close", headers=teacher)
    g = client.get(f"/api/exams/{e['id']}/grading", headers=teacher).json()
    by = {x["prompt"]: x for x in g["questions"] if x["type"] == "code"}
    assert by["Write it."]["sql_setup"] == "create table t(a);" and by["Write it."]["sql_compare"] == "ordered"
    assert by["default sql"]["sql_setup"] == "" and by["default sql"]["sql_compare"] == "unordered"
    assert "sql_setup" not in by["py"] and "sql_compare" not in by["py"]
    assert not any("sql_setup" in x for x in g["questions"] if x["type"] != "code")


def test_student_payload_still_hides_tests_and_points(client, teacher, alice):
    body = exam_body(q(language="sql", sql_setup="s"), q(language="python", prompt="py"))
    _, a = open_and_start(client, teacher, alice, body)
    raw = json.dumps(a)
    assert HIDDEN_IN not in raw and HIDDEN_OUT not in raw
    for x in a["questions"]:
        if x["type"] == "code":
            assert all(set(t) == {"input", "expected"} for t in x["tests"])
    py = next(x for x in a["questions"] if x["type"] == "code" and x["prompt"] == "py")
    assert "sql_setup" not in py
    sq = student_question({"id": "q_1", **q(language="python")})
    assert sq["tests"] == [{"input": "", "expected": "1"}]
