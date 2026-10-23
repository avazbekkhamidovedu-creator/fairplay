"""DKU OJ bridge (docs/dkuoj-bridge.md), against a FAKE DKU OJ (httpx.MockTransport).
Nothing here talks to a real DKU OJ."""
import asyncio
import copy
import hashlib
import hmac
import io
import json
import time
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.integrations.dkuoj import adapter, client as ojclient, sync as ojsync
from app.main import create_app
from app.timeutil import iso, utcnow

from .conftest import SAMPLE_EXAM, login

OJ = "https://oj.test"
TOKEN = "fairplay-shared-token-0123456789abcdef"
KEY = "cs101a-midterm"


def png_bytes() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (4, 3), (200, 10, 10)).save(buf, "PNG")
    return buf.getvalue()


PNG = png_bytes()


def package(version="7f3a9c1e"):
    return {
        "key": KEY, "package_version": version, "total_points": 100, "description_md": "Closed book.",
        "questions": [
            {"id": "p000123", "order": 1, "label": "Q1", "title": "Hash maps", "type": "choice", "points": 10,
             "partial": False, "statement_md": "Which structure? ![cell](fp-asset:cell.png)", "statement_html": None,
             "choice": {"multiple": False, "true_false": False, "shuffle": True,
                        "options": [{"id": 501, "text_md": "Array"}, {"id": 502, "text_md": "Hash table"}]}},
            {"id": "p000128", "order": 2, "label": "Q2", "title": "Pick all", "type": "choice", "points": 8,
             "partial": True, "statement_md": "Pick the O(1) ones", "statement_html": None,
             "choice": {"multiple": True, "true_false": False, "shuffle": False,
                        "options": [{"id": 601, "text_md": "dict get"}, {"id": 602, "text_md": "list.index"},
                                    {"id": 603, "text_md": "set add"}]}},
            {"id": "p000129", "order": 3, "label": "Q3", "title": "TF", "type": "choice", "points": 2,
             "partial": False, "statement_md": "Python lists are arrays.", "statement_html": None,
             "choice": {"multiple": False, "true_false": True, "shuffle": False,
                        "options": [{"id": 701, "text_md": "True"}, {"id": 702, "text_md": "False"}]}},
            {"id": "p000125", "order": 4, "label": "Q4", "title": "Fill", "type": "fill_blank", "points": 6,
             "partial": True, "statement_md": "A ___ maps keys to values; lookup is O(___).",
             "fill_blank": {"blanks": [{"index": 0, "points": 3}, {"index": 1, "points": 3}], "case_sensitive": False}},
            {"id": "p000126", "order": 5, "label": "Q5", "title": "Explain", "type": "short_answer", "points": 10,
             "partial": True, "statement_md": None, "statement_html": "<p>Explain <b>hashing</b></p>",
             "short_answer": {}},
            {"id": "p000127", "order": 6, "label": "Q6", "title": "Report", "type": "file_upload", "points": 10,
             "partial": True, "statement_md": "Upload your report",
             "file_upload": {"allowed_extensions": ["pdf", "png", "jpg"], "max_file_size": 1048576,
                             "instructions_md": "One PDF"}},
            {"id": "p000124", "order": 7, "label": "Q7", "title": "Reverse a list", "type": "code", "points": 54,
             "partial": True, "statement_md": "Write ~O(n)~ code",
             "code": {"languages": [{"key": "PY3", "name": "Python 3"}, {"key": "CPP17", "name": "C++17"},
                                    {"key": "RUST", "name": "Rust"}],
                      "time_limit_s": 2.0, "memory_limit_kb": 262144,
                      "templates": {"PY3": "def reverse(head):\n    pass\n"},
                      "samples": [{"input": "1 2 3\n", "output": "3 2 1\n"}]}},
        ],
        "assets": [{"name": "cell.png", "sha256": hashlib.sha256(PNG).hexdigest(), "bytes": len(PNG),
                    "content_type": "image/png"}],
    }


class FakeOJ:
    def __init__(self):
        now = utcnow()
        self.start = now + timedelta(hours=1)
        self.end = now + timedelta(hours=3)
        self.listing_updated = iso(now)
        self.package = package()
        self.too_early = False
        self.calls: list[str] = []
        self.submissions: dict[str, dict] = {}     # attempt_id -> body
        self.submit_fail: dict[str, list[int]] = {}   # username -> statuses to return first
        self.uploads: dict[str, bytes] = {}
        self.results: list[dict] = []
        self.asset_bytes = {"cell.png": PNG}
        self.removed = False
        self.roster = [
            {"username": "alice", "first_name": "Alice", "last_name": "Chen", "email": "a@x.edu", "extra_time_seconds": 600},
            {"username": "carol", "first_name": "Carol", "last_name": "Wang", "email": "c@x.edu", "extra_time_seconds": 0},
            {"username": "ax19", "first_name": "Avazbek", "last_name": "Khamidov", "email": "ax19@x.edu",
             "extra_time_seconds": 0},
        ]
        self.editors = [{"username": "teacher", "first_name": "Demo", "last_name": "Teacher", "email": "t@x.edu"},
                        {"username": "sd123", "first_name": "Shuai", "last_name": "Dong", "email": "sd@x.edu"}]

    def listing(self):
        return {"key": KEY, "name": "CS 101 Midterm", "course": {"id": 12, "name": "CS 101"},
                "classes": [{"id": 31, "name": "CS 101 Section A"}],
                "start_time": iso(self.start), "end_time": iso(self.end), "time_limit_seconds": 5400,
                "results_hidden": True, "package_version": self.package["package_version"],
                "removed": self.removed,
                "settings": {"calculator": "scientific", "open_book": False, "websites": ["docs.python.org", "*.com"],
                             "strong_lock": True, "resume_needs_approval": True},
                "editors": self.editors, "roster": self.roster, "updated_at": self.listing_updated}

    def handle(self, req: httpx.Request) -> httpx.Response:
        u = urlsplit(str(req.url))
        path = u.path
        self.calls.append(f"{req.method} {path}")
        if req.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"detail": "bad token"})
        assert path.startswith("/api/fairplay/v1/")
        p = path[len("/api/fairplay/v1/"):]
        q = parse_qs(u.query)
        if p == "exams":
            return httpx.Response(200, json={"exams": [self.listing()], "next_page": None,
                                             "server_time": iso(utcnow())})
        if p == f"exams/{KEY}/package":
            if self.too_early:
                return httpx.Response(425, headers={"Retry-After": "3600"}, json={"detail": "too early"})
            etag = f'"{self.package["package_version"]}"'
            if req.headers.get("if-none-match") == etag:
                return httpx.Response(304, headers={"ETag": etag})
            return httpx.Response(200, json=self.package, headers={"ETag": etag})
        if p.startswith(f"exams/{KEY}/assets/"):
            name = p.rsplit("/", 1)[1]
            return httpx.Response(200, content=self.asset_bytes[name], headers={"Content-Type": "image/png"})
        if p == f"exams/{KEY}/uploads":
            data = req.content
            assert hashlib.sha256(data).hexdigest() == req.headers["x-sha256"]
            assert req.headers["x-question-id"] == "p000127"
            up = f"up_{len(self.uploads) + 1}"
            self.uploads[up] = data
            return httpx.Response(200, json={"upload_id": up})
        if p == f"exams/{KEY}/submissions":
            body = json.loads(req.content)
            fails = self.submit_fail.get(body["username"]) or []
            if fails:
                code = fails.pop(0)
                return httpx.Response(code, json={"detail": "busy" if code >= 500 else "conflict"})
            old = self.submissions.get(body["attempt_id"])
            if old is not None and old != body:
                return httpx.Response(409, json={"detail": "different body"})
            self.submissions[body["attempt_id"]] = body
            return httpx.Response(200, json={"receipt": f"oj_rcpt_{body['attempt_id']}",
                                             "submissions": [{"question_id": a["question_id"], "submission_id": i}
                                                             for i, a in enumerate(body["answers"])]})
        if p == f"exams/{KEY}/results":
            since = (q.get("changed_since") or [None])[0]
            rows = [r for r in self.results if since is None or r["updated_at"] > since]
            return httpx.Response(200, json={"results": rows, "next_page": None, "server_time": iso(utcnow())})
        return httpx.Response(404, json={"detail": "nope"})


