"""2026-10-23 (exam tools): calculator / open book / websites settings, open-book
materials, the CSP for the websites pane and the pdf.js worker, and the column migration."""
import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from .conftest import SAMPLE_EXAM, login

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
TOOLS = {"calculator": "graphing", "open_book": True, "websites": ["docs.python.org", "en.cppreference.com"]}


def create(client, teacher, **extra):
    r = client.post("/api/exams", json={**SAMPLE_EXAM, **extra}, headers=teacher)
    assert r.status_code == 201, r.text
    return r.json()


def open_exam(client, teacher, eid):
    return client.post(f"/api/exams/{eid}/open", headers=teacher).json()


def upload(client, h, eid, name, data, ctype="application/octet-stream"):
    return client.post(f"/api/student/exams/{eid}/materials", headers=h, files={"file": (name, data, ctype)})


# ---------------------------------------------------------------- settings
def test_tools_default_off(client, teacher):
    e = create(client, teacher)
    assert (e["calculator"], e["open_book"], e["websites"]) == ("off", False, [])


def test_tools_saved_and_kept_on_put(client, teacher):
    e = create(client, teacher, **TOOLS)
    assert e["calculator"] == "graphing" and e["open_book"] is True
    assert e["websites"] == ["docs.python.org", "en.cppreference.com"]
    r = client.put(f"/api/exams/{e['id']}", json={**SAMPLE_EXAM, "title": "T2"}, headers=teacher)
    assert r.status_code == 200
    assert r.json()["calculator"] == "graphing" and r.json()["websites"] == TOOLS["websites"]
    r = client.put(f"/api/exams/{e['id']}", json={**SAMPLE_EXAM, "calculator": "off", "websites": []},
                   headers=teacher).json()
    assert r["calculator"] == "off" and r["websites"] == [] and r["open_book"] is True


@pytest.mark.parametrize("given,want", [
    (["HTTPS://Docs.Python.org/"], ["docs.python.org"]),
    (["docs.python.org", "docs.python.org", " ", "www.Wolfram.com"], ["docs.python.org", "www.wolfram.com"]),
])
def test_websites_normalised(client, teacher, given, want):
    assert create(client, teacher, websites=given)["websites"] == want


@pytest.mark.parametrize("bad", ["docs.python.org/3/library", "*.com", "localhost", "ftp://x.org",
                                 "10.0.0.1", "python.org:8080", "a b.com", "user@host.com"])
def test_websites_refused(client, teacher, bad):
    r = client.post("/api/exams", json={**SAMPLE_EXAM, "websites": [bad]}, headers=teacher)
    assert r.status_code == 422, bad


def test_websites_at_most_20_and_calculator_values(client, teacher):
    many = [f"s{i}.example.com" for i in range(21)]
    assert client.post("/api/exams", json={**SAMPLE_EXAM, "websites": many}, headers=teacher).status_code == 422
    assert len(create(client, teacher, websites=many[:20])["websites"]) == 20
    assert client.post("/api/exams", json={**SAMPLE_EXAM, "calculator": "casio"}, headers=teacher).status_code == 422


def test_website_exam_has_no_tools(client, teacher):
    body = {**SAMPLE_EXAM, "questions": [], "mode": "external", "external_url": "https://canvas.dku.edu/q",
            **TOOLS}
    r = client.post("/api/exams", json=body, headers=teacher)
    assert r.status_code == 201, r.text
    e = r.json()
    assert (e["calculator"], e["open_book"], e["websites"]) == ("off", False, [])


def test_tools_reach_the_student(client, teacher, alice):
    e = open_exam(client, teacher, create(client, teacher, **TOOLS)["id"])
    listed = next(x for x in client.get("/api/student/exams", headers=alice).json() if x["id"] == e["id"])
    assert listed["tools"] == TOOLS
    a = client.post(f"/api/student/exams/{e['id']}/start", json={"start_code": e["start_code"]},
                    headers=alice).json()
    assert a["tools"] == TOOLS and a["allowed_apps"] == []
    g = client.get(f"/api/student/attempts/{a['attempt_id']}", headers=alice).json()
    assert g["tools"] == TOOLS


