"""2026-10-23 (review): open-book materials are streamed in pieces with an ETag, the upload
body is read only after every check, the 20 MB limit, and the upload / exam-start race."""
import hashlib
import os
from datetime import timedelta

from sqlalchemy import func, select, text

from .conftest import login
from .test_exam_tools_2026_10_23 import PDF, create, open_exam, upload


def book(client, teacher):
    return open_exam(client, teacher, create(client, teacher, open_book=True)["id"])


def start(client, h, exam):
    a = client.post(f"/api/student/exams/{exam['id']}/start", json={"start_code": exam["start_code"]}, headers=h)
    assert a.status_code in (200, 201), a.text
    return a.json()["attempt_id"]


def raw_post(client, h, eid, body: bytes, ctype="multipart/form-data; boundary=xx", **extra):
    return client.post(f"/api/student/exams/{eid}/materials", content=body,
                       headers={**h, "content-type": ctype, **extra})


# ---------------------------------------------------------------- download
def test_download_streams_in_pieces(client, teacher, alice, monkeypatch):
    from app import materials
    monkeypatch.setattr(materials, "CHUNK", 7)          # many pieces, one odd-sized last one
    exam = book(client, teacher)
    data = b"%PDF-1.4\n" + os.urandom(1000) + b"\n%%EOF\n"
    f = upload(client, alice, exam["id"], "big.pdf", data).json()
    aid = start(client, alice, exam)
    r = client.get(f"/api/student/attempts/{aid}/materials/{f['id']}", headers=alice)
    assert r.status_code == 200 and r.content == data
    assert r.headers["content-length"] == str(len(data))
    r = client.get(f"/api/exams/{exam['id']}/materials/{f['id']}", headers=teacher)
    assert r.status_code == 200 and r.content == data


def test_substr_and_length_on_blob_bytes(client):
    """substr / length count bytes of a BLOB (not characters), with NULs and invalid UTF-8."""
    from app.db import get_database
    from app.models import ExamMaterial

    blob = bytes(range(256)) * 3

    async def go():
        async with get_database().sessionmaker() as s:
            q = select(func.length(ExamMaterial.data)).where(ExamMaterial.id == "x")
            assert (await s.execute(q)).scalar_one_or_none() is None
            got = (await s.execute(select(func.substr(text(":b"), 255, 10), func.length(text(":b"))),
                                   {"b": blob})).one()
            return bytes(got[0]), got[1]

    part, n = client.portal.call(go)
    assert part == blob[254:264] and n == len(blob)


def test_etag_and_not_modified(client, teacher, alice):
    exam = book(client, teacher)
    f = upload(client, alice, exam["id"], "book.pdf", PDF).json()
    aid = start(client, alice, exam)
    url = f"/api/student/attempts/{aid}/materials/{f['id']}"
    r = client.get(url, headers=alice)
    etag = r.headers["etag"]
    assert etag == f'"{hashlib.sha256(PDF).hexdigest()}"'
    r = client.get(url, headers={**alice, "If-None-Match": etag})
    assert r.status_code == 304 and r.content == b"" and r.headers["etag"] == etag
    assert client.get(url, headers={**alice, "If-None-Match": 'W/"other", ' + etag}).status_code == 304
    assert client.get(url, headers={**alice, "If-None-Match": '"other"'}).status_code == 200
    # the checks still come first: another student gets 404, not 304
    bob = login(client, "bob")
    assert client.get(url, headers={**bob, "If-None-Match": etag}).status_code == 404
    t = client.get(f"/api/exams/{exam['id']}/materials/{f['id']}", headers={**teacher, "If-None-Match": etag})
    assert t.status_code == 304


def test_row_without_sha_uses_its_id(client, teacher, alice):
    from app.db import get_database
    exam = book(client, teacher)
    f = upload(client, alice, exam["id"], "book.pdf", PDF).json()

    async def clear():
        async with get_database().sessionmaker() as s:
            await s.execute(text("UPDATE exam_materials SET sha256 = ''"))
            await s.commit()

    client.portal.call(clear)
    aid = start(client, alice, exam)
    r = client.get(f"/api/student/attempts/{aid}/materials/{f['id']}", headers=alice)
    assert r.status_code == 200 and r.headers["etag"] == f'"{f["id"]}"' and r.content == PDF


# ---------------------------------------------------------------- upload: checks before the body
def test_upload_checks_run_before_the_body_is_read(client, teacher, alice):
    exam = book(client, teacher)
    eid = exam["id"]
    junk = b"not multipart at all"
    # no login: 401, the body is never parsed (it isn't even multipart)
    assert raw_post(client, {}, eid, junk).status_code == 401
    # a closed-book exam answers 409 whatever the declared size
    closed = open_exam(client, teacher, create(client, teacher)["id"])
    r = raw_post(client, alice, closed["id"], junk)
    assert r.status_code == 409 and "not open book" in r.json()["detail"]
    # unknown exam
    assert raw_post(client, alice, "e_nope", junk).status_code == 404