@pytest.fixture
def fake(monkeypatch):
    f = FakeOJ()
    monkeypatch.setenv("DKUOJ_URL", OJ)
    monkeypatch.setenv("DKUOJ_TOKEN", TOKEN)
    monkeypatch.setattr(ojsync, "BACKGROUND", False)
    ojclient.set_transport_factory(lambda base: httpx.MockTransport(f.handle))

    async def no_sleep(_s):
        return None
    ojclient.set_sleep(no_sleep)
    yield f
    ojclient.set_transport_factory(None)
    ojclient.set_sleep(None)


def oj_settings(tmp_path, static_dir, **kw):
    return Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'oj.db'}", redis_url=None,
                    secret="test-secret-0123456789abcdef", pbkdf2_iterations=1000,
                    require_app=False, app_keys=["test-app-key"], dev=False, seed_loadtest_users=0,
                    seed_demo=True, single_process=True, static_dir=str(static_dir),
                    uploads_dir=str(tmp_path / "uploads"),
                    public_url="https://exam.test", **kw)


@pytest.fixture
def oj(tmp_path, static_dir, fake):
    with TestClient(create_app(oj_settings(tmp_path, static_dir))) as c:
        yield c


def run(c, now=None):
    async def go():
        from app.db import get_database
        return await ojsync.run_once(get_database().sessionmaker, now=now)
    return c.portal.call(go)


def db(c, fn):
    """Run fn(session) inside the app's loop and return its result."""
    async def go():
        from app.db import get_database
        async with get_database().sessionmaker() as s:
            return await fn(s)
    return c.portal.call(go)


def oj_exam(c, teacher):
    return next(e for e in c.get("/api/exams", headers=teacher).json() if e.get("external"))


def start(c, h, exam_id, code):
    return c.post(f"/api/student/exams/{exam_id}/start", json={"start_code": code}, headers=h)


def deliveries(c):
    from sqlalchemy import select
    from app.integrations.models import ExternalDelivery as OjDelivery

    async def q(s):
        return {d.attempt_id: (d.status, d.tries, d.last_error, d.receipt, d.next_at)
                for d in (await s.execute(select(OjDelivery))).scalars()}
    return db(c, q)


# ---------------------------------------------------------------- adapter
def test_adapter_round_trip_every_type():
    pkg = package()
    fqs = adapter.questions_from_package(pkg, "cs 101")
    assert [q["type"] for q in fqs] == ["mcq", "mcq", "mcq", "fill_blank", "short", "file_upload", "code"]
    for oq, fq in zip(pkg["questions"], fqs):
        back = adapter.question_to_oj(fq)
        for k in ("id", "order", "label", "title", "type", "points", "partial", "statement_md", "statement_html"):
            assert back[k] == oq.get(k), (oq["id"], k)
        sub = oq["type"]
        if sub == "code":
            want = dict(oq["code"])
            want["languages"] = [{"key": x["key"], "name": x["name"]} for x in want["languages"]]
            assert back["code"] == want
        else:
            assert back[sub] == oq[sub], oq["id"]
        # no answer key fields ever filled
        assert fq["correct_index"] is None and fq["accepted_answers"] == [] and fq["answer_number"] is None
    code = fqs[6]
    assert code["languages"] == [{"key": "PY3", "name": "Python 3", "runner": "python"},
                                 {"key": "CPP17", "name": "C++17", "runner": "cpp"},
                                 {"key": "RUST", "name": "Rust", "runner": None}]
    assert code["time_limit_ms"] == 2000 and code["tests"] == [{"input": "1 2 3\n", "expected": "3 2 1\n",
                                                                  "visible": True, "points": 0}]
    assert fqs[4]["prompt_format"] == "html" and fqs[0]["prompt_format"] == "markdown"
    # answers both ways
    values = {0: 1, 1: [0, 2], 2: 0, 3: ["hash table", "1"], 4: "because", 5: ["f1"],
              6: {"language": "CPP17", "source": "int main(){}"}}
    for i, v in values.items():
        out = adapter.answer_to_oj(fqs[i], v, {"f1": "up_9"})
        assert out["question_id"] == pkg["questions"][i]["id"]
        back = adapter.answer_from_oj(fqs[i], out)
        assert back == ({"language": "CPP17", "source": "int main(){}"} if i == 6 else
                        ["up_9"] if i == 5 else v)
    assert adapter.answer_to_oj(fqs[0], 1)["choice"] == {"selected": [502]}
    assert adapter.answer_to_oj(fqs[1], [2, 0])["choice"] == {"selected": [601, 603]}
    assert adapter.answer_to_oj(fqs[3], ["x"])["fill_blank"] == {"answers": ["x", ""]}
    assert adapter.answer_to_oj(fqs[4], "  ") is None and adapter.answer_to_oj(fqs[5], ["unknown"]) is None
    # runners per contract
    assert [adapter.runner_for(k) for k in ("PY3", "PYPY3", "JS", "NODE", "V8JS", "C", "C11", "CPP20", "JAVA17", "GO")] \
        == ["python", "python", "javascript", "javascript", "javascript", "c", "c", "cpp", "java", None]


