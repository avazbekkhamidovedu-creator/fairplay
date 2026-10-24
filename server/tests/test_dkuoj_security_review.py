"""Security review 2026-10-24, FairPlay side of the DKU OJ bridge: each finding's attack,
now refused. Against a FAKE DKU OJ (httpx.MockTransport), like the other bridge tests."""
import asyncio
import logging
import time
import zlib
from datetime import timedelta

import httpx
import pytest

from app.integrations import sanitize, site_policy
from app.integrations.dkuoj import client as ojclient, signing
from app.integrations.dkuoj.config import DkuojConfig
from app.timeutil import iso, utcnow

from .conftest import login
from .test_dkuoj_bridge import KEY, OJ, TOKEN, db, fp_signed, oj_exam, run, start
from .test_dkuoj_bridge_v2 import (CLASS_ID, V1, bearer, call, fake, make_login, oj,  # noqa: F401
                                   set_up)

MiB = 1024 * 1024


# ---------------------------------------------------------------- 1. bounded answers
class Trickle(httpx.AsyncByteStream):
    """A body sent in pieces, optionally slowly; counts how much was actually pulled."""

    def __init__(self, chunks, delay=0.0):
        self.chunks, self.delay, self.sent = list(chunks), delay, 0

    async def __aiter__(self):
        for c in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.sent += len(c)
            yield c

    async def aclose(self):
        pass


def gzip_bomb(mib: int):
    """mib MiB of zeros, gzip-compressed in 64 KiB-ish pieces (about 1 KiB per MiB)."""
    z = zlib.compressobj(9, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
    out = [z.compress(b"\0" * MiB) for _ in range(mib)] + [z.flush()]
    data = b"".join(out)
    return [data[i:i + 16 * 1024] for i in range(0, len(data), 16 * 1024)]


def with_transport(handler):
    ojclient.set_transport_factory(lambda base: httpx.MockTransport(handler))

    async def no_sleep(_s):
        return None
    ojclient.set_sleep(no_sleep)


@pytest.fixture
def evil():
    yield
    ojclient.set_transport_factory(None)
    ojclient.set_sleep(None)


def _client_call(fn):
    async def go():
        async with ojclient.DkuojClient(OJ, TOKEN) as c:
            return await fn(c)
    return asyncio.run(go())


@pytest.mark.parametrize("what", ["package", "roster", "list", "student_classes", "results"])
def test_gzip_bomb_is_cut_off_while_reading(evil, what):
    """A 1 KiB-per-MiB gzip body used to be inflated whole (1 MiB -> 1 GiB in memory)
    before any size check; now it is read as a stream and dropped past the cap."""
    streams = []

    def handler(req):
        assert req.headers["accept-encoding"] == "identity"
        s = Trickle(gzip_bomb(256))
        streams.append(s)
        return httpx.Response(200, headers={"Content-Encoding": "gzip", "Content-Type": "application/json"},
                              stream=s)
    with_transport(handler)
    calls = {"package": lambda c: c.package(KEY), "roster": lambda c: c.roster(CLASS_ID),
             "list": lambda c: c.list_exams(None), "student_classes": lambda c: c.student_classes("bl642"),
             "results": lambda c: c.results(KEY, None)}
    with pytest.raises(ojclient.OjError) as ei:
        _client_call(calls[what])
    assert "larger than 5 MB" in ei.value.message if what == "package" else "too large" in ei.value.message
    assert len(streams) == 1                               # not retried
    total = sum(len(c) for c in streams[0].chunks)
    assert streams[0].sent < total / 2                     # stopped early, not after the whole body


def test_plain_oversized_body_and_asset_caps(evil):
    def handler(req):
        if "/assets/" in str(req.url):
            return httpx.Response(200, stream=Trickle([b"x" * MiB] * 11), headers={"Content-Type": "image/png"})
        return httpx.Response(200, stream=Trickle([b" " * MiB] * 3))
    with_transport(handler)
    with pytest.raises(ojclient.OjError, match="larger than 10 MB"):
        _client_call(lambda c: c.asset(KEY, "big.png"))
    with pytest.raises(ojclient.OjError, match="too large"):
        _client_call(lambda c: c.roster(CLASS_ID))


def test_normal_gzip_answer_still_works(evil):
    import gzip
    import json
    body = gzip.compress(json.dumps({"classes": [{"id": 31, "name": "A"}]}).encode())

    def handler(req):
        return httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=Trickle([body[:10], body[10:]]))
    with_transport(handler)
    assert _client_call(lambda c: c.student_classes("bl642")) == {"classes": [{"id": 31, "name": "A"}]}


