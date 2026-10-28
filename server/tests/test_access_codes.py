import fitz
from sqlalchemy import delete, select

from app.integrations.dkuoj import access_codes
from app.integrations.dkuoj.models import ExamAccessCode, OjRosterEntry
from app.models import Exam

from .test_dkuoj_bridge import KEY, db, run
from .test_dkuoj_bridge_v2 import V1, call, fake, oj  # noqa: F401  (fixtures)

PDF = f"{V1}/exams/{KEY}/access-codes.pdf"
REISSUE = f"{V1}/exams/{KEY}/access-codes/reissue"


def secret(c):
    return c.app.state.settings.secret


def pdf_text(data):
    doc = fitz.open(stream=data, filetype="pdf")
    return doc, "\n".join(p.get_text() for p in doc)


def codes_of(c):
    async def q(s):
        e = (await s.execute(select(Exam).where(Exam.external_key == KEY))).scalar_one()
        sheet = await access_codes.ensure_codes(s, secret(c), e)
        return {r.netid: code for r, code in sheet.rows}
    return db(c, q)


def test_generator_alphabet_length_and_uniqueness():
    seen = {access_codes.generate_code() for _ in range(5000)}
    assert len(seen) > 4990
    assert all(len(x) == 6 and set(x) <= set(access_codes.ALPHABET) for x in seen)
    assert not set("01OIL") & set(access_codes.ALPHABET)

    taken, made = set(), set()
    for _ in range(5000):
        code, h = access_codes._new_code("s", taken)
        assert h not in made
        made.add(h)
    assert len(taken) == 5000


def test_normalisation_is_case_insensitive():
    h = access_codes.code_hmac("s", "k7m2qx")
    assert h == access_codes.code_hmac("s", "K7M-2QX") == access_codes.code_hmac("s", " k7m 2qx ")
    assert h != access_codes.code_hmac("other", "K7M2QX")


def test_pdf_headers_content_and_stable_codes(oj):
    run(oj)
    r = call(oj, "GET", PDF)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == f'attachment; filename="access-codes-{KEY}.pdf"'
    assert r.headers["cache-control"] == "no-store"
    assert r.content.startswith(b"%PDF")
    codes = codes_of(oj)
    assert set(codes) == {"alice", "carol", "ax19", "bl642"}
    assert all(len(c) == 6 for c in codes.values()) and len(set(codes.values())) == 4
    doc, text = pdf_text(r.content)
    assert len(doc) == 2          # class list + slips
    for word in ("Avazbek", "Khamidov", "ax19", codes["ax19"], "Do not share. One code per student.", "UTC"):
        assert word in text
    assert text.index("Chen") < text.index("Khamidov") < text.index("Wang")
    again = call(oj, "GET", PDF)
    assert again.status_code == 200 and codes_of(oj) == codes
    assert codes["alice"] in pdf_text(again.content)[1]

    async def sealed(s):
        return [r.code_sealed for r in (await s.execute(select(ExamAccessCode))).scalars()]
    assert not any(c in " ".join(db(oj, sealed)) for c in codes.values())


def add_students(c, n):
    async def many(s):
        from app.models import User
        e = (await s.execute(select(Exam).where(Exam.external_key == KEY))).scalar_one()
        for i in range(n):
            u = User(username=f"zz{i:02d}", name=f"Pat {i}", role="student", password_hash="x", netid=f"zz{i:02d}")
            s.add(u)
            await s.flush()
            s.add(OjRosterEntry(exam_id=e.id, user_id=u.id, first_name=f"Pat{i:02d}", last_name=f"Okafor{i:02d}"))
        await s.commit()
    db(c, many)


def test_layouts_and_page_counts_for_50_students(oj):
    run(oj)
    add_students(oj, 46)                      # 4 from the sync + 46 = 50
    codes = codes_of(oj)
    assert len(codes) == 50
    both = call(oj, "GET", PDF)
    assert both.status_code == 200
    doc, text = pdf_text(both.content)
    assert len(doc) == 2 + 5                  # 28 rows a page, 12 slips a page
    assert "Page 1 of 7" in text and "Page 7 of 7" in text
    lst = call(oj, "GET", PDF + "?layout=list")
    assert len(pdf_text(lst.content)[0]) == 2
    slips = call(oj, "GET", PDF + "?layout=slips")
    assert len(pdf_text(slips.content)[0]) == 5
    assert len(pdf_text(call(oj, "GET", PDF + "?layout=both").content)[0]) == 7
    for resp in (both, lst, slips):
        assert resp.headers["cache-control"] == "no-store"
        assert resp.headers["content-disposition"] == f'attachment; filename="access-codes-{KEY}.pdf"'
    for name, resp in (("both", both), ("list", lst), ("slips", slips)):
        t = pdf_text(resp.content)[1]
        for netid in ("zz00", "zz45", "ax19"):
            assert netid in t, (name, netid)
        assert "Okafor45" in t and "Pat45" in t and codes["ax19"] in t and codes["zz45"] in t
    list_text = pdf_text(lst.content)[1]
    assert "Present" in list_text and "Access code" in list_text and "Do not share" in list_text
    assert list_text.count("Last name") == 2 and "Class list" in list_text
    assert "Present" not in pdf_text(slips.content)[1] and "CS 101 Midterm" in pdf_text(slips.content)[1]


def test_sixty_students_fit_three_list_pages_and_five_slip_pages(oj):
    run(oj)
    add_students(oj, 56)
    call(oj, "GET", PDF)
    assert len(pdf_text(call(oj, "GET", PDF + "?layout=list").content)[0]) == 3
    assert len(pdf_text(call(oj, "GET", PDF + "?layout=slips").content)[0]) == 5
    assert len(pdf_text(call(oj, "GET", PDF).content)[0]) == 8