def test_adapter_refuses_bad_packages():
    bad = package()
    bad["questions"][0]["type"] = "essay"
    with pytest.raises(adapter.AdapterError):
        adapter.questions_from_package(bad)
    bad = package()
    bad["assets"][0]["name"] = "../x.png"
    with pytest.raises(adapter.AdapterError):
        adapter.asset_names(bad)
    # unknown fields (an answer key, if one ever came) are not copied
    leaky = package()
    leaky["questions"][0]["choice"]["options"][1]["is_correct"] = True
    leaky["questions"][4]["short_answer"] = {"reference_answer": "SECRET"}
    fqs = adapter.questions_from_package(leaky)
    assert "is_correct" not in json.dumps(fqs) and "SECRET" not in json.dumps(fqs)


# ---------------------------------------------------------------- the whole flow
def test_full_flow(oj, fake):
    teacher, alice, carol, bob = (login(oj, u) for u in ("teacher", "alice", "carol", "bob"))
    out = run(oj)
    assert out["exams"] == 1 and out["packages"] == 1, out
    e = oj_exam(oj, teacher)
    x = e["external"]
    assert x["source"] == "dkuoj" and x["name"] == "DKU OJ" and x["read_only"] and x["graded_externally"]
    assert x["key"] == KEY and x["package_ready"] is True and x["start_time"] and x["end_time"]
    assert e["title"] == "CS 101 Midterm" and e["duration_min"] == 90 and e["course"] == "CS 101"
    assert e["calculator"] == "scientific" and e["websites"] == ["docs.python.org"]   # "*.com" refused
    assert e["require_strong_lock"] is True and x["version"] == "7f3a9c1e"
    assert e["question_count"] == 7 and e["total_points"] == 100
    assert x["url"] == f"{OJ}/contest/{KEY}"
    # accounts: new roster student and editor created without a usable password
    r = oj.post("/api/auth/login", json={"username": "ax19", "password": "!dkuoj-import"})
    assert r.status_code == 401
    # read-only in the editor; grading refused
    body = {**SAMPLE_EXAM}
    r = oj.put(f"/api/exams/{e['id']}", json=body, headers=teacher)
    assert r.status_code == 409 and "Edit this exam in DKU OJ" in r.json()["detail"]
    assert oj.delete(f"/api/exams/{e['id']}", headers=teacher).status_code == 409
    # teacher asset route
    r = oj.get(f"/api/exams/{e['id']}/assets/cell.png", headers=teacher)
    assert r.status_code == 200 and r.content == PNG

    e = oj.post(f"/api/exams/{e['id']}/open", headers=teacher).json()
    code = e["start_code"]
    assert e["status"] == "live"
    # only the DKU OJ roster may start (bob is in the teacher's demo class but not listed)
    assert start(oj, bob, e["id"], code).status_code == 403
    assert e["id"] not in [x["id"] for x in oj.get("/api/student/exams", headers=bob).json()]
    lst = oj.get("/api/student/exams", headers=alice).json()
    row = next(x for x in lst if x["id"] == e["id"])
    assert row["source"] == "dkuoj" and row["external_name"] == "DKU OJ"

    a = start(oj, alice, e["id"], code)
    assert a.status_code == 200, a.text
    a = a.json()
    aid = a["attempt_id"]
    assert a["source"] == "dkuoj" and a["external_name"] == "DKU OJ" and a["total_points"] == 100
    assert a["answer_files"] == []
    # extra time from the roster (600 s) on top of the 90 minutes
    from app.timeutil import parse_iso
    assert (parse_iso(a["deadline_at"]) - parse_iso(a["started_at"])).total_seconds() == 90 * 60 + 600
    # student view: no OJ ids, no secrets, the new type fields are there
    text = json.dumps(a["questions"])
    for leak in ('"oj"', "external_ref", "option_ids", "501", "p000123"):
        assert leak not in text, leak
    qs = {q["label"]: q for q in a["questions"]}
    assert qs["Q1"]["prompt_format"] == "markdown" and "fp-asset:cell.png" in qs["Q1"]["prompt"]
    assert qs["Q1"]["multiple"] is False and qs["Q1"]["shuffle"] is True and qs["Q1"]["points"] == 10
    assert qs["Q2"]["multiple"] is True and qs["Q3"]["true_false"] is True
    assert qs["Q4"]["blanks"] == 2 and qs["Q4"]["blank_points"] == [3, 3]
    assert qs["Q5"]["prompt_format"] == "html"
    assert qs["Q6"]["allowed_extensions"] == ["pdf", "png", "jpg"] and qs["Q6"]["max_file_size"] == 1048576
    assert [x["runner"] for x in qs["Q7"]["languages"]] == ["python", "cpp", None]
    assert qs["Q7"]["templates"] == {"PY3": "def reverse(head):\n    pass\n"}
    assert qs["Q7"]["tests"] == [{"input": "1 2 3\n", "expected": "3 2 1\n"}]
    for q in a["questions"]:
        assert q["correct_index"] is None if "correct_index" in q else True
        assert "accepted_answers" not in q
    # version frozen at the first start
    assert oj_exam(oj, teacher)["external"]["frozen_version"] == "7f3a9c1e"
    # a new package in DKU OJ never changes the running exam
    fake.package = package("99999999")
    fake.package["questions"][0]["title"] = "Changed"
    n_calls = len(fake.calls)
    run(oj)
    e2 = oj_exam(oj, teacher)
    assert e2["external"]["version"] == "7f3a9c1e" and e2["questions"][0]["title"] == "Hash maps"
    assert not any(c.endswith("/package") for c in fake.calls[n_calls:]), fake.calls

    # assets through the attempt
    r = oj.get(f"/api/student/attempts/{aid}/assets/cell.png", headers=alice)
    assert r.status_code == 200 and r.content == PNG and r.headers["etag"] == f'"{hashlib.sha256(PNG).hexdigest()}"'
    assert oj.get(f"/api/student/attempts/{aid}/assets/cell.png", headers=carol).status_code == 404
    assert oj.get(f"/api/student/attempts/{aid}/assets/nope.png", headers=alice).status_code == 404

    # a file for Q6
    qid = {q["label"]: q["id"] for q in a["questions"]}
    r = oj.post(f"/api/student/attempts/{aid}/files?question_id={qid['Q6']}",
                files={"file": ("report.exe", b"MZ", "application/octet-stream")}, headers=alice)
    assert r.status_code == 422 and "pdf" in r.json()["detail"]
    r = oj.post(f"/api/student/attempts/{aid}/files?question_id={qid['Q6']}",
                files={"file": ("report.pdf", b"%PDF-1.4 hello", "application/pdf")}, headers=alice)
    assert r.status_code == 201, r.text
    fid = r.json()["id"]
    assert oj.post(f"/api/student/attempts/{aid}/files?question_id={qid['Q1']}",
                   files={"file": ("x.pdf", b"%PDF", "application/pdf")}, headers=alice).status_code == 404

    def save(answers, seq):
        return oj.put(f"/api/student/attempts/{aid}/answers", json={"answers": answers, "client_seq": seq},
                      headers=alice)
    # invalid answers are refused by the normal answer path
    assert save({qid["Q1"]: 5}, 1).status_code == 422
    assert save({qid["Q2"]: 1}, 1).status_code == 422
    assert save({qid["Q4"]: ["a", "b", "c"]}, 1).status_code == 422
    assert save({qid["Q6"]: ["someone-elses-file"]}, 1).status_code == 422
    assert save({qid["Q7"]: {"language": "JAVA", "source": "x"}}, 1).status_code == 422
    assert save({qid["Q7"]: {"language": "PY3", "source": "x", "extra": 1}}, 1).status_code == 422
    answers = {qid["Q1"]: 1, qid["Q2"]: [2, 0], qid["Q3"]: 1, qid["Q4"]: ["hash table", "1"],
               qid["Q5"]: "It spreads keys over buckets.", qid["Q6"]: [fid],
               qid["Q7"]: {"language": "PY3", "source": "def reverse(h):\n    return h[::-1]\n"}}
    r = save(answers, 2)
    assert r.status_code == 200 and r.json()["saved"] is True
    back = oj.get(f"/api/student/attempts/{aid}", headers=alice).json()
    assert back["answers"] == {**answers, qid["Q2"]: [0, 2]}     # multiple: stored sorted
    assert [f["id"] for f in back["answer_files"]] == [fid] and back["answer_files"][0]["name"] == "report.pdf"
    assert oj.get(f"/api/student/attempts/{aid}/files/{fid}", headers=alice).content == b"%PDF-1.4 hello"
    # live room works like any exam
    live = oj.get(f"/api/exams/{e['id']}/live", headers=teacher).json()
    assert live["students"][0]["answered"] == 7

    # hand-in: receipt at once, outbox row written with it
    fake.submit_fail["alice"] = [503]
    r = oj.post(f"/api/student/attempts/{aid}/submit", json={"answers": back["answers"], "client_seq": 3},
                headers=alice)
    assert r.status_code == 200 and r.json()["receipt"]
    assert deliveries(oj)[aid][:2] == ("pending", 0)
    # 503 -> failed, retried after 30 s
    t0 = utcnow()
    run(oj, now=t0)
    st, tries, err, rcpt, next_at = deliveries(oj)[aid]
    assert (st, tries) == ("failed", 1) and "503" in err and next_at == t0 + timedelta(seconds=30)
    run(oj, now=t0 + timedelta(seconds=10))          # not due yet
    assert deliveries(oj)[aid][0] == "failed"
    run(oj, now=t0 + timedelta(seconds=31))
    st, tries, err, rcpt, _ = deliveries(oj)[aid]
    assert (st, tries, err, rcpt) == ("sent", 2, None, f"oj_rcpt_{aid}")
    body = fake.submissions[aid]
    assert body["username"] == "alice" and body["package_version"] == "7f3a9c1e"
    assert body["handed_in_evidence"] == "server" and body["started_at"].endswith("Z")
    assert body["integrity_report_url"] == f"https://exam.test/teacher/exams/{e['id']}/attempts/{aid}"
    by = {x["question_id"]: x for x in body["answers"]}
    assert by["p000123"] == {"question_id": "p000123", "choice": {"selected": [502]}}
    assert by["p000128"]["choice"] == {"selected": [601, 603]}
    assert by["p000129"]["choice"] == {"selected": [702]}
    assert by["p000125"]["fill_blank"] == {"answers": ["hash table", "1"]}
    assert by["p000126"]["short_answer"] == {"text": "It spreads keys over buckets."}
    assert by["p000127"]["file_upload"] == {"upload_ids": ["up_1"]} and fake.uploads["up_1"] == b"%PDF-1.4 hello"
    assert by["p000124"]["code"] == {"language": "PY3", "source": "def reverse(h):\n    return h[::-1]\n"}
    assert len(fake.uploads) == 1          # the file went up once, not per try

    # carol: DKU OJ answers 409 -> dead + an event for the teacher
    c_att = start(oj, carol, e["id"], code).json()
    oj.put(f"/api/student/attempts/{c_att['attempt_id']}/answers",
           json={"answers": {qid["Q5"]: "short"}, "client_seq": 1}, headers=carol)
    fake.submit_fail["carol"] = [409]
    oj.post(f"/api/student/attempts/{c_att['attempt_id']}/submit", json={"answers": {qid["Q5"]: "short"},
            "client_seq": 2}, headers=carol)
    run(oj, now=utcnow())
    assert deliveries(oj)[c_att["attempt_id"]][0] == "dead"
    res = oj.get(f"/api/exams/{e['id']}/results", headers=teacher).json()
    assert res["external"]["graded_externally"] is True and res["external"]["name"] == "DKU OJ"
    carol_row = next(x for x in res["attempts"] if x["username"] == "carol")
    assert carol_row["external_delivery"]["status"] == "dead"
    assert any(ev["type"] == "external_delivery_failed" for ev in carol_row["events"])
    status = oj.get("/api/integrations/dkuoj/status", headers=teacher).json()
    assert status["deliveries"]["dead"] == 1 and status["deliveries"]["sent"] == 1
    assert TOKEN not in json.dumps(status)
    # no local grading for OJ exams
    alice_row = next(x for x in res["attempts"] if x["username"] == "alice")
    assert alice_row["score"] == 0 and all(not ans["correct"] for ans in alice_row["answers"])
    assert oj.put(f"/api/exams/{e['id']}/attempts/{aid}/grades/{qid['Q5']}", json={"points": 1},
                  headers=teacher).status_code == 409
    oj.post(f"/api/exams/{e['id']}/close", headers=teacher)
    assert oj.post(f"/api/exams/{e['id']}/release", headers=teacher).status_code == 409

    # results: not before the end; released ones after it
    grades = oj.get("/api/student/grades", headers=alice).json()
    mine = next(g for g in grades if g["exam_id"] == e["id"])
    assert mine["released"] is False and mine["score"] is None
    assert oj.get(f"/api/student/grades/{e['id']}", headers=alice).status_code == 403
    fake.results = [{"username": "alice", "released": True, "total": 82, "total_points": 100,
                     "updated_at": iso(utcnow()),
                     "questions": [{"question_id": "p000123", "points": 10, "feedback_md": ""},
                                   {"question_id": "p000126", "points": 7, "feedback_md": "Mention *collisions*."},
                                   {"question_id": "p000124", "points": 50, "feedback_md": ""}]}]
    run(oj, now=fake.end - timedelta(minutes=1))
    assert oj.get("/api/student/grades", headers=alice).json()[0]["released"] is False
    run(oj, now=fake.end + timedelta(minutes=1))
    g = next(x for x in oj.get("/api/student/grades", headers=alice).json() if x["exam_id"] == e["id"])
    assert g["released"] is True and g["score"] == 82 and g["total_points"] == 100 and g["percent"] == 82.0
    d = oj.get(f"/api/student/grades/{e['id']}", headers=alice).json()
    assert d["score"] == 82 and d["external_name"] == "DKU OJ" and d["source"] == "dkuoj"
    dq = {q["label"]: q for q in d["questions"]}
    assert dq["Q1"]["status"] == "correct" and dq["Q1"]["points_awarded"] == 10
    assert dq["Q5"]["status"] == "partial" and dq["Q5"]["comment"] == "Mention *collisions*."
    assert dq["Q4"]["status"] == "pending" and all(q["correct_answer"] is None for q in d["questions"])
    assert "external_ref" not in dq["Q1"] and "option_ids" not in json.dumps(d)
    lst = oj.get("/api/student/exams", headers=alice).json()
    assert next(x for x in lst if x["id"] == e["id"])["attempt"]["percent"] == 82.0
    # carol has no released result
    assert oj.get(f"/api/student/grades/{e['id']}", headers=carol).status_code == 403
    # a withdrawn release hides it again
    fake.results = [{**fake.results[0], "released": False, "updated_at": iso(utcnow() + timedelta(seconds=5))}]
    run(oj, now=fake.end + timedelta(minutes=3))
    assert oj.get(f"/api/student/grades/{e['id']}", headers=alice).status_code == 403