def test_multipart_never_parsed_before_the_checks(client, teacher, alice, monkeypatch):
    from starlette.formparsers import MultiPartParser
    closed = open_exam(client, teacher, create(client, teacher)["id"])

    async def boom(self):
        raise AssertionError("the body was parsed before the checks")

    monkeypatch.setattr(MultiPartParser, "parse", boom)
    assert upload(client, {}, closed["id"], "a.txt", b"hi").status_code == 401
    assert upload(client, alice, closed["id"], "a.txt", b"hi").status_code == 409
    assert client.post("/api/uploads", files={"file": ("a.png", b"x", "image/png")},
                       headers=alice).status_code == 403
    assert client.post(f"/api/exams/{closed['id']}/scans", files=[("files", ("s.pdf", b"%PDF-", "application/pdf"))],
                       headers=alice).status_code == 403


def test_upload_refused_by_declared_size(client, teacher, alice, monkeypatch):
    from app import materials
    monkeypatch.setattr(materials, "MAX_BYTES", 1000)
    eid = book(client, teacher)["id"]
    # more than the room left + multipart overhead: refused before reading
    big = b"x" * (1000 + materials.MULTIPART_OVERHEAD + 1)
    r = raw_post(client, alice, eid, big)
    assert r.status_code == 413 and "20 MB" in r.json()["detail"]
    # no Content-Length at all (chunked): refused
    def chunks():
        yield b"--xx\r\n"
    r = client.post(f"/api/student/exams/{eid}/materials", content=chunks(),
                    headers={**alice, "content-type": "multipart/form-data; boundary=xx"})
    assert r.status_code == 413
    # the room shrinks with what is already there
    assert upload(client, alice, eid, "a.txt", b"x" * 900).status_code == 201
    r = raw_post(client, alice, eid, b"y" * (100 + materials.MULTIPART_OVERHEAD + 1))
    assert r.status_code == 413
    # not multipart
    assert raw_post(client, alice, eid, b"{}", ctype="application/json").status_code == 422
    # multipart without a file part
    body = b'--xx\r\nContent-Disposition: form-data; name="other"\r\n\r\nhi\r\n--xx--\r\n'
    assert raw_post(client, alice, eid, body).status_code == 422


def test_upload_body_read_with_a_hard_cap(client, teacher, alice, monkeypatch):
    """A Content-Length that understates the body can't get more than the cap read."""
    from app import materials, uploads
    monkeypatch.setattr(materials, "MAX_BYTES", 100)
    monkeypatch.setattr(materials, "MULTIPART_OVERHEAD", 50)
    eid = book(client, teacher)["id"]
    seen = {}
    real = uploads.read_capped_form

    async def spy(request, cap, too_big, max_files=1):
        seen["cap"] = cap
        return await real(request, cap, too_big, max_files)

    monkeypatch.setattr(materials, "read_capped_form", spy)
    r = upload(client, alice, eid, "a.txt", b"x" * 400)
    assert r.status_code == 413 and seen["cap"] == 150


def test_room_full_answers_before_reading(client, teacher, alice, monkeypatch):
    from app import materials
    monkeypatch.setattr(materials, "MAX_BYTES", 10)
    eid = book(client, teacher)["id"]
    assert upload(client, alice, eid, "a.txt", b"x" * 10).status_code == 201
    r = raw_post(client, alice, eid, b"junk")
    assert r.status_code == 413 and "20 MB" in r.json()["detail"]


def test_sha256_stored_on_upload(client, teacher, alice):
    from app.db import get_database
    eid = book(client, teacher)["id"]
    f = upload(client, alice, eid, "book.pdf", PDF).json()

    async def sha():
        async with get_database().sessionmaker() as s:
            return (await s.execute(text("SELECT sha256 FROM exam_materials WHERE id = :i"), {"i": f["id"]})).scalar_one()

    assert client.portal.call(sha) == hashlib.sha256(PDF).hexdigest()


# ---------------------------------------------------------------- upload racing the start
def test_upload_racing_the_exam_start_is_refused(client, teacher, alice, monkeypatch):
    """The attempt appears while the body is arriving: the file is not kept."""
    from app import materials
    exam = book(client, teacher)
    eid = exam["id"]
    real_read = materials.read_capped_form

    async def read_then_start(request, cap, too_big, max_files=1):
        form = await real_read(request, cap, too_big, max_files)
        # the student starts the exam (on another device) meanwhile
        from app.db import get_database
        from app.models import Attempt
        from app.timeutil import utcnow
        async with get_database().sessionmaker() as s:
            uid = (await s.execute(text("SELECT id FROM users WHERE username = 'alice'"))).scalar_one()
            s.add(Attempt(id="a_race", exam_id=eid, student_id=uid, status="in_progress",
                          started_at=utcnow(), deadline_at=utcnow() + timedelta(hours=1)))
            await s.commit()
        return form

    monkeypatch.setattr(materials, "read_capped_form", read_then_start)
    r = upload(client, alice, eid, "late.txt", b"too late")
    assert r.status_code == 409 and "started" in r.json()["detail"]
    monkeypatch.setattr(materials, "read_capped_form", real_read)
    info = client.get(f"/api/student/exams/{eid}/materials", headers=alice).json()
    assert info["files"] == [] and info["can_change"] is False


# ---------------------------------------------------------------- question images (/api/uploads)
def test_image_upload_needs_a_teacher_before_the_body(client, alice):
    assert client.post("/api/uploads", content=b"junk", headers={"content-type": "text/plain"}).status_code == 401
    assert client.post("/api/uploads", content=b"junk", headers={**alice, "content-type": "text/plain"}).status_code == 403