def test_unknown_encoding_and_oversized_refusal(evil):
    def handler(req):
        if "roster" in str(req.url):
            return httpx.Response(200, headers={"Content-Encoding": "br"}, stream=Trickle([b"zzz"]))
        return httpx.Response(400, stream=Trickle([b"e" * MiB]))       # a 1 MiB error page
    with_transport(handler)
    with pytest.raises(ojclient.OjError, match="encoding"):
        _client_call(lambda c: c.roster(CLASS_ID))
    with pytest.raises(ojclient.OjError) as ei:
        _client_call(lambda c: c.list_exams(None))
    assert ei.value.status == 400 and ei.value.body == ""


def test_slow_trickle_hits_the_total_deadline(evil):
    """Each byte arrived just within the read timeout, so a 3 s call took 25 s; now each
    try ends after 2 x its timeout whatever the server does."""
    def handler(req):
        return httpx.Response(200, stream=Trickle([b"{"] + [b" "] * 100 + [b"}"], delay=0.1))
    with_transport(handler)
    t = time.monotonic()
    with pytest.raises(ojclient.OjError, match="did not answer in time"):
        _client_call(lambda c: c.student_classes("bl642", timeout=0.2))
    assert time.monotonic() - t < 2.0
    assert ojclient.MAX_TOTAL_S <= 30


# ---------------------------------------------------------------- 2. reset of a native account
def test_reset_refused_for_an_account_with_fairplay_classes(oj, fake):
    run(oj)
    bob = set_up(oj, make_login(oj))
    teacher = login(oj, "teacher")
    c = oj.post("/api/classes", json={"name": "Native Physics", "term": "F26"}, headers=teacher).json()
    assert oj.post(f"/api/classes/{c['id']}/members", json={"usernames": ["bobli"]},
                   headers=teacher).json()["added"] == 1
    r = call(oj, "POST", f"{V1}/students/bl642/reset", {"class_id": CLASS_ID}, acting="anyone")
    assert r.status_code == 403
    assert r.json()["detail"] == "This account is also used for FairPlay classes; reset it in FairPlay."
    assert oj.get("/api/me", headers=bearer(bob["token"])).status_code == 200      # still Bob's
    status = call(oj, "GET", f"{V1}/classes/{CLASS_ID}/status").json()
    assert any(ev["kind"] == "student_reset_refused" and ev["netid"] == "bl642" for ev in status["recent"])
    # out of the native class again: DKU OJ may reset it
    oj.delete(f"/api/classes/{c['id']}", headers=teacher)

    async def leave(s):
        from sqlalchemy import delete
        from app.models import ClassMember
        await s.execute(delete(ClassMember).where(ClassMember.class_id == c["id"]))
        await s.commit()
    db(oj, leave)
    assert call(oj, "POST", f"{V1}/students/bl642/reset", {"class_id": CLASS_ID}).json() == {"ok": True}
    assert oj.get("/api/me", headers=bearer(bob["token"])).status_code == 401


