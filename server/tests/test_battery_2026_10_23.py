"""2026-10-23: the student page sends the laptop battery with its heartbeat; the live room
shows it so the invigilator can bring a charger before the laptop dies."""
from .test_student import start


def _live_row(client, teacher, exam_id, aid):
    rows = client.get(f"/api/exams/{exam_id}/live", headers=teacher).json()["students"]
    return next(r for r in rows if r["attempt_id"] == aid)


def test_battery_reaches_the_live_room(client, teacher, alice, live_exam):
    aid = start(client, alice, live_exam).json()["attempt_id"]
    url = f"/api/student/attempts/{aid}/heartbeat"
    assert client.post(url, json={"current_question": 2, "battery": 0.144, "charging": False},
                       headers=alice).status_code == 200
    row = _live_row(client, teacher, live_exam["id"], aid)
    assert row["battery"] == 0.14 and row["charging"] is False
    assert row["current_question"] == 2


def test_battery_is_optional_and_checked(client, teacher, alice, live_exam):
    aid = start(client, alice, live_exam).json()["attempt_id"]
    url = f"/api/student/attempts/{aid}/heartbeat"
    assert client.post(url, json={"current_question": 0}, headers=alice).status_code == 200
    row = _live_row(client, teacher, live_exam["id"], aid)
    assert row["battery"] is None and row["charging"] is None
    assert client.post(url, json={"battery": 1.5}, headers=alice).status_code == 422
    assert client.post(url, json={"battery": -0.1}, headers=alice).status_code == 422
