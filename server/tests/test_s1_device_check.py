"""S1 (2026-10-20): POST /api/student/device-check and the teacher's readiness list."""
from .conftest import SAMPLE_EXAM, login

CHECKS = [{"id": "macos_version", "ok": True, "message": "macOS 15.1"},
          {"id": "displays", "ok": False, "message": "Unplug the second display"},
          {"id": "vpn", "ok": False, "message": "A VPN is on", "info": True}]


def report(**kw):
    return {"platform": "mac", "app_version": "0.5", "os_version": "15.1", "checks": CHECKS, **kw}


def test_device_check_saved_and_listed(client, teacher, alice, bob):
    e = client.post("/api/exams", json=SAMPLE_EXAM, headers=teacher).json()
    r = client.post("/api/student/device-check", headers=alice, json=report(exam_id=e["id"]))
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["saved_at"].endswith("Z")
    rows = client.get(f"/api/exams/{e['id']}/readiness", headers=teacher).json()
    assert len(rows) == 3                                   # the whole class (alice, bob, carol)
    checked = [r for r in rows if r["platform"]]
    assert len(checked) == 1
    a = checked[0]
    assert a["platform"] == "mac" and a["app_version"] == "0.5" and a["os_version"] == "15.1"
    assert a["ok"] is False and a["failed"] == ["displays"]        # vpn is info: never blocks
    assert a["checked_at"].endswith("Z") and isinstance(a["student_id"], int)
    never = [r for r in rows if r is not a]
    assert all(r["platform"] is None and r["ok"] is None and r["failed"] is None
               and r["checked_at"] is None for r in never)
    # a new report replaces the old one (one row per student + platform)
    fixed = [dict(c, ok=True) if c["id"] == "displays" else c for c in CHECKS]
    client.post("/api/student/device-check", headers=alice, json=report(checks=fixed))
    rows = client.get(f"/api/exams/{e['id']}/readiness", headers=teacher).json()
    a2 = next(r for r in rows if r["student_id"] == a["student_id"])
    assert a2["ok"] is True and a2["failed"] == []


def test_readiness_without_class_lists_attempts(client, teacher, alice):
    body = {k: v for k, v in SAMPLE_EXAM.items() if k != "class_id"}
    e = client.post("/api/exams", json={**body, "class_id": None}, headers=teacher).json()
    assert e["class_id"] is None
    assert client.get(f"/api/exams/{e['id']}/readiness", headers=teacher).json() == []


def test_readiness_owner_only(client, teacher, alice):
    e = client.post("/api/exams", json=SAMPLE_EXAM, headers=teacher).json()
    assert client.get(f"/api/exams/{e['id']}/readiness", headers=alice).status_code == 403
    assert client.get("/api/exams/e_nope/readiness", headers=teacher).status_code == 404


def test_device_check_limits(client, alice):
    url = "/api/student/device-check"
    assert client.post(url, headers=alice, json=report(checks=[CHECKS[0]] * 40)).status_code == 200
    assert client.post(url, headers=alice, json=report(checks=[CHECKS[0]] * 41)).status_code == 422
    long = "x" * 301
    assert client.post(url, headers=alice, json=report(os_version=long)).status_code == 422
    assert client.post(url, headers=alice, json=report(
        checks=[{"id": "disk", "ok": True, "message": long}])).status_code == 422
    assert client.post(url, headers=alice, json=report(
        checks=[{"id": long, "ok": True}])).status_code == 422
    assert client.post(url, headers=alice, json=report(platform="  ")).status_code == 422
    assert client.post(url, headers=alice, json=report(exam_id=long)).status_code == 422
    assert client.post(url, headers=alice, json=report(os_version="y" * 300)).status_code == 200
    assert client.post(url, json=report()).status_code == 401
    t = login(client, "teacher")
    assert client.post(url, headers=t, json=report()).status_code == 403
