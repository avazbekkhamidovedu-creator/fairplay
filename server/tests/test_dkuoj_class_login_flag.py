import pytest

from .test_dkuoj_bridge_v2 import CLASS_ID, V1, bearer, call, make_login, oj, fake  # noqa: F401  (fixtures)


@pytest.fixture
def off(oj, monkeypatch):
    monkeypatch.delenv("FAIRPLAY_CLASS_LOGIN", raising=False)
    return oj


def test_class_login_routes_are_404_when_off(oj, monkeypatch):
    cl = make_login(oj)
    monkeypatch.delenv("FAIRPLAY_CLASS_LOGIN")
    assert call(oj, "POST", f"{V1}/classes/{CLASS_ID}/login", {"class_name": "x"}).status_code == 404
    assert call(oj, "DELETE", f"{V1}/classes/{CLASS_ID}/login").status_code == 404
    assert call(oj, "GET", f"{V1}/classes/{CLASS_ID}/status").status_code == 404
    assert call(oj, "POST", f"{V1}/students/bl642/reset", {"class_id": CLASS_ID}).status_code == 404
    r = oj.post("/api/setup/identify", json={"first_name": "Bob", "last_name": "Li", "netid": "bl642"},
                headers=bearer("x"))
    assert r.status_code == 404
    r = oj.post("/api/setup/account", json={"username": "bobli", "password": "a", "password2": "a"},
                headers=bearer("x"))
    assert r.status_code == 404
    r = oj.post("/api/auth/login", json={"username": cl["username"], "password": cl["password"]})
    assert r.status_code == 401
    assert "setup_token" not in r.text


def test_flag_off_leaves_the_other_bridge_routes(off):
    assert call(off, "POST", f"{V1}/sync", {}).status_code == 200