# ---------------------------------------------------------------- 3. websites from DKU OJ
def test_dkuoj_cannot_open_ai_or_chat_sites(oj, fake):
    orig = fake.listing

    def evil_listing():
        d = orig()
        d["settings"]["websites"] = ["docs.python.org", "chatgpt.com", "*.openai.com", "claude.ai",
                                     "web.whatsapp.com", "*.google.com", "en.cppreference.com"]
        return d
    fake.listing = evil_listing
    run(oj)
    s = call(oj, "GET", f"{V1}/exams/{KEY}").json()["settings"]
    assert s["websites"] == ["docs.python.org", "en.cppreference.com"]
    assert sorted(b["site"] for b in s["websites_blocked"]) == sorted(
        ["chatgpt.com", "*.openai.com", "claude.ai", "web.whatsapp.com", "*.google.com"])
    reasons = {b["site"]: b["reason"] for b in s["websites_blocked"]}
    assert reasons["chatgpt.com"] == "AI assistant" and reasons["web.whatsapp.com"] == "messaging"
    assert "*.google.com" in reasons          # refused as a website name already (shared suffix)
    # what the student's app gets
    teacher, alice = login(oj, "teacher"), login(oj, "alice")
    v = call(oj, "POST", f"{V1}/exams/{KEY}/open", {"open_to": "all"}).json()
    a = start(oj, alice, oj_exam(oj, teacher)["id"], v["start_code"]).json()
    assert a["tools"]["websites"] == ["docs.python.org", "en.cppreference.com"]


def test_external_site_allowlist(oj, fake, monkeypatch):
    monkeypatch.setenv(site_policy.ENV_ALLOWLIST, "docs.python.org, *.cppreference.com")
    orig = fake.listing

    def listing():
        d = orig()
        d["settings"]["websites"] = ["docs.python.org", "en.cppreference.com", "stackoverflow.com"]
        return d
    fake.listing = listing
    run(oj)
    s = call(oj, "GET", f"{V1}/exams/{KEY}").json()["settings"]
    assert s["websites"] == ["docs.python.org", "en.cppreference.com"]
    assert s["websites_blocked"] == [{"site": "stackoverflow.com", "reason": site_policy.NOT_ALLOWED}]


def test_site_policy_matching(monkeypatch):
    monkeypatch.delenv(site_policy.ENV_ALLOWLIST, raising=False)
    d = site_policy.denied
    assert d("chat.openai.com") and d("*.openai.com") and d("deepseek.com") and d("chat.deepseek.com")
    assert d("*.google.com") and d("gemini.google.com") and d("*.github.com") and d("github.com")
    assert d("drive.google.com") and d("web.telegram.org") and d("wx.qq.com")
    assert not d("google.com") and not d("docs.python.org") and not d("docs.github.com")
    assert not d("notopenai.com") and not d("*.python.org")
    monkeypatch.setenv(site_policy.ENV_ALLOWLIST, "docs.python.org,*.cppreference.com")
    kept, blocked = site_policy.filter_sites(["docs.python.org", "python.org", "*.cppreference.com",
                                              "en.cppreference.com", "*.python.org"])
    assert kept == ["docs.python.org", "*.cppreference.com", "en.cppreference.com"]
    assert [b["site"] for b in blocked] == ["python.org", "*.python.org"]


# ---------------------------------------------------------------- 4. answer files after hand-in
@pytest.mark.parametrize("review,want", [("nothing", 404), ("score", 404), ("answers", 200)])
def test_answer_file_after_handin_follows_review_level(oj, fake, review, want):
    fake.review = review
    run(oj)
    teacher, alice = login(oj, "teacher"), login(oj, "alice")
    v = call(oj, "POST", f"{V1}/exams/{KEY}/open", {"open_to": "all"}).json()
    a = start(oj, alice, oj_exam(oj, teacher)["id"], v["start_code"]).json()
    aid = a["attempt_id"]
    q6 = next(q for q in a["questions"] if q["type"] == "file_upload")
    fid = oj.post(f"/api/student/attempts/{aid}/files?question_id={q6['id']}",
                  files={"file": ("report.pdf", b"%PDF-1.4 my answer", "application/pdf")}, headers=alice).json()["id"]
    assert oj.get(f"/api/student/attempts/{aid}/files/{fid}", headers=alice).status_code == 200   # writing
    assert oj.post(f"/api/student/attempts/{aid}/submit", json={"answers": {q6["id"]: [fid]}, "client_seq": 1},
                   headers=alice).status_code == 200
    assert oj.get(f"/api/student/attempts/{aid}/files/{fid}", headers=alice).status_code == want


