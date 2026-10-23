"""2026-10-23 (review 2): coding questions are never printed or scanned, wildcard websites
checked against the Public Suffix List, open-book upload slots / exam cap / retention,
offline packages say materials need the connection, materials gone with open book, and
multipart edge cases (too many scan files, a form closed on 422)."""
import asyncio
import hashlib
from datetime import timedelta

import pytest
from sqlalchemy import text

from .conftest import SAMPLE_EXAM, login
from .test_code_questions import CODE_Q
from .test_exam_tools_2026_10_23 import PDF, create, open_exam, upload


def _sql(client, sql, **params):
    from app.db import get_database

    async def go():
        async with get_database().sessionmaker() as s:
            r = await s.execute(text(sql), params)
            await s.commit()
            return r

    return client.portal.call(go)


def _count(client, sql, **params):
    from app.db import get_database

    async def go():
        async with get_database().sessionmaker() as s:
            return (await s.execute(text(sql), params)).scalar_one()

    return client.portal.call(go)


def _invalidate(client, eid):
    from app.services import invalidate_exam
    from app.store import get_store

    async def go():
        await invalidate_exam(get_store(), eid)

    client.portal.call(go)


# ---------------------------------------------------------------- M1: coding questions on paper
def test_print_layout_never_skips_a_question():
    from app.scan.print import NotPrintable, plan
    qs = [{"id": "q_1", "type": "mcq", "prompt": "A?", "options": ["a", "b"], "points": 1},
          {"id": "q_2", "type": "code", "prompt": "Write it", "points": 5}]
    with pytest.raises(NotPrintable):
        plan(qs)
    with pytest.raises(ValueError):
        plan([{"id": "q_9", "type": "drawing", "prompt": "?"}])
    pages = plan(qs[:1])
    assert [b["number"] for b in pages[0].boxes] == [1]


def test_computer_exam_with_code_cannot_be_printed_or_scanned(client, teacher):
    from app.routes_scan import CODE_NOT_PRINTABLE
    e = create(client, teacher, questions=[*SAMPLE_EXAM["questions"], CODE_Q])
    eid = e["id"]
    info = client.get(f"/api/exams/{eid}/print", headers=teacher).json()
    assert info["printable"] is False and info["not_printable_reason"] == CODE_NOT_PRINTABLE
    assert info["pages_per_student"] == 0
    assert [q["printable"] for q in info["questions"]] == [True, True, True, False]
    for r in (client.get(f"/api/exams/{eid}/print.pdf?blank=1", headers=teacher),
              client.put(f"/api/exams/{eid}/print-settings", json={"answer_lines": {}}, headers=teacher),
              client.post(f"/api/exams/{eid}/scans", headers=teacher,
                          files=[("files", ("s.pdf", b"%PDF-1.4", "application/pdf"))]),
              client.post(f"/api/exams/{eid}/scans/pages/1/assign", headers=teacher,
                          json={"student_id": 1, "page_no": 1})):
        assert r.status_code == 409, r.text
        assert r.json()["detail"] == "Coding questions can't be printed. Remove them or print without them."
    assert _count(client, "SELECT count(*) FROM attempts WHERE exam_id = :e", e=eid) == 0


def test_code_added_after_printing_blocks_the_scans(client, teacher):
    e = create(client, teacher)
    eid = e["id"]
    r = client.get(f"/api/exams/{eid}/print.pdf?blank=1", headers=teacher)
    assert r.status_code == 200
    assert client.get(f"/api/exams/{eid}/print", headers=teacher).json()["printable"] is True
    r = client.put(f"/api/exams/{eid}", headers=teacher,
                   json={**SAMPLE_EXAM, "questions": [*e["questions"], CODE_Q]})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/exams/{eid}/scans", headers=teacher,
                    files=[("files", ("s.pdf", b"%PDF-1.4", "application/pdf"))])
    assert r.status_code == 409 and "Coding questions" in r.json()["detail"]


