import json
import re

import pytest
from fastapi.testclient import TestClient

from app.config import parse_webtest_sites
from app.main import create_app
from app.security import HOMEWORK_SCOPE, make_token

from .conftest import login
from .test_homework_login import SECRET, begin, bearer, fake, oj_env, settings_for  # noqa: F401

LIST, OPEN = "/api/homework/webtest", "/api/homework/webtest/open"
SITES = [
    {"id": "wikipedia", "label": "Wikipedia", "url": "https://en.wikipedia.org/", "hosts": ["*.wikimedia.org"]},
    {"id": "box", "label": "Duke Box", "url": "https://duke.app.box.com/s/abcdefghij1234", "box": True},
]


def make(tmp_path, static_dir, sites=SITES, **kw):
    raw = sites if isinstance(sites, str) else json.dumps(sites)
    return TestClient(create_app(settings_for(tmp_path, static_dir, homework_webtest_sites=raw, **kw)))


@pytest.fixture
def wt(tmp_path, static_dir, fake):
    with make(tmp_path, static_dir) as c:
        yield c


def student(c):
    return login(c, "alice")


def hw_token(c, user_id):
    return {"Authorization": "Bearer " + make_token(SECRET, user_id, "student", 0, scope=HOMEWORK_SCOPE,
                                                    login_at=int(__import__("time").time()),
                                                    exp=int(__import__("time").time()) + 600)}


def test_off_by_default_empty_list_and_404(tmp_path, static_dir, fake):
    with make(tmp_path, static_dir, sites="") as c:
        h = student(c)
        assert c.get(LIST, headers=h).json() == {"sites": []}
        assert c.post(OPEN, json={"id": "wikipedia"}, headers=h).status_code == 404


def test_login_off_routes_404(client):
    h = login(client, "alice")
    assert client.get(LIST, headers=h).status_code == 404
    assert client.post(OPEN, json={"id": "x"}, headers=h).status_code == 404


def test_login_off_setting_ignored(tmp_path, static_dir):
    s = settings_for(tmp_path, static_dir, homework_login="", homework_webtest_sites="not json")
    s.check_secure()


def test_list_shape_no_urls(wt):
    r = wt.get(LIST, headers=student(wt))
    assert r.status_code == 200
    assert r.json() == {"sites": [{"id": "wikipedia", "label": "Wikipedia"}, {"id": "box", "label": "Duke Box"}]}
    assert "http" not in r.text and "wikimedia" not in r.text


def test_open_shape(wt):
    r = wt.post(OPEN, json={"id": "wikipedia"}, headers=student(wt))
    assert r.status_code == 200
    b = r.json()
    assert set(b) == {"state", "start_url", "allowed_hosts", "finish_url", "resume_paths", "expires_at"}
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", b["state"])
    assert b["start_url"] == "https://en.wikipedia.org/"
    assert b["allowed_hosts"] == ["en.wikipedia.org", "*.wikimedia.org"]
    assert b["finish_url"] == "https://fairplay-webtest.invalid/done"
    assert b["resume_paths"] == ["/__none__"]
    assert b["expires_at"].endswith("Z") or "+" in b["expires_at"]
    assert wt.post(OPEN, json={"id": "wikipedia"}, headers=student(wt)).json()["state"] != b["state"]


def test_open_box_includes_box_hosts(wt):
    b = wt.post(OPEN, json={"id": "box"}, headers=student(wt)).json()
    h = b["allowed_hosts"]
    assert h[0] == "duke.app.box.com"
    for want in ("duke.box.com", "duke.account.box.com", "sso.services.box.net", "shib.oit.duke.edu",
                 "*.duosecurity.com", "cdn01.boxcdn.net", "*.boxcloud.com"):
        assert want in h
    assert len(h) == len(set(h))
    assert all(re.fullmatch(r"(\*\.)?[a-z0-9.-]+", x) for x in h)