# ---------------------------------------------------------------- 5. text from DKU OJ cleaned
def test_feedback_and_statements_cleaned_on_arrival(oj, fake):
    fake.review = "answers_and_results"
    q1 = fake.package["questions"][0]
    q1["statement_md"] = ("Which? ![cell](fp-asset:cell.png) ![t](https://tracker.example/p.png)"
                          "<script>alert(1)</script> [x](javascript:alert(3))")
    q5 = fake.package["questions"][4]
    q5["statement_html"] = '<p onclick="x()">Explain <b>hashing</b><img src="https://e.x/i.png"></p>'
    q1["choice"]["options"][0]["text_md"] = "Array<img src=x onerror=alert(1)>"
    run(oj)
    teacher, alice = login(oj, "teacher"), login(oj, "alice")
    v = call(oj, "POST", f"{V1}/exams/{KEY}/open", {"open_to": "all"}).json()
    e = oj_exam(oj, teacher)
    a = start(oj, alice, e["id"], v["start_code"]).json()
    qs = {q["title"]: q for q in a["questions"]}
    assert qs["Hash maps"]["prompt"] == ("Which? ![cell](fp-asset:cell.png) [external image removed] x")
    assert qs["Explain"]["prompt"] == "<p>Explain <b>hashing</b>[external image removed]</p>"
    assert "Array[external image removed]" in qs["Hash maps"]["options"]
    oj.post(f"/api/student/attempts/{a['attempt_id']}/submit", json={"answers": {}, "client_seq": 1}, headers=alice)
    fake.results = [{"username": "alice", "released": True, "total": 1, "total_points": 100,
                     "updated_at": iso(utcnow()),
                     "questions": [{"question_id": "p000123", "points": 1,
                                    "feedback_md": "<img src=x onerror=alert(1)><a href='javascript:alert(2)'>x</a> ok"}]}]
    run(oj, now=fake.end + timedelta(minutes=1))
    d = oj.get(f"/api/student/grades/{e['id']}", headers=alice).json()
    assert [q["comment"] for q in d["questions"] if q["comment"]] == ["[external image removed]<a>x</a> ok"]


def test_sanitizer_rules():
    h, m = sanitize.clean_html, sanitize.clean_markdown
    assert h('<a href="jav&#x09;ascript:alert(1)">x</a>') == "<a>x</a>"
    assert h("<svg><script>1</script></svg>a < b") == "a &lt; b"
    assert h('<img src="data:image/png;base64,AAAA">') == '<img src="data:image/png;base64,AAAA">'
    assert h('<img src="data:image/svg+xml;base64,AAAA">') == sanitize.EXTERNAL_IMAGE
    assert h('<div class="note" style="x">d</div><iframe src=x></iframe>') == '<div class="note">d</div>'
    code = "```cpp\nvector<int> v; if (a<b) {}\n<script>\n```"
    assert m("Use `a<b>c`.\n" + code) == "Use `a<b>c`.\n" + code
    assert m("![r][ref]\n\n[ref]: https://evil/i.png") == "[external image removed]\n\n[ref]: https://evil/i.png"
    assert m("[w](https://en.wikipedia.org/wiki/A_(b)) <javascript:alert(1)>") == \
        "[w](https://en.wikipedia.org/wiki/A_(b)) javascript:alert(1)"
    assert m("<b onmouseover=1>bold</b> plain a<b text") == "<b>bold</b> plain a<b text"


# ---------------------------------------------------------------- 6. join box oracle
def test_class_login_name_in_join_box_counts_as_a_wrong_code(oj, fake):
    cl = make_login(oj)
    alice = login(oj, "alice")
    codes = [oj.post("/api/student/classes/join", json={"code": cl["username"]}, headers=alice).status_code
             for _ in range(12)]
    assert codes[:10] == [400] * 10 and codes[10:] == [429, 429]


# ---------------------------------------------------------------- 7. no NetIDs in the logs
def test_http_client_logs_only_warnings():
    import app.main  # noqa: F401
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


