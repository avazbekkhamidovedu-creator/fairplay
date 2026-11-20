import asyncio

import httpx
import pytest

from app import box_profiles, lms
from app.integrations import sanitize
from app.lms import LmsError, base as lmsbase, make_provider

from .test_dkuoj_bridge import KEY, run
from .test_dkuoj_bridge_v2 import CLASS_ID, V1, call, fake, oj
from .test_lms import canvas, connect


def test_pending_settings_from_a_non_editor_are_dropped(oj):
    r = call(oj, "POST", f"{V1}/exams/{KEY}/settings", {"open_book": True}, acting="mallory")
    assert r.status_code == 200
    run(oj)
    assert call(oj, "GET", f"{V1}/exams/{KEY}").json()["settings"]["open_book"] is False


def test_pending_settings_from_an_editor_are_kept(oj):
    assert call(oj, "POST", f"{V1}/exams/{KEY}/settings", {"open_book": True}, acting="teacher").status_code == 200
    run(oj)
    assert call(oj, "GET", f"{V1}/exams/{KEY}").json()["settings"]["open_book"] is True


def test_any_non_student_dkuoj_signs_for_a_class_is_class_staff(oj):
    """2026-11: DKU OJ vouches for the acting user of a class (instructor / TA / course staff); a student is refused."""
    body = {"class_name": "CS 101 Section A", "course_name": "CS 101"}
    assert call(oj, "POST", f"{V1}/classes/{CLASS_ID}/login", body, acting="mallory").status_code == 200
    run(oj)
    assert call(oj, "GET", f"{V1}/classes/{CLASS_ID}/status", acting="mallory").status_code == 200
    assert call(oj, "GET", f"{V1}/classes/{CLASS_ID}/status", acting="alice").status_code == 403
    assert call(oj, "GET", f"{V1}/classes/{CLASS_ID}/status", acting="teacher").status_code == 200


def _provider(handler):
    lms.set_transport_factory(lambda pid, url: httpx.MockTransport(handler))
    try:
        return make_provider("canvas", "https://canvas.test", "tok")
    finally:
        lms.set_transport_factory(None)


def test_same_host_needs_the_same_scheme():
    p = _provider(lambda req: httpx.Response(200, json=[]))
    assert p.same_host("https://canvas.test/api/v1/courses?page=2")
    assert not p.same_host("http://canvas.test/api/v1/courses?page=2")


def test_lms_response_size_is_capped(monkeypatch):
    monkeypatch.setattr(lmsbase, "MAX_BODY_BYTES", 100)
    p = _provider(lambda req: httpx.Response(200, content=b"x" * 500))
    with pytest.raises(LmsError):
        asyncio.run(p.request("GET", "/api/v1/courses"))


def test_lms_request_asks_for_identity_and_refuses_encoded_answers():
    seen = {}

    def handler(req):
        seen["ae"] = req.headers["accept-encoding"]
        return httpx.Response(200, content=b"[]", headers={"content-encoding": "gzip"})

    p = _provider(handler)
    with pytest.raises(LmsError):
        asyncio.run(p.request("GET", "/api/v1/courses"))
    assert seen["ae"] == "identity"


def test_a_stalled_lms_ends_in_a_502(client, teacher, canvas, monkeypatch):
    from app import routes_lms
    from app.lms.canvas import CanvasProvider

    async def hang(self):
        await asyncio.sleep(30)

    cid = connect(client, teacher).json()["id"]
    monkeypatch.setattr(routes_lms, "LMS_DEADLINE_S", 0.05)
    monkeypatch.setattr(CanvasProvider, "list_courses", hang)
    r = client.get(f"/api/lms/connections/{cid}/courses", headers=teacher)
    assert r.status_code == 502 and "too long" in r.json()["detail"]


def test_spa_path_with_a_nul_is_a_404(client):
    assert client.get("/teacher%00x").status_code == 404


def test_box_api_host_paths_are_blocked_like_the_app_host_ones():
    rules = box_profiles.PROFILES["duke"]["blocked_requests"]
    old = {(r["path"].replace("/api/2.0/", "/2.0/"), str(r.get("methods"))) for r in rules
           if r["path"].startswith("/api/2.0/")}
    twins = {(r["path"], str(r.get("methods"))) for r in rules if r["host"] == "api.box.com"}
    assert old and old == twins


def test_inline_code_does_not_span_a_blank_line():
    text = 'a ` b\n\n<img src="https://evil.test/t.png">\n\nc `'
    assert "<img" not in sanitize.clean_markdown(text)
    assert sanitize.clean_markdown("`a<b>c`") == "`a<b>c`"