def test_merge_refuses_an_exam_with_code(client, teacher):
    """The last guard: a scan batch never makes a paper attempt with an empty coding answer."""
    from fastapi import HTTPException

    from app.db import get_database
    from app.models import Exam
    from app.routes_scan import merge_pages
    eid = create(client, teacher, questions=[*SAMPLE_EXAM["questions"], CODE_Q])["id"]

    async def go():
        async with get_database().sessionmaker() as s:
            ex = await s.get(Exam, eid)
            with pytest.raises(HTTPException) as err:
                await merge_pages(s, ex, [])
            return err.value.status_code

    assert client.portal.call(go) == 409


# ---------------------------------------------------------------- M2: websites and the PSL
@pytest.mark.parametrize("bad", [
    "*.pythonanywhere.com", "*.r2.dev", "*.hf.space", "*.streamlit.app", "*.supabase.co", "*.ngrok.app",
    "*.deno.dev", "*.googleapis.com", "*.web.core.windows.net", "*.s3.us-east-1.amazonaws.com",
    "*.blogspot.co.uk", "*.me.uk", "*.github.io", "*.com", "*.co.uk", "*.google.com",
    # a plain host that is itself a public suffix
    "github.io", "pythonanywhere.com", "co.uk", "r2.dev",
    # addresses and ports
    "127.0.0.1", "10.0.0.1", "0x7f.1", "[::1]", "::1", "python.org:8080", "*.python.org:443",
    "http://192.168.1.1/", "localhost", "python",
])
def test_websites_refused(bad):
    from app.schemas import normalize_website
    with pytest.raises(ValueError):
        normalize_website(bad)


@pytest.mark.parametrize("given,want", [
    ("*.python.org", "*.python.org"),
    ("docs.python.org", "docs.python.org"),
    ("alice.github.io", "alice.github.io"),
    ("*.alice.github.io", "*.alice.github.io"),
    ("пример.рф", "xn--e1afmkfd.xn--p1ai"),
    ("*.Пример.РФ", "*.xn--e1afmkfd.xn--p1ai"),
    ("https://münchen.de/", "xn--mnchen-3ya.de"),
    ("xn--e1afmkfd.xn--p1ai", "xn--e1afmkfd.xn--p1ai"),
    ("*.dku.edu.cn", "*.dku.edu.cn"),
])
def test_websites_accepted(given, want):
    from app.schemas import normalize_website
    assert normalize_website(given) == want


def test_public_suffix_list_is_bundled_with_the_private_section():
    from app.schemas import is_public_suffix, is_shared_suffix
    assert is_public_suffix("github.io") and is_public_suffix("hf.space") and is_public_suffix("com")
    assert not is_public_suffix("python.org") and not is_shared_suffix("python.org")
    assert is_shared_suffix("blogspot.co.uk")


def test_idn_website_through_the_api(client, teacher):
    r = client.post("/api/exams", json={**SAMPLE_EXAM, "websites": ["пример.рф", "*.python.org"]}, headers=teacher)
    assert r.status_code == 201, r.text
    assert r.json()["websites"] == ["xn--e1afmkfd.xn--p1ai", "*.python.org"]
    r = client.post("/api/exams", json={**SAMPLE_EXAM, "websites": ["*.hf.space"]}, headers=teacher)
    assert r.status_code == 422


# ---------------------------------------------------------------- M3: uploads, cap, retention
def book(client, teacher):
    return open_exam(client, teacher, create(client, teacher, open_book=True)["id"])


def test_upload_hashed_in_pieces(client, teacher, alice, monkeypatch):
    from app import materials
    monkeypatch.setattr(materials, "READ_PIECE", 7)
    eid = book(client, teacher)["id"]
    r = upload(client, alice, eid, "book.pdf", PDF)
    assert r.status_code == 201, r.text
    assert r.json()["size"] == len(PDF)
    got = _count(client, "SELECT sha256 FROM exam_materials WHERE id = :i", i=r.json()["id"])
    assert got == hashlib.sha256(PDF).hexdigest()
    data = _count(client, "SELECT data FROM exam_materials WHERE id = :i", i=r.json()["id"])
    assert bytes(data) == PDF