def test_open_unknown_id_404(wt):
    h = student(wt)
    assert wt.post(OPEN, json={"id": "nope"}, headers=h).status_code == 404
    assert wt.post(OPEN, json={}, headers=h).status_code == 422


def test_rate_limit(wt):
    h = student(wt)
    codes = [wt.post(OPEN, json={"id": "wikipedia"}, headers=h).status_code for _ in range(31)]
    assert codes[:30] == [200] * 30 and codes[30] == 429


def test_auth_rules(wt):
    assert wt.get(LIST).status_code == 401
    assert wt.post(OPEN, json={"id": "wikipedia"}).status_code == 401
    teacher = login(wt, "teacher")
    assert wt.get(LIST, headers=teacher).status_code == 403
    assert wt.post(OPEN, json={"id": "wikipedia"}, headers=teacher).status_code == 403


def test_homework_token_accepted_exam_token_refused(wt):
    from sqlalchemy import select
    from app.models import User
    from .test_dkuoj_bridge import db
    async def find(s):
        return (await s.execute(select(User.id).where(User.username == "alice"))).scalar_one()
    uid = db(wt, find)
    assert wt.get(LIST, headers=hw_token(wt, uid)).status_code == 200
    exam = {"Authorization": "Bearer " + make_token(SECRET, uid, "student", 600, scope="exam-1")}
    assert wt.get(LIST, headers=exam).status_code == 403
    assert wt.post(OPEN, json={"id": "wikipedia"}, headers=exam).status_code == 403


def test_routes_not_exam_routes(wt):
    from .test_homework_login import all_api_routes, _needs_student
    paths = {r.path: _needs_student(r) for r in all_api_routes(wt.app.routes)}
    assert paths["/api/homework/webtest"] is False and paths["/api/homework/webtest/open"] is False


def entry(**kw):
    return json.dumps([{"id": "a", "label": "A", "url": "https://a.example.org/", **kw}])


BAD = [
    ("{", "valid JSON"),
    ('{"id": "a"}', "JSON list"),
    ("[1]", "must be an object"),
    (entry(id="A"), "id must be"),
    (entry(id=""), "id must be"),
    (entry(id="a" * 33), "id must be"),
    (json.dumps([{"id": "a", "label": "A", "url": "https://x.org/"}] * 2), "used twice"),
    (entry(label=""), "label must be"),
    (entry(label="x" * 41), "label must be"),
    (entry(url="http://a.example.org/"), "https://"),
    (entry(url="http://localhost:8000/"), "https://"),
    (entry(url="ftp://a.example.org/"), "https://"),
    (entry(url="https://user:pw@a.example.org/"), "user name"),
    (entry(url="https://user@a.example.org/"), "user name"),
    (entry(url="a.example.org"), "https://"),
    (entry(hosts=["a.example.org:8080"]), "no port"),
    (entry(hosts=["a.org/x"]), "no port"),
    (entry(hosts="a.org"), "list of host"),
    (entry(box="yes"), "true or false"),
    (entry(extra=1), "unknown keys"),
    (json.dumps([{"id": f"s{i}", "label": "A", "url": "https://a.example.org/"} for i in range(13)]), "at most 12"),
]


@pytest.mark.parametrize("raw,msg", BAD)
def test_bad_value_refuses_to_start(tmp_path, static_dir, fake, raw, msg):
    s = settings_for(tmp_path, static_dir, homework_webtest_sites=raw)
    with pytest.raises(RuntimeError, match=msg):
        s.check_secure()


def test_valid_values():
    assert len(parse_webtest_sites(json.dumps([{"id": f"s{i}", "label": "A", "url": "https://a.example.org/"}
                                               for i in range(12)]))) == 12
    assert parse_webtest_sites(entry(url="http://localhost:8000/"), dev=True)[0]["host"] == "localhost"
    assert parse_webtest_sites("") == []


def test_env_read(monkeypatch):
    from app.config import Settings
    monkeypatch.setenv("HOMEWORK_WEBTEST_SITES", entry())
    assert Settings().homework_webtest_sites == entry()