def test_offline_package_carries_tools():
    from app.offline import package_plaintext
    exam = {"id": "e_1", "title": "T", "duration_min": 5, "questions": [], "allowed_apps": ["excel"], **TOOLS}
    plain = package_plaintext(exam)
    # 2026-10-23 (review 2): plus "the materials are not in here"
    assert plain["tools"] == {**TOOLS, "materials_need_connection": True} and plain["allowed_apps"] == []
    # a cache entry from before 2026-10-23 has no tool fields: all off
    old = package_plaintext({"id": "e_1", "title": "T", "duration_min": 5, "questions": []})
    assert old["tools"] == {"calculator": "off", "open_book": False, "websites": [],
                            "materials_need_connection": False}


def test_old_database_gets_the_tool_columns(tmp_path):
    """An exams table from before 2026-10-23 gets the new columns with their defaults."""
    from app.config import Settings
    from app.db import Database

    url = f"sqlite+aiosqlite:///{tmp_path / 'old.db'}"

    async def go():
        db = Database(Settings(database_url=url))
        await db.create_all()
        await db.dispose()
        eng = create_async_engine(url)
        async with eng.begin() as conn:
            await conn.execute(text("DROP TABLE exam_materials"))
            for col in ("calculator", "open_book", "websites"):
                await conn.execute(text(f"ALTER TABLE exams DROP COLUMN {col}"))
            await conn.execute(text("INSERT INTO users (id, username, name, role, password_hash, created_at) "
                                    "VALUES (1, 't', 'T', 'teacher', 'x', '2026-01-01 00:00:00')"))
            await conn.execute(text("INSERT INTO exams (id, teacher_id, title, duration_min, status, questions, "
                                    "question_count, total_points, created_at, updated_at) VALUES "
                                    "('e_old', 1, 'Old', 10, 'draft', '[]', 0, 0, '2026-01-01 00:00:00', "
                                    "'2026-01-01 00:00:00')"))
        await eng.dispose()
        db = Database(Settings(database_url=url))
        await db.create_all()
        await db.dispose()
        eng = create_async_engine(url)
        async with eng.connect() as conn:
            row = (await conn.execute(text("SELECT calculator, open_book, websites FROM exams"))).one()
            tables = (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).scalars().all()
        await eng.dispose()
        return row, tables

    row, tables = asyncio.run(go())
    assert row[0] == "off" and not row[1] and row[2] in ("[]", [])
    assert "exam_materials" in tables


# ---------------------------------------------------------------- materials
@pytest.fixture
def book_exam(client, teacher):
    return open_exam(client, teacher, create(client, teacher, open_book=True)["id"])


def test_materials_upload_list_delete(client, alice, book_exam):
    eid = book_exam["id"]
    info = client.get(f"/api/student/exams/{eid}/materials", headers=alice).json()
    assert info["open_book"] is True and info["can_change"] is True and info["files"] == []
    assert info["max_files"] == 5 and info["max_bytes"] == 20 * 1024 * 1024
    r = upload(client, alice, eid, "notes/../Chapter 1.pdf", PDF)
    assert r.status_code == 201, r.text
    f = r.json()
    assert f["name"] == "Chapter 1.pdf" and f["kind"] == "pdf" and f["media_type"] == "application/pdf"
    assert f["size"] == len(PDF)
    assert upload(client, alice, eid, "photo.jpeg", JPG).json()["name"] == "photo.jpeg"
    # a PNG named .pdf is stored as what it is
    assert upload(client, alice, eid, "scan.pdf", PNG).json()["name"] == "scan.pdf.png"
    assert upload(client, alice, eid, "Cheat sheet.md", "# Ω formulas\n".encode()).json()["kind"] == "md"
    info = client.get(f"/api/student/exams/{eid}/materials", headers=alice).json()
    assert [x["name"] for x in info["files"]][0] == "Chapter 1.pdf" and len(info["files"]) == 4
    assert info["used_bytes"] == sum(x["size"] for x in info["files"])
    assert client.delete(f"/api/student/exams/{eid}/materials/{f['id']}", headers=alice).status_code == 204
    assert client.delete(f"/api/student/exams/{eid}/materials/{f['id']}", headers=alice).status_code == 404
    assert len(client.get(f"/api/student/exams/{eid}/materials", headers=alice).json()["files"]) == 3


