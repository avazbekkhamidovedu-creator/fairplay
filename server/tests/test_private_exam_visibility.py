"""An exam opened to selected students is private: everyone else gets what an unknown id gets."""
from datetime import timedelta

from app.timeutil import utcnow

from .conftest import login
from .test_open_to_autoclose import _manual, client, ids_by_name, new_exam, set_exam, start  # noqa: F401


def listed(client, h):
    return {x["id"]: x for x in client.get("/api/student/exams", headers=h).json()}


def open_to(client, teacher, names):
    e = new_exam(client, teacher)
    ids = ids_by_name(client, teacher, e["id"])
    return client.post(f"/api/exams/{e['id']}/open", json={"open_to": [ids[n] for n in names]},
                       headers=teacher).json()


def test_unselected_student_does_not_see_it(client, teacher, alice, bob):
    e = open_to(client, teacher, ["alice", "bob"])
    carol = login(client, "carol")
    assert e["id"] not in listed(client, carol)
    assert e["title"] not in client.get("/api/student/exams", headers=carol).text
    for h in (alice, bob):
        assert e["id"] in listed(client, h)


def test_unselected_student_gets_the_unknown_exam_answer(client, teacher, alice, bob):
    e = open_to(client, teacher, ["alice", "bob"])
    carol = login(client, "carol")
    unknown = start(client, carol, "e_nope", e["start_code"])
    r = start(client, carol, e["id"], e["start_code"])
    assert (r.status_code, r.json()) == (unknown.status_code, unknown.json()) == (404, {"detail": "Exam not found"})
    for path in ("challenge", "package", "package-key"):
        a = client.get(f"/api/student/exams/{e['id']}/{path}", headers=carol)
        b = client.get(f"/api/student/exams/e_nope/{path}", headers=carol)
        assert (a.status_code, a.json()) == (b.status_code, b.json())


def test_selected_students_can_start(client, teacher, alice, bob):
    e = open_to(client, teacher, ["alice", "bob"])
    assert start(client, alice, e["id"], e["start_code"]).status_code == 200
    assert start(client, bob, e["id"], e["start_code"]).status_code == 200


def test_not_yet_open_still_shows_for_selected(client, teacher, alice, bob):
    e = open_to(client, teacher, ["alice", "bob"])
    set_exam(client, e["id"], scheduled_at=utcnow() + timedelta(hours=2))
    assert listed(client, alice)[e["id"]]["open_for_me"] is True
    assert e["id"] not in listed(client, login(client, "carol"))


def test_no_selection_shows_for_the_whole_class(client, teacher, alice, bob):
    e = new_exam(client, teacher)
    e = client.post(f"/api/exams/{e['id']}/open", headers=teacher).json()
    for h in (alice, bob, login(client, "carol")):
        assert e["id"] in listed(client, h)