# ---------------------------------------------------------------- 8. deployment
@pytest.mark.parametrize("url,dev,insecure,ok", [
    ("https://oj.dku.edu", False, "", True),
    ("http://127.0.0.1:8001", False, "", True),
    ("http://localhost:8001", False, "", True),
    ("http://[::1]:8001", False, "", True),
    ("http://oj.dku.edu", False, "", False),
    ("http://10.0.0.5", False, "", False),
    ("http://oj.dku.edu", True, "", True),
    ("http://oj.dku.edu", False, "1", True),
    ("ftp://oj.dku.edu", True, "1", False),
])
def test_plain_http_dkuoj_url_refused(monkeypatch, url, dev, insecure, ok):
    monkeypatch.setenv("DKUOJ_URL", url)
    monkeypatch.setenv("DKUOJ_TOKEN", TOKEN)
    monkeypatch.setenv("DKUOJ_ALLOW_INSECURE", insecure)
    cfg = DkuojConfig.from_env()
    if ok:
        cfg.check(dev=dev)
    else:
        with pytest.raises(RuntimeError, match="DKUOJ_URL"):
            cfg.check(dev=dev)


def test_create_app_fails_on_plain_http(monkeypatch, tmp_path, static_dir):
    from app.main import create_app
    from .test_dkuoj_bridge import oj_settings
    monkeypatch.setenv("DKUOJ_URL", "http://oj.dku.edu")
    monkeypatch.setenv("DKUOJ_TOKEN", TOKEN)
    monkeypatch.delenv("DKUOJ_ALLOW_INSECURE", raising=False)
    try:
        with pytest.raises(RuntimeError, match="plain http"):
            create_app(oj_settings(tmp_path, static_dir))
    finally:
        monkeypatch.delenv("DKUOJ_URL")
        from app.integrations.dkuoj.source import SOURCE
        SOURCE.config = DkuojConfig()


NEW = "fairplay-new-shared-token-abcdef0123456789"


def test_token_rotation(monkeypatch):
    monkeypatch.setenv("DKUOJ_TOKEN", f" {NEW} , {TOKEN} ")
    cfg = DkuojConfig.from_env()
    assert cfg.token == NEW and cfg.tokens == [NEW, TOKEN] and cfg.verify_tokens == [NEW, TOKEN]
    monkeypatch.setenv("DKUOJ_TOKEN", "a" * 40 + "," + "b" * 40 + "," + "c" * 40)
    with pytest.raises(RuntimeError, match="at most 2"):
        DkuojConfig.from_env().check()

    async def check(tok, accept):
        path = "/api/integrations/dkuoj/v1/exams/x"
        h = httpx.Headers(signing.sign_headers(tok, signing.OJ_TO_FP, "GET", path, b"", "prof"))
        return await signing.verify(accept, signing.OJ_TO_FP, "GET", path, h, b"")
    assert asyncio.run(check(TOKEN, [NEW, TOKEN])) == "prof"        # old token still accepted
    assert asyncio.run(check(NEW, [NEW, TOKEN])) == "prof"
    with pytest.raises(signing.BadSignature):
        asyncio.run(check("x" * 40, [NEW, TOKEN]))
    with pytest.raises(signing.BadSignature):
        asyncio.run(check(TOKEN, [NEW]))                            # rotation finished


def test_rotation_end_to_end(monkeypatch, tmp_path, static_dir, fake):
    """FairPlay signs with the first token (DKU OJ already switched); DKU OJ calls signed with
    the old one are still accepted until it is removed."""
    from fastapi.testclient import TestClient
    from app.main import create_app
    from .test_dkuoj_bridge import oj_settings
    import tests.test_dkuoj_bridge as T
    monkeypatch.setenv("DKUOJ_TOKEN", f"{NEW},{TOKEN}")
    seen = []

    def oj_checks(req, token=TOKEN):        # DKU OJ already has the NEW token
        seen.append(fp_signed(req, NEW))
        return seen[-1]
    monkeypatch.setattr(T, "fp_signed", oj_checks)
    with TestClient(create_app(oj_settings(tmp_path, static_dir))) as c:
        run(c)
        assert seen and all(seen)
        assert call(c, "GET", f"{V1}/exams/{KEY}", token=TOKEN).status_code == 200
        assert call(c, "GET", f"{V1}/exams/{KEY}", token=NEW).status_code == 200
        assert call(c, "GET", f"{V1}/exams/{KEY}", token="y" * 40).status_code == 401