@pytest.mark.parametrize("name,data", [
    ("virus.exe", b"MZ\x90\x00" + b"\x00" * 20),
    ("pic.svg", b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"),
    ("page.html", b"<html><script>alert(1)</script></html>"),
    ("fake.txt", b"\x00\x01\x02binary"),
    ("latin.txt", "caf\xe9".encode("latin-1")),
    ("empty.pdf", b""),
])
def test_materials_type_checked_by_bytes(client, alice, book_exam, name, data):
    assert upload(client, alice, book_exam["id"], name, data).status_code == 422


def test_materials_limits(client, alice, book_exam, monkeypatch):
    from app import materials
    eid = book_exam["id"]
    for i in range(5):
        assert upload(client, alice, eid, f"n{i}.txt", b"hello").status_code == 201
    r = upload(client, alice, eid, "n5.txt", b"hello")
    assert r.status_code == 409 and "at most 5" in r.json()["detail"]
    for f in client.get(f"/api/student/exams/{eid}/materials", headers=alice).json()["files"]:
        client.delete(f"/api/student/exams/{eid}/materials/{f['id']}", headers=alice)
    monkeypatch.setattr(materials, "MAX_BYTES", 100)
    assert upload(client, alice, eid, "a.txt", b"x" * 60).status_code == 201
    r = upload(client, alice, eid, "b.txt", b"x" * 60)
    assert r.status_code == 413 and "20 MB" in r.json()["detail"]
    assert upload(client, alice, eid, "c.txt", b"x" * 101).status_code == 413


def test_materials_only_for_open_book_and_before_start(client, teacher, alice, book_exam):
    closed_book = open_exam(client, teacher, create(client, teacher)["id"])
    r = upload(client, alice, closed_book["id"], "a.txt", b"hi")
    assert r.status_code == 409 and r.json()["detail"] == "This exam is not open book"
    eid = book_exam["id"]
    fid = upload(client, alice, eid, "a.txt", b"hi").json()["id"]
    client.post(f"/api/student/exams/{eid}/start", json={"start_code": book_exam["start_code"]}, headers=alice)
    info = client.get(f"/api/student/exams/{eid}/materials", headers=alice).json()
    assert info["can_change"] is False and "started" in info["reason"]
    assert upload(client, alice, eid, "b.txt", b"hi").status_code == 409
    assert client.delete(f"/api/student/exams/{eid}/materials/{fid}", headers=alice).status_code == 409


def test_materials_during_the_attempt(client, teacher, alice, book_exam):
    eid = book_exam["id"]
    pdf = upload(client, alice, eid, "book.pdf", PDF).json()
    txt = upload(client, alice, eid, "notes.txt", b"<script>alert(1)</script>").json()
    a = client.post(f"/api/student/exams/{eid}/start", json={"start_code": book_exam["start_code"]},
                    headers=alice).json()
    aid = a["attempt_id"]
    files = client.get(f"/api/student/attempts/{aid}/materials", headers=alice).json()["files"]
    assert [f["id"] for f in files] == [pdf["id"], txt["id"]]
    r = client.get(f"/api/student/attempts/{aid}/materials/{pdf['id']}", headers=alice)
    assert r.status_code == 200 and r.content == PDF
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith("inline;")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"]
    # 2026-10-23 (review): the attempt's browser may keep it; never a shared cache
    assert r.headers["cache-control"] == "private, max-age=3600"
    assert r.headers["content-length"] == str(len(PDF))
    r = client.get(f"/api/student/attempts/{aid}/materials/{txt['id']}", headers=alice)
    assert r.headers["content-type"].startswith("text/plain")          # never text/html
    # another student can't read them
    bob = login(client, "bob")
    assert client.get(f"/api/student/attempts/{aid}/materials/{pdf['id']}", headers=bob).status_code == 404
    assert client.get(f"/api/student/attempts/{aid}/materials", headers=bob).status_code == 404
    # after the hand-in: gone
    client.post(f"/api/student/attempts/{aid}/submit", json={"answers": {}, "client_seq": 1}, headers=alice)
    assert client.get(f"/api/student/attempts/{aid}/materials/{pdf['id']}", headers=alice).status_code == 409


def test_materials_class_members_only(client, teacher, book_exam):
    outsider = login(client, "carol")
    # carol is a demo member: take her out of the exam's class
    client.delete(f"/api/classes/{book_exam['class_id']}/members/carol", headers=teacher)
    assert upload(client, outsider, book_exam["id"], "a.txt", b"hi").status_code == 403
    assert client.get(f"/api/student/exams/{book_exam['id']}/materials", headers=outsider).status_code == 403


def test_teacher_sees_materials_read_only(client, teacher, alice, book_exam):
    eid = book_exam["id"]
    f = upload(client, alice, eid, "book.pdf", PDF).json()
    lst = client.get(f"/api/exams/{eid}/materials", headers=teacher).json()
    assert lst == [{"username": "alice", "name": lst[0]["name"], "files": [f]}]
    a = client.post(f"/api/student/exams/{eid}/start", json={"start_code": book_exam["start_code"]},
                    headers=alice).json()
    one = client.get(f"/api/exams/{eid}/attempts/{a['attempt_id']}/materials", headers=teacher).json()
    assert one == {"files": [f]}
    r = client.get(f"/api/exams/{eid}/materials/{f['id']}", headers=teacher)
    assert r.status_code == 200 and r.content == PDF and r.headers["content-type"] == "application/pdf"
    assert client.get(f"/api/exams/{eid}/materials", headers=alice).status_code == 403
    # deleting the exam removes the files
    client.post(f"/api/exams/{eid}/close", headers=teacher)
    assert client.delete(f"/api/exams/{eid}", headers=teacher).status_code == 204


# ---------------------------------------------------------------- http guard
def test_csp_allows_the_websites_pane_and_pdf_worker(client):
    csp = client.get("/teacher").headers["content-security-policy"]
    assert "frame-src 'self' https:" in csp and "worker-src 'self' blob:" in csp
    # phase 4: 'wasm-unsafe-eval' (Pyodide's WebAssembly) but never JavaScript eval
    assert "script-src 'self' 'wasm-unsafe-eval';" in csp and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    assert "'unsafe-eval'" not in csp and "'unsafe-inline'" not in csp.split("style-src")[0] and "http:" not in csp.replace("https:", "")


def test_material_upload_size_limit():
    from app.http_guard import DEFAULT_LIMIT, MB, limit_for
    assert limit_for("/api/student/exams/e_1/materials") == 21 * MB
    assert limit_for("/api/student/exams/e_1/materials/m_1") == DEFAULT_LIMIT


def test_module_worker_served_as_javascript(client, static_dir):
    (static_dir / "assets" / "pdf.worker.min-x.mjs").write_text("self.onmessage = () => {};")
    r = client.get("/assets/pdf.worker.min-x.mjs")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")


def test_page_meta_csp_matches_the_header():
    """web/index.html carries the same policy as a <meta> (audit W-14): the websites pane
    and the pdf.js worker need the same frame-src / worker-src there."""
    import os
    import re
    from app.http_guard import CSP
    html = os.path.join(os.path.dirname(__file__), "..", "..", "web", "index.html")
    if not os.path.isfile(html):
        pytest.skip("web sources not next to the server")
    meta = re.search(r'http-equiv="Content-Security-Policy"\s+content="([^"]+)"', open(html).read()).group(1)
    assert meta == CSP.replace("; frame-ancestors 'none'", "")
