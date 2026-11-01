from app.integrations.dkuoj.adapter import question_from_oj, question_to_oj
from app.question_types import check_answer, extended_fields

LANGS = {"PY3": "Python 3", "CPP17": "C++17", "JAVA": "Java", "C11": "C11"}


def code_q(*keys):
    return {"id": "p1", "order": 1, "type": "code", "title": "t", "points": 5, "statement_md": "s",
            "code": {"languages": [{"key": k, "name": LANGS[k]} for k in keys],
                     "templates": {k: f"tpl-{k}" for k in keys}, "samples": []}}


def test_one_language_is_the_default():
    q = question_from_oj(code_q("CPP17"))
    assert [x["key"] for x in q["languages"]] == ["CPP17"]
    assert q["default_language"] == "CPP17"
    assert q["starter_code"] == "tpl-CPP17"


def test_python_is_the_default_and_listed_first():
    q = question_from_oj(code_q("CPP17", "JAVA", "PY3"))
    assert q["default_language"] == "PY3"
    assert [x["key"] for x in q["languages"]] == ["PY3", "CPP17", "JAVA"]
    assert q["language"] == "python"
    assert q["starter_code"] == "tpl-PY3"


def test_without_python_the_first_allowed_is_the_default():
    q = question_from_oj(code_q("JAVA", "C11", "CPP17"))
    assert q["default_language"] == "JAVA"
    assert [x["key"] for x in q["languages"]] == ["JAVA", "C11", "CPP17"]
    assert q["starter_code"] == "tpl-JAVA"


def test_student_view_carries_the_default():
    q = question_from_oj(code_q("CPP17", "PY3"))
    assert extended_fields(q, teacher=False)["default_language"] == "PY3"
    assert question_to_oj(q)["code"]["languages"][0]["key"] == "PY3"


def test_existing_answer_language_is_kept():
    q = question_from_oj(code_q("CPP17", "PY3"))
    ans = check_answer(q, {"language": "CPP17", "source": "int main(){}"})
    assert ans == {"language": "CPP17", "source": "int main(){}"}
    assert check_answer(q, "x") == {"language": "PY3", "source": "x"}