def test_package_waits_until_24h_before_and_honours_425(oj, fake):
    teacher = login(oj, "teacher")
    fake.start = utcnow() + timedelta(days=3)
    fake.end = fake.start + timedelta(hours=2)
    out = run(oj)
    assert out["packages"] == 0 and not any(c.endswith("/package") for c in fake.calls)
    e = oj_exam(oj, teacher)
    assert e["question_count"] == 0 and e["external"]["package_ready"] is False
    r = oj.post(f"/api/exams/{e['id']}/open", headers=teacher)
    assert r.status_code == 409 and "24 hours" in r.json()["detail"]
    # 24 h before: DKU OJ still says 425 -> retried after Retry-After, not every loop
    fake.too_early = True
    t = fake.start - timedelta(hours=23)
    run(oj, now=t)
    n = sum(c.endswith("/package") for c in fake.calls)
    assert n == 1
    run(oj, now=t + timedelta(minutes=10))
    assert sum(c.endswith("/package") for c in fake.calls) == 1
    fake.too_early = False
    run(oj, now=t + timedelta(hours=1, minutes=1))
    assert sum(c.endswith("/package") for c in fake.calls) == 2
    assert oj_exam(oj, teacher)["question_count"] == 7
    # unchanged afterwards: no new download (304 or not due)
    run(oj, now=t + timedelta(hours=2))
    assert sum(c.endswith("/package") for c in fake.calls) == 2


