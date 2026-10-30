import pytest

from app.services import student_question

from .conftest import SAMPLE_EXAM
from .test_code_questions import HIDDEN_IN, HIDDEN_OUT, code_of, create, exam_body, open_and_start

LANGS = ("python", "javascript", "typescript", "c", "cpp", "java", "sql")

TESTS = [
    {"input": "", "expected": "1", "visible": True, "points": 2},
    {"input": HIDDEN_IN, "expected": HIDDEN_OUT, "visible": False, "points": 8},
]


def q(**kw):
    return {"type": "code", "prompt": "Write it.", "points": 10, "tests": TESTS, **kw}


def post(client, teacher, body):
    return client.post("/api/exams", json=body, headers=teacher)


@pytest.mark.parametrize("lang", LANGS)
def test_all_languages_accepted(client, teacher, lang):
    e = create(client, teacher, exam_body(q(language=lang), default_language=lang))
    assert code_of(e)["language"] == lang and e["default_language"] == lang


def test_unknown_language_refused(client, teacher):
    assert post(client, teacher, exam_body(q(language="ruby"))).status_code == 422
    assert post(client, teacher, exam_body(q(), default_language="go")).status_code == 422


def test_sql_fields_kept_for_explicit_sql(client, teacher):
    c = code_of(create(client, teacher, exam_body(
        q(language="sql", sql_setup="create table t(a int);", sql_compare="ordered"))))
    assert c["sql_setup"] == "create table t(a int);" and c["sql_compare"] == "ordered"


def test_sql_fields_kept_via_exam_default_and_defaults(client, teacher):
    e = create(client, teacher, exam_body(q(sql_setup="create table t(a int);"), default_language="sql"))
    c = code_of(e)
    assert c["sql_setup"] == "create table t(a int);" and c["sql_compare"] == "unordered"
    e = client.put(f"/api/exams/{e['id']}", json=exam_body(q(sql_setup="x")), headers=teacher).json()
    assert code_of(e)["sql_setup"] == "x"       # default_language not sent: the stored "sql" is kept


@pytest.mark.parametrize("lang", [l for l in LANGS if l != "sql"])
def test_sql_fields_dropped_for_other_languages(client, teacher, lang):
    c = code_of(create(client, teacher, exam_body(
        q(language=lang, sql_setup="create table t(a int);", sql_compare="ordered"))))
    assert c["sql_setup"] == "" and c["sql_compare"] == "unordered"


def test_sql_fields_dropped_when_default_is_not_sql(client, teacher):
    c = code_of(create(client, teacher, exam_body(q(sql_setup="x", sql_compare="ordered"))))
    assert c["sql_setup"] == "" and c["sql_compare"] == "unordered"
    other = create(client, teacher, exam_body(
        {"type": "short", "prompt": "p", "accepted_answers": ["a"], "sql_setup": "x"}))
    assert not {"sql_setup", "sql_compare"} & set(other["questions"][-1])


def test_sql_setup_length_limit(client, teacher):
    ok = code_of(create(client, teacher, exam_body(q(language="sql", sql_setup="a" * 50_000))))
    assert len(ok["sql_setup"]) == 50_000
    assert post(client, teacher, exam_body(q(language="sql", sql_setup="a" * 50_001))).status_code == 422


def test_sql_compare_values(client, teacher):
    for v in ("ordered", "unordered"):
        assert code_of(create(client, teacher, exam_body(q(language="sql", sql_compare=v))))["sql_compare"] == v
    assert post(client, teacher, exam_body(q(language="sql", sql_compare="random"))).status_code == 422


def test_student_payload_sql_vs_other(client, teacher, alice):
    body = exam_body(q(language="sql", sql_setup="create table t(a int);", sql_compare="ordered"),
                     q(language="python", sql_setup="ignored"))
    e, a = open_and_start(client, teacher, alice, body)
    codes = [x for x in a["questions"] if x["type"] == "code"]
    sql, py = codes
    assert sql["sql_setup"] == "create table t(a int);" and sql["sql_compare"] == "ordered"
    assert "sql_setup" not in py and "sql_compare" not in py
    for x in codes:
        assert x["tests"] == [{"input": "", "expected": "1"}]
        assert "points" not in x["tests"][0]
    assert HIDDEN_IN not in str(a) and HIDDEN_OUT not in str(a)


def test_student_question_unit():
    stored = {"id": "q_1", "type": "code", "prompt": "p", "points": 3, "language": None,
              "sql_setup": "s", "sql_compare": "ordered",
              "tests": [{"input": "a", "expected": "b", "visible": False, "points": 3},
                        {"input": "c", "expected": "d", "visible": True, "points": 0}]}
    sq = student_question(stored, "sql")
    assert sq["sql_setup"] == "s" and sq["sql_compare"] == "ordered"
    assert sq["tests"] == [{"input": "c", "expected": "d"}]
    sq = student_question(stored, "python")
    assert "sql_setup" not in sq and "sql_compare" not in sq


def test_teacher_round_trip_and_copy(client, teacher):
    e = create(client, teacher, exam_body(
        q(language="sql", sql_setup="create table t(a int);", sql_compare="ordered")))
    body = {**SAMPLE_EXAM, "title": "Copy", "questions": e["questions"]}
    copy = create(client, teacher, body)
    c = code_of(copy)
    assert c["sql_setup"] == "create table t(a int);" and c["sql_compare"] == "ordered"
    assert c["tests"] == code_of(e)["tests"]
    got = client.get(f"/api/exams/{copy['id']}", headers=teacher).json()
    assert code_of(got)["sql_setup"] == "create table t(a int);"