def test_list_is_sorted_and_slips_keep_the_order(oj):
    run(oj)
    t = pdf_text(call(oj, "GET", PDF + "?layout=list").content)[1]
    assert t.index("Chen") < t.index("Khamidov") < t.index("Wang")
    s = pdf_text(call(oj, "GET", PDF + "?layout=slips").content)[1]
    assert s.index("Chen") < s.index("Khamidov") < s.index("Wang")


def test_bad_layout_is_refused(oj):
    run(oj)
    for bad in ("grid", "", "LIST", "both;x"):
        assert call(oj, "GET", PDF + f"?layout={bad}").status_code == 422, bad
    assert db(oj, lambda s: s.scalar(select(ExamAccessCode.id).limit(1))) is None


def test_reissue_invalidates_old_code(oj):
    run(oj)
    old = codes_of(oj)
    r = call(oj, "POST", REISSUE, {"netid": "alice"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    new = codes_of(oj)
    assert new["alice"] != old["alice"]
    assert {k: v for k, v in new.items() if k != "alice"} == {k: v for k, v in old.items() if k != "alice"}

    async def find(s):
        e = (await s.execute(select(Exam).where(Exam.external_key == KEY))).scalar_one()
        return [(await access_codes.lookup(s, secret(oj), e.id, c)) for c in
                (old["alice"], new["alice"].lower(), old["carol"])]
    gone, fresh, other = db(oj, find)
    assert gone is None and fresh.netid == "alice" and other.netid == "carol"
    assert codes_of(oj)["alice"] in pdf_text(call(oj, "GET", PDF).content)[1]
    assert call(oj, "POST", REISSUE, {"netid": "nobody9"}).status_code == 404
    assert call(oj, "POST", REISSUE, {}).status_code == 400


def test_refused_requests(oj):
    run(oj)
    assert oj.get(PDF).status_code == 401
    assert oj.post(REISSUE, json={"netid": "alice"}).status_code == 401
    for who in ("alice", "nobody", ""):
        assert call(oj, "GET", PDF, acting=who).status_code == 403
        assert call(oj, "POST", REISSUE, {"netid": "alice"}, acting=who).status_code == 403
    assert call(oj, "GET", f"{V1}/exams/nope/access-codes.pdf").status_code == 404
    assert call(oj, "POST", f"{V1}/exams/nope/access-codes/reissue", {"netid": "alice"}).status_code == 404
    assert db(oj, lambda s: s.scalar(select(ExamAccessCode.id).limit(1))) is None


def test_empty_roster_is_409(oj):
    run(oj)

    async def empty(s):
        await s.execute(delete(OjRosterEntry))
        await s.commit()
    db(oj, empty)
    r = call(oj, "GET", PDF)
    assert r.status_code == 409 and "no student list" in r.json()["detail"]


def set_names(c, netid, first, last):
    async def go(s):
        from sqlalchemy import update
        from app.models import User
        uid = (await s.execute(select(User.id).where(User.netid == netid))).scalar_one()
        await s.execute(update(OjRosterEntry).where(OjRosterEntry.user_id == uid)
                        .values(first_name=first, last_name=last))
        await s.commit()
    db(c, go)


def stored(c):
    async def q(s):
        return {r.netid: (r.first_name, r.last_name) for r in (await s.execute(select(ExamAccessCode))).scalars()}
    return db(c, q)


def test_names_come_from_the_roster_and_refresh(fake, oj):
    fake.roster[0].update(first_name="Mary Ann", last_name="Li")
    run(oj)
    codes_of(oj)
    assert stored(oj)["alice"] == ("Mary Ann", "Li")
    assert stored(oj)["carol"] == ("Carol", "Wang")
    set_names(oj, "carol", "Caroline", "Wong")
    set_names(oj, "ax19", None, None)
    assert codes_of(oj) == codes_of(oj)
    assert stored(oj)["carol"] == ("Caroline", "Wong")
    assert stored(oj)["ax19"][0] and stored(oj)["ax19"] != (None, None)
    text = pdf_text(call(oj, "GET", PDF).content)[1]
    assert "Mary Ann" in text and "Caroline" in text


def test_chinese_names_build(oj):
    run(oj)
    set_names(oj, "alice", "小明", "王")
    r = call(oj, "GET", PDF)
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    doc, text = pdf_text(r.content)
    assert len(doc) == 2 and "ax19" in text


def test_concurrent_creation_and_collision(oj, monkeypatch):
    run(oj)
    first = codes_of(oj)
    assert codes_of(oj) == first

    async def wipe(s):
        await s.execute(delete(ExamAccessCode))
        await s.commit()
    db(oj, wipe)

    real_rows = access_codes._rows
    calls = {"n": 0}

    async def stale_rows(s, exam):
        calls["n"] += 1
        if calls["n"] == 1:
            from app.db import get_database
            async with get_database().sessionmaker() as other:
                await access_codes._ensure(other, secret(oj), exam)
            return {}
        return await real_rows(s, exam)
    monkeypatch.setattr(access_codes, "_rows", stale_rows)
    made = codes_of(oj)
    monkeypatch.undo()
    assert set(made) == {"alice", "carol", "ax19", "bl642"} and calls["n"] >= 3
    assert codes_of(oj) == made

    db(oj, wipe)
    queue = iter(["AAAAAA", "AAAAAA", "BBBBBB", "CCCCCC", "DDDDDD", "EEEEEE"])
    monkeypatch.setattr(access_codes, "generate_code", lambda: next(queue))
    got = codes_of(oj)
    assert sorted(got.values()) == ["AAAAAA", "BBBBBB", "CCCCCC", "DDDDDD"]