def test_asset_hash_mismatch_keeps_package_out(oj, fake):
    teacher = login(oj, "teacher")
    fake.asset_bytes["cell.png"] = PNG + b"x"
    run(oj)
    e = oj_exam(oj, teacher)
    assert e["question_count"] == 0 and e["external"]["version"] is None


def test_live_link_signs_in_editors_only(oj, fake):
    run(oj)
    teacher = login(oj, "teacher")
    e = oj_exam(oj, teacher)

    def link(user="teacher", exp=None, key=KEY, token=TOKEN):
        exp = str(exp or int(time.time()) + 120)
        sig = hmac.new(token.encode(), f"live|{key}|{user}|{exp}".encode(), hashlib.sha256).hexdigest()
        return f"/integrations/dkuoj/live?exam={key}&user={user}&exp={exp}&sig={sig}"
    r = oj.get(link(), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/teacher/external-live?code=")
    code = r.headers["location"].split("code=", 1)[1]
    ex = oj.post("/api/integrations/live/exchange", json={"code": code})
    assert ex.status_code == 200 and ex.json()["exam_id"] == e["id"] and ex.json()["role"] == "teacher"
    h = {"Authorization": f"Bearer {ex.json()['token']}"}
    assert oj.get(f"/api/exams/{e['id']}/live", headers=h).status_code == 200
    assert oj.post("/api/integrations/live/exchange", json={"code": code}).status_code == 403     # once
    good = link(exp=int(time.time()) + 100)
    assert oj.get(good, follow_redirects=False).status_code == 303
    assert oj.get(good, follow_redirects=False).status_code == 403                     # link used
    assert oj.get(link(token="wrong-token-wrong-token-wrong-token"), follow_redirects=False).status_code == 403
    assert oj.get(link(exp=int(time.time()) - 1), follow_redirects=False).status_code == 403
    assert oj.get(link(exp=int(time.time()) + 3600), follow_redirects=False).status_code == 403
    assert oj.get(link(user="alice"), follow_redirects=False).status_code == 403        # a student
    assert oj.get(link(key="other-exam"), follow_redirects=False).status_code == 403
    # sd123 is an editor (account created by the sync): signed in; a non-editor teacher is not
    assert oj.get(link(user="sd123", exp=int(time.time()) + 90), follow_redirects=False).status_code == 303
    fake.editors = [fake.editors[1]]
    fake.listing_updated = iso(utcnow() + timedelta(seconds=1))
    run(oj)
    assert oj.get(link(user="teacher", exp=int(time.time()) + 80), follow_redirects=False).status_code == 403
    # the demo teacher lost access to the exam in the teacher API too
    assert oj.get(f"/api/exams/{e['id']}", headers=teacher).status_code == 404
    assert e["id"] not in [x["id"] for x in oj.get("/api/exams", headers=teacher).json()]


def test_bridge_off_hides_routes(client):
    assert client.get("/integrations/dkuoj/live?exam=x&user=y&exp=1&sig=" + "0" * 64,
                      follow_redirects=False).status_code == 404
    assert client.get("/integrations/nope/live", follow_redirects=False).status_code == 404
    assert client.post("/api/integrations/live/exchange", json={"code": "abcdefgh"}).status_code == 403
    t = login(client, "teacher")
    assert client.get("/api/integrations/dkuoj/status", headers=t).json()["enabled"] is False


def test_normal_exams_refuse_structured_answers(client, teacher, alice, live_exam):
    a = client.post(f"/api/student/exams/{live_exam['id']}/start", json={"start_code": live_exam["start_code"]},
                    headers=alice).json()
    qid = a["questions"][0]["id"]
    for v in ([1, 2], {"language": "x", "source": "y"}):
        r = client.put(f"/api/student/attempts/{a['attempt_id']}/answers",
                       json={"answers": {qid: v}, "client_seq": 1}, headers=alice)
        assert r.status_code == 422
    assert a["source"] == "fairplay" and "external_name" not in a and "answer_files" not in a
    assert "prompt_format" not in a["questions"][0]
    assert client.get(f"/api/exams/{live_exam['id']}", headers=teacher).json()["external"] is None


def test_outbox_survives_restart_and_close_writes_it(tmp_path, static_dir, fake):
    s = oj_settings(tmp_path, static_dir)
    with TestClient(create_app(s)) as c:
        teacher, alice = login(c, "teacher"), login(c, "alice")
        run(c)
        e = oj_exam(c, teacher)
        e = c.post(f"/api/exams/{e['id']}/open", headers=teacher).json()
        a = start(c, alice, e["id"], e["start_code"]).json()
        qs = {q["label"]: q["id"] for q in a["questions"]}
        c.put(f"/api/student/attempts/{a['attempt_id']}/answers",
              json={"answers": {qs["Q3"]: 0}, "client_seq": 1}, headers=alice)
        # the teacher closes: the auto hand-in writes the outbox row too
        c.post(f"/api/exams/{e['id']}/close", headers=teacher)
        assert deliveries(c)[a["attempt_id"]][0] == "pending"
    with TestClient(create_app(oj_settings(tmp_path, static_dir))) as c:     # a restart
        assert deliveries(c)[a["attempt_id"]][0] == "pending"
        run(c)
        assert deliveries(c)[a["attempt_id"]][0] == "sent"
    body = fake.submissions[a["attempt_id"]]
    assert body["answers"] == [{"question_id": "p000129", "choice": {"selected": [701]}}]


def test_delivery_backoff_and_give_up():
    from app.integrations.dkuoj.delivery import backoff
    assert [backoff(n).total_seconds() for n in (1, 2, 3, 4, 5, 6, 7, 20)] == \
        [30, 60, 120, 240, 480, 900, 900, 900]


def test_one_runner_lock(oj):
    from app.integrations.dkuoj.sync import acquire_lock, release_lock
    now = utcnow()

    async def go(s):
        a = await acquire_lock(s, "worker-a", now)
        b = await acquire_lock(s, "worker-b", now)
        a2 = await acquire_lock(s, "worker-a", now + timedelta(seconds=10))     # renew
        b_late = await acquire_lock(s, "worker-b", now + timedelta(seconds=200))  # a's lock expired
        a_back = await acquire_lock(s, "worker-a", now + timedelta(seconds=201))
        await release_lock(s, "worker-b")
        a_free = await acquire_lock(s, "worker-a", now + timedelta(seconds=202))
        return a, b, a2, b_late, a_back, a_free
    assert db(oj, go) == (True, False, True, True, False, True)


def test_removed_exam_cannot_start(oj, fake):
    teacher, alice = login(oj, "teacher"), login(oj, "alice")
    run(oj)
    e = oj_exam(oj, teacher)
    e = oj.post(f"/api/exams/{e['id']}/open", headers=teacher).json()
    fake.removed = True
    fake.listing_updated = iso(utcnow() + timedelta(seconds=1))
    run(oj)
    assert oj_exam(oj, teacher)["external"]["removed"] is True
    from app.services import invalidate_exam
    from app.store import get_store
    oj.portal.call(invalidate_exam, get_store(), e["id"])
    r = start(oj, alice, e["id"], e["start_code"])
    assert r.status_code == 409 and "switched off" in r.json()["detail"]
    # once DKU OJ removed it, the teacher may delete it (roster, editors, assets go with it)
    oj.post(f"/api/exams/{e['id']}/close", headers=teacher)
    assert oj.delete(f"/api/exams/{e['id']}", headers=teacher).status_code == 204

    async def left(s):
        from sqlalchemy import func, select
        from app.integrations.dkuoj.models import OjAsset, OjEditor, OjRosterEntry
        return [(await s.execute(select(func.count()).select_from(m))).scalar_one()
                for m in (OjAsset, OjEditor, OjRosterEntry)]
    assert db(oj, left) == [0, 0, 0]


def test_sealed_submit_writes_outbox_with_sealed_evidence(oj, fake):
    from .test_s5_sealed_submit import seal
    teacher, alice = login(oj, "teacher"), login(oj, "alice")
    run(oj)
    e = oj_exam(oj, teacher)
    e = oj.post(f"/api/exams/{e['id']}/open", headers=teacher).json()
    a = start(oj, alice, e["id"], e["start_code"]).json()
    qs = {q["label"]: q["id"] for q in a["questions"]}
    answers = {qs["Q2"]: [1], qs["Q7"]: {"language": "CPP17", "source": "int main(){}"}}
    r = oj.post(f"/api/student/attempts/{a['attempt_id']}/sealed-submit", headers=alice,
                json=seal(a["attempt_id"], a["session"], answers))
    assert r.status_code == 200, r.text
    assert deliveries(oj)[a["attempt_id"]][0] == "pending"
    run(oj)
    body = fake.submissions[a["attempt_id"]]
    assert body["handed_in_evidence"] == "sealed"
    assert {x["question_id"]: x for x in body["answers"]}["p000124"]["code"]["language"] == "CPP17"
    # a bad structured answer inside a seal is refused like a normal save
    c = login(oj, "carol")
    b = start(oj, c, e["id"], e["start_code"]).json()
    r = oj.post(f"/api/student/attempts/{b['attempt_id']}/sealed-submit", headers=c,
                json=seal(b["attempt_id"], b["session"], {qs["Q2"]: [9]}))
    assert r.status_code == 422


# ---------------------------------------------------------------- DKU OJ's real shapes
# Copied from DKU OJ's own builders and tests (judge/utils/fairplay_package.py,
# fairplay_delivery.py, views/api/fairplay.py): course may be null, blank points are floats,
# languages sorted by key with names, samples use "output", a removed entry has only key /
# name / times, results of a withdrawn release are just {"released": false}, every list
# answer carries server_time, and error bodies are {"detail": ...}.
def real_listing(now, removed=False):
    if removed:
        return {"key": "fpexam", "name": "CS 101 Midterm", "removed": True,
                "start_time": iso(now - timedelta(hours=1)), "end_time": iso(now + timedelta(hours=2)),
                "updated_at": iso(now + timedelta(seconds=5))}
    return {"key": "fpexam", "name": "CS 101 Midterm", "course": None,
            "classes": [{"id": 7, "name": "FP Section A"}],
            "start_time": iso(now - timedelta(hours=1)), "end_time": iso(now + timedelta(hours=2)),
            "time_limit_seconds": 5400, "results_hidden": True, "package_version": "0f1e2d3c4b5a6978",
            "removed": False,
            "settings": {"calculator": "off", "open_book": False, "websites": ["docs.python.org"],
                         "strong_lock": False, "resume_needs_approval": True},
            "editors": [{"username": "teacher", "first_name": "Shuai", "last_name": "Dong",
                         "email": "fp_teacher@example.edu"}],
            "roster": [{"username": "alice", "first_name": "Avazbek", "last_name": "Khamidov",
                        "email": "fp_student@example.edu", "extra_time_seconds": 0},
                       {"username": "carol", "first_name": "", "last_name": "", "email": "",
                        "extra_time_seconds": 0}],
            "updated_at": iso(now)}


REAL_PACKAGE = {
    "key": "fpexam", "package_version": "0f1e2d3c4b5a6978", "total_points": 66, "description_md": "",
    "questions": [
        {"id": "p000002", "order": 1, "label": "1", "title": "Hash maps", "type": "choice", "points": 10,
         "partial": True, "statement_md": "Which structure gives O(1) average lookup?", "statement_html": None,
         "choice": {"multiple": False, "true_false": False, "shuffle": True,
                    "options": [{"id": 1, "text_md": "Array"}, {"id": 2, "text_md": "Hash table"}]}},
        {"id": "p000003", "order": 2, "label": "2", "title": "Fill", "type": "fill_blank", "points": 6,
         "partial": True, "statement_md": "A ___ maps keys to values.", "statement_html": None,
         "fill_blank": {"blanks": [{"index": 0, "points": 3.0}, {"index": 1, "points": 3.0}],
                        "case_sensitive": False}},
        {"id": "p000004", "order": 3, "label": "3", "title": "Explain", "type": "short_answer", "points": 10,
         "partial": True, "statement_md": "Explain hashing.", "statement_html": None, "short_answer": {}},
        {"id": "p000005", "order": 4, "label": "4", "title": "Report", "type": "file_upload", "points": 10,
         "partial": True, "statement_md": "Upload your report.", "statement_html": None,
         "file_upload": {"allowed_extensions": ["pdf", "png"], "max_file_size": 1024, "instructions_md": ""}},
        {"id": "p000001", "order": 5, "label": "5", "title": "Reverse a list", "type": "code", "points": 30,
         "partial": True, "statement_md": "Write it.", "statement_html": None,
         "code": {"languages": [{"key": "CPP14", "name": "C++14"}, {"key": "PY3", "name": "Python 3"}],
                  "time_limit_s": 2.0, "memory_limit_kb": 262144,
                  "templates": {"PY3": "def reverse(head):\n    pass\n"},
                  "samples": [{"input": "1 2 3\n", "output": "3 2 1\n"}]}}],
    "assets": []}


class RealShapeOJ:
    """Answers like DKU OJ's views (status codes and bodies), checks requests like them."""

    def __init__(self):
        self.now = utcnow()
        self.removed = False
        self.calls, self.bodies = [], []
        self.uploads = {}
        self.package_status = 200
        self.submit_status = None
        self.results_rows = []
        self.results_since = []

    def handle(self, req):
        u = urlsplit(str(req.url))
        p = u.path[len("/api/fairplay/v1/"):]
        q = parse_qs(u.query)
        self.calls.append(f"{req.method} {p}")
        if req.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"detail": "Invalid or missing bearer token."},
                                  headers={"WWW-Authenticate": "Bearer"})
        since = (q.get("changed_since") or [None])[0]
        if p == "exams":
            rows = [real_listing(self.now, removed=True)] if self.removed and since else \
                ([] if self.removed else [real_listing(self.now)])
            return httpx.Response(200, json={"exams": rows, "next_page": None, "server_time": iso(utcnow())})
        if p == "exams/fpexam/package":
            if self.package_status == 422:
                return httpx.Response(422, json={"detail": "The exam package is larger than 5 MB; reduce the statements or images."})
            return httpx.Response(200, json=REAL_PACKAGE, headers={"ETag": '"0f1e2d3c4b5a6978"'})
        if p == "exams/fpexam/uploads":
            for h in ("x-username", "x-question-id", "x-filename", "x-sha256"):
                assert req.headers.get(h), h
            assert hashlib.sha256(req.content).hexdigest() == req.headers["x-sha256"]
            up = "up_" + "%024x" % (len(self.uploads) + 1)
            self.uploads[up] = req.headers["x-filename"]
            return httpx.Response(200, json={"upload_id": up})
        if p == "exams/fpexam/submissions":
            body = json.loads(req.content)
            self.bodies.append(body)
            if self.submit_status:
                return httpx.Response(self.submit_status, json={"detail": "Request body is larger than 2097152 bytes."})
            assert body["handed_in_evidence"] in ("server", "sealed")
            for a in body["answers"]:
                if "file_upload" in a:
                    assert len(a["file_upload"]["upload_ids"]) == 1
                if "code" in a:
                    assert a["code"]["source"].strip()
            return httpx.Response(200, json={"receipt": "oj_rcpt_" + "ab" * 12,
                                             "submissions": [{"question_id": a["question_id"], "submission_id": i + 1}
                                                             for i, a in enumerate(body["answers"])]})
        if p == "exams/fpexam/results":
            self.results_since.append(since)
            return httpx.Response(200, json={"results": self.results_rows, "next_page": None,
                                             "server_time": iso(self.now + timedelta(hours=3))})
        return httpx.Response(404, json={"detail": "No FairPlay exam with this key."})