def test_upload_waits_for_a_slot_then_busy(client, teacher, alice, monkeypatch):
    from app import materials
    monkeypatch.setattr(materials, "SLOT_WAIT_S", 0.05)
    eid = book(client, teacher)["id"]

    async def take():
        sem = materials._upload_slots()
        for _ in range(materials.UPLOAD_SLOTS):
            await sem.acquire()

    async def give():
        sem = materials._upload_slots()
        for _ in range(materials.UPLOAD_SLOTS):
            sem.release()

    client.portal.call(take)
    r = upload(client, alice, eid, "a.txt", b"hello")
    assert r.status_code == 429 and r.json()["detail"] == "Busy, try again in a moment"
    client.portal.call(give)
    # the per-student lock was released: the next try goes through
    assert upload(client, alice, eid, "a.txt", b"hello").status_code == 201


def test_exam_total_cap(client, teacher, alice, bob, monkeypatch):
    from app import materials
    monkeypatch.setattr(materials, "EXAM_MAX_BYTES", 1000)
    eid = book(client, teacher)["id"]
    assert upload(client, alice, eid, "a.txt", b"x" * 900).status_code == 201
    r = upload(client, bob, eid, "b.txt", b"y" * 200)
    assert r.status_code == 413 and "400 MB for all students" in r.json()["detail"]
    assert upload(client, bob, eid, "b.txt", b"y" * 100).status_code == 201
    r = upload(client, bob, eid, "c.txt", b"z")       # full: refused before reading
    assert r.status_code == 413 and "400 MB" in r.json()["detail"]


def test_exam_cap_checked_again_after_the_insert(client, teacher, alice, monkeypatch):
    """Two students at once: the second insert finds the exam over the cap and is undone."""
    from app import materials
    eid = book(client, teacher)["id"]
    real = materials._exam_usage
    calls = {"n": 0}

    async def usage(session, exam_id):
        calls["n"] += 1
        return 0 if calls["n"] == 1 else materials.EXAM_MAX_BYTES + 1

    monkeypatch.setattr(materials, "_exam_usage", usage)
    r = upload(client, alice, eid, "a.txt", b"hello")
    assert r.status_code == 413
    monkeypatch.setattr(materials, "_exam_usage", real)
    assert _count(client, "SELECT count(*) FROM exam_materials WHERE exam_id = :e", e=eid) == 0


def test_retention_deletes_materials_30_days_after_close(client, teacher, alice, bob):
    from app import materials
    from app.timeutil import utcnow
    old, recent, live = book(client, teacher), book(client, teacher), book(client, teacher)
    for e in (old, recent, live):
        assert upload(client, alice, e["id"], "a.txt", b"notes").status_code == 201
    for e in (old, recent):
        assert client.post(f"/api/exams/{e['id']}/close", headers=teacher).status_code == 200
    _sql(client, "UPDATE exams SET closed_at = :t WHERE id = :e", t=utcnow() - timedelta(days=31), e=old["id"])
    _sql(client, "UPDATE exams SET closed_at = :t WHERE id = :e", t=utcnow() - timedelta(days=29), e=recent["id"])
    assert client.portal.call(materials.cleanup_old_materials) == 1
    left = {r[0] for r in _sql(client, "SELECT exam_id FROM exam_materials").all()}
    assert left == {recent["id"], live["id"]}
    # an old exam closed before closed_at existed counts from its last change
    _sql(client, "UPDATE exams SET closed_at = NULL, updated_at = :t WHERE id = :e",
         t=utcnow() - timedelta(days=40), e=recent["id"])
    assert client.portal.call(materials.cleanup_old_materials) == 1
    assert client.portal.call(materials.cleanup_old_materials) == 0


