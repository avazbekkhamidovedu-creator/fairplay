"""S2 (2026-10-20): X-Server-Time on every /api response, app version gate (426),
GET /api/app/version."""
from app.app_version import older_than, version_tuple

from .conftest import login
from .test_app_session import DEV_A, app_client, beat, exam, start  # noqa: F401  (fixtures)

MAC = {"X-FairPlay-Platform": "mac"}


def test_version_compare():
    assert version_tuple("0.10") > version_tuple("0.9")
    assert version_tuple("1.0") == version_tuple("1.0.0") == version_tuple("1")
    assert version_tuple("") == version_tuple("0") == ()
    assert older_than("0.4", "0.5") and not older_than("0.5", "0.5")
    assert not older_than("0.10", "0.9") and older_than("0.9", "0.10")
    assert not older_than("0.1", None) and not older_than("0.1", "")
    assert older_than(None, "0.1")          # platform header without a version: too old


def test_server_time_header_on_api_only(app_client):
    r = app_client.get("/api/health")
    t = r.headers["x-server-time"]
    assert t.endswith("Z") and len(t) == len("2026-10-20T10:00:00.000Z")
    assert "x-server-time" not in app_client.get("/teacher").headers
    # also on errors
    assert "x-server-time" in app_client.get("/api/nope").headers


def test_app_version_endpoint(app_client):
    s = app_client.app.state.settings
    r = app_client.get("/api/app/version").json()
    assert r["mac"] is None and r["windows"] is None and r["server_time"].endswith("Z")
    s.min_app_version_mac = "0.6"
    assert app_client.get("/api/app/version").json()["mac"] == "0.6"


def _hdr(h, version, platform="mac"):
    return {**h, "X-FairPlay-Platform": platform, "X-FairPlay-Version": version}


def test_start_gated_only_with_platform_header(app_client, exam):
    s = app_client.app.state.settings
    s.min_app_version_mac = "0.10"
    h = login(app_client, "alice")
    r = start(app_client, _hdr(h, "0.9"), exam)
    assert r.status_code == 426
    assert r.json() == {"detail": "Update FairPlay to continue", "min_app_version": "0.10"}
    # windows has no minimum; no platform header = browser / no gate
    ok = start(app_client, _hdr(h, "0.1", "windows"), exam)
    assert ok.status_code == 200 and ok.json()["min_app_version"] is None
    assert ok.json()["server_time"].endswith("Z")


def test_start_ok_with_new_enough_version(app_client, exam):
    app_client.app.state.settings.min_app_version_mac = "0.9"
    h = login(app_client, "alice")
    r = start(app_client, _hdr(h, "0.10"), exam)
    assert r.status_code == 200 and r.json()["min_app_version"] == "0.9"


def test_app_beat_gated(app_client, exam):
    h = login(app_client, "alice")
    a = start(app_client, h, exam).json()
    hs = {**h, "X-FairPlay-Session": a["session"]}
    r = beat(app_client, _hdr(hs, "0.5"), a["attempt_id"], a["session"], 1)
    assert r.status_code == 200 and r.json()["min_app_version"] is None
    assert r.json()["server_time"].endswith("Z")
    app_client.app.state.settings.min_app_version_mac = "1.0"
    r = beat(app_client, _hdr(hs, "0.99"), a["attempt_id"], a["session"], 2)
    assert r.status_code == 426 and r.json()["min_app_version"] == "1.0"
    assert beat(app_client, _hdr(hs, "1.0.0"), a["attempt_id"], a["session"], 3).status_code == 200
    # health is never gated
    assert app_client.get("/api/health", headers=_hdr({}, "0.1")).status_code == 200


def test_ipad_platform_gate(app_client, exam):
    """iPad app (2026-10-20): its own minimum, reported by /api/app/version."""
    s = app_client.app.state.settings
    assert app_client.get("/api/app/version").json()["ipad"] is None
    s.min_app_version_ipad = "0.6"
    assert app_client.get("/api/app/version").json()["ipad"] == "0.6"
    h = login(app_client, "alice")
    assert start(app_client, _hdr(h, "0.5", "ipad"), exam).status_code == 426
    r = start(app_client, _hdr(h, "0.6", "ipad"), exam)
    assert r.status_code == 200 and r.json()["min_app_version"] == "0.6"


def test_old_app_cannot_sign_in_but_browsers_and_new_apps_can(app_client):
    app_client.app.state.settings.min_app_version_mac = "0.8.8"
    body = {"username": "alice", "password": "alice123"}
    old = app_client.post("/api/auth/login", json=body, headers={"X-FairPlay-Platform": "mac", "X-FairPlay-Version": "0.8.7"})
    assert old.status_code == 426 and old.json()["min_app_version"] == "0.8.8"
    new = app_client.post("/api/auth/login", json=body, headers={"X-FairPlay-Platform": "mac", "X-FairPlay-Version": "0.8.8"})
    assert new.status_code == 200
    assert app_client.post("/api/auth/login", json=body).status_code == 200      # browser: never gated