def test_against_dkuoj_real_shapes(tmp_path, static_dir, monkeypatch):
    real = RealShapeOJ()
    monkeypatch.setattr(ojsync, "BACKGROUND", False)
    monkeypatch.setenv("DKUOJ_URL", OJ)
    monkeypatch.setenv("DKUOJ_TOKEN", TOKEN)
    ojclient.set_transport_factory(lambda base: httpx.MockTransport(real.handle))

    async def no_sleep(_s):
        return None
    ojclient.set_sleep(no_sleep)
    try:
        with TestClient(create_app(oj_settings(tmp_path, static_dir))) as c:
            teacher, alice = login(c, "teacher"), login(c, "alice")
            run(c)
            e = oj_exam(c, teacher)
            assert e["course"] == "" and e["question_count"] == 5 and e["external"]["package_ready"]
            e = c.post(f"/api/exams/{e['id']}/open", headers=teacher).json()
            a = start(c, alice, e["id"], e["start_code"]).json()
            qs = {q["title"]: q for q in a["questions"]}
            assert qs["Report"]["max_files"] == 1
            assert [x["key"] for x in qs["Reverse a list"]["languages"]] == ["CPP14", "PY3"]
            qid = {k: v["id"] for k, v in qs.items()}
            files = []
            for name in ("a.pdf", "b.pdf"):
                r = c.post(f"/api/student/attempts/{a['attempt_id']}/files?question_id={qid['Report']}",
                           files={"file": (name, b"%PDF-" + name.encode(), "application/pdf")}, headers=alice)
                files.append(r.json()["id"])

            def put(ans, seq):
                return c.put(f"/api/student/attempts/{a['attempt_id']}/answers",
                             json={"answers": ans, "client_seq": seq}, headers=alice)
            assert put({qid["Report"]: files}, 1).status_code == 422          # exactly one file
            ans = {qid["Hash maps"]: 1, qid["Fill"]: ["hash table"], qid["Explain"]: "x",
                   qid["Report"]: [files[1]], qid["Reverse a list"]: {"language": "PY3", "source": "  "}}
            assert put(ans, 2).status_code == 200
            c.post(f"/api/student/attempts/{a['attempt_id']}/submit", json={"answers": ans, "client_seq": 3},
                   headers=alice)
            run(c)
            body = real.bodies[-1]
            by = {x["question_id"]: x for x in body["answers"]}
            assert by["p000002"]["choice"] == {"selected": [2]}
            assert by["p000003"]["fill_blank"] == {"answers": ["hash table", ""]}
            assert len(by["p000005"]["file_upload"]["upload_ids"]) == 1 and list(real.uploads.values()) == ["b.pdf"]
            assert "p000001" not in by                     # blank code: no submission (DKU OJ refuses it)
            assert body["integrity_report_url"].startswith("https://exam.test/")
            assert deliveries(c)[a["attempt_id"]][0] == "sent"

            # results: the next poll asks from the server_time DKU OJ sent
            real.results_rows = [{"username": "alice", "released": True, "total": 16, "total_points": 66,
                                  "updated_at": iso(real.now + timedelta(hours=3)),
                                  "questions": [{"question_id": "p000002", "points": 10, "feedback_md": ""},
                                                {"question_id": "p000003", "points": 6, "feedback_md": ""}]}]
            run(c, now=real.now + timedelta(hours=3))
            assert c.get("/api/student/grades", headers=alice).json()[0]["score"] == 16
            real.results_rows = [{"username": "alice", "released": False,
                                  "updated_at": iso(real.now + timedelta(hours=4))}]
            run(c, now=real.now + timedelta(hours=4))
            assert real.results_since[-1] == iso(real.now + timedelta(hours=3))
            assert c.get(f"/api/student/grades/{e['id']}", headers=alice).status_code == 403

            # removed: only in the incremental poll; editors keep the exam
            real.removed = True
            run(c)
            assert oj_exam(c, teacher)["external"]["removed"] is True

    finally:
        ojclient.set_transport_factory(None)
        ojclient.set_sleep(None)