def test_retention_runs_from_the_exam_list_at_most_hourly(client, teacher, alice, monkeypatch):
    from app import materials
    from app.timeutil import utcnow
    monkeypatch.setattr(materials, "_last_cleanup", 0.0)
    e = book(client, teacher)
    assert upload(client, alice, e["id"], "a.txt", b"notes").status_code == 201
    client.post(f"/api/exams/{e['id']}/close", headers=teacher)
    _sql(client, "UPDATE exams SET closed_at = :t WHERE id = :e", t=utcnow() - timedelta(days=31), e=e["id"])
    assert client.get("/api/exams", headers=teacher).status_code == 200      # first call: runs
    assert _count(client, "SELECT count(*) FROM exam_materials") == 0
    # within the hour nothing runs again (the student list is a trigger too)
    e2 = book(client, teacher)
    assert upload(client, alice, e2["id"], "b.txt", b"notes").status_code == 201
    client.post(f"/api/exams/{e2['id']}/close", headers=teacher)
    _sql(client, "UPDATE exams SET closed_at = :t WHERE id = :e", t=utcnow() - timedelta(days=31), e=e2["id"])
    assert client.get("/api/student/exams", headers=alice).status_code == 200
    assert _count(client, "SELECT count(*) FROM exam_materials") == 1


def test_retention_failure_never_fails_the_list(client, teacher, monkeypatch):
    from app import materials

    async def boom(now=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(materials, "_last_cleanup", 0.0)
    monkeypatch.setattr(materials, "cleanup_old_materials", boom)
    assert client.get("/api/exams", headers=teacher).status_code == 200


# ---------------------------------------------------------------- L1 / L2
def test_offline_package_says_materials_need_the_connection():
    from app.offline import package_plaintext
    base = {"id": "e_1", "title": "T", "duration_min": 5, "questions": []}
    assert package_plaintext({**base, "open_book": True})["tools"]["materials_need_connection"] is True
    assert package_plaintext(base)["tools"]["materials_need_connection"] is False


def test_attempt_materials_gone_when_open_book_is_off(client, teacher, alice):
    exam = book(client, teacher)
    f = upload(client, alice, exam["id"], "book.pdf", PDF).json()
    a = client.post(f"/api/student/exams/{exam['id']}/start", json={"start_code": exam["start_code"]}, headers=alice)
    aid = a.json()["attempt_id"]
    assert client.get(f"/api/student/attempts/{aid}/materials", headers=alice).status_code == 200
    _sql(client, "UPDATE exams SET open_book = :f WHERE id = :e", f=False, e=exam["id"])
    _invalidate(client, exam["id"])
    assert client.get(f"/api/student/attempts/{aid}/materials", headers=alice).status_code == 404
    assert client.get(f"/api/student/attempts/{aid}/materials/{f['id']}", headers=alice).status_code == 404


# ---------------------------------------------------------------- L3: multipart edge cases
def test_too_many_scan_files_is_the_routes_422(client, teacher, monkeypatch):
    from app import routes_scan
    monkeypatch.setattr(routes_scan, "MAX_FILES", 2)
    eid = create(client, teacher)["id"]
    assert client.get(f"/api/exams/{eid}/print.pdf?blank=1", headers=teacher).status_code == 200
    files = [("files", (f"s{i}.pdf", b"%PDF-1.4", "application/pdf")) for i in range(3)]
    r = client.post(f"/api/exams/{eid}/scans", headers=teacher, files=files)
    assert r.status_code == 422 and r.json()["detail"] == "Upload 1 to 2 files"


def test_image_upload_closes_the_form_on_422(client, teacher, monkeypatch):
    from starlette.datastructures import FormData
    closed = []
    real = FormData.close

    async def spy(self):
        closed.append(True)
        await real(self)

    monkeypatch.setattr(FormData, "close", spy)
    r = client.post("/api/uploads", headers=teacher, files={"other": ("a.png", b"x", "image/png")})
    assert r.status_code == 422 and closed


def test_nginx_materials_limit_matches_the_api():
    from pathlib import Path
    conf = (Path(__file__).resolve().parents[1] / "nginx.conf").read_text()
    line = next(x for x in conf.splitlines() if "client_max_body_size" in x and "open-book" in x)
    assert "21m" in line and "20 MB" in line