def test_dkuoj_refusals_are_dead_not_retried(tmp_path, static_dir, monkeypatch):
    real = RealShapeOJ()
    real.package_status = 422
    monkeypatch.setattr(ojsync, "BACKGROUND", False)
    monkeypatch.setenv("DKUOJ_URL", OJ)
    monkeypatch.setenv("DKUOJ_TOKEN", TOKEN)
    ojclient.set_transport_factory(lambda base: httpx.MockTransport(real.handle))
    try:
        with TestClient(create_app(oj_settings(tmp_path, static_dir))) as c:
            teacher = login(c, "teacher")
            run(c)
            n = real.calls.count("GET exams/fpexam/package")
            assert n == 1 and oj_exam(c, teacher)["question_count"] == 0
            run(c, now=utcnow() + timedelta(minutes=10))       # 422 for this version: not asked again
            assert real.calls.count("GET exams/fpexam/package") == 1
            # a new version in the listing is fetched again
            real.package_status = 200
            original = real_listing
            monkeypatch.setitem(REAL_PACKAGE, "package_version", "1111111111111111")
            monkeypatch.setitem(globals(), "real_listing", lambda now, removed=False: {
                **original(now, removed), "package_version": "1111111111111111",
                "updated_at": iso(now + timedelta(seconds=9))})
            run(c, now=utcnow() + timedelta(minutes=20))
            assert oj_exam(c, teacher)["question_count"] == 5
            e = oj_exam(c, teacher)
            e = c.post(f"/api/exams/{e['id']}/open", headers=teacher).json()
            for status, who in ((413, "alice"), (401, "carol")):
                real.submit_status = status
                h = login(c, who)
                a = start(c, h, e["id"], e["start_code"]).json()
                c.post(f"/api/student/attempts/{a['attempt_id']}/submit", json={"answers": {}, "client_seq": 1},
                       headers=h)
                run(c)
                assert deliveries(c)[a["attempt_id"]][:2] == ("dead", 1)
                tries_before = len(real.bodies)
                run(c, now=utcnow() + timedelta(hours=1))
                assert len(real.bodies) == tries_before          # dead: never retried
            res = c.get(f"/api/exams/{e['id']}/results", headers=teacher).json()
            assert {x["external_delivery"]["status"] for x in res["attempts"]} == {"dead"}
            assert all(any(ev["type"] == "external_delivery_failed" for ev in x["events"]) for x in res["attempts"])
    finally:
        ojclient.set_transport_factory(None)
        ojclient.set_sleep(None)
