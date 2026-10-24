"""2026-10-24: the early upload window (open-book files N days before the exam), allowed
websites as full links (start pages), Duke Box (site policy exception + companion sites) and
DKU OJ's POST /exams/<key>/settings (FairPlay-set values win over the exam list)."""
from datetime import timedelta

import pytest

from app.integrations import site_policy
from app.schemas import parse_website_entry, split_websites
from app.site_companions import MAX_LOCK_SITES, allowed_sites, companions, companions_for
from app.timeutil import iso, utcnow

from .conftest import SAMPLE_EXAM
from .test_dkuoj_bridge import KEY, run
from .test_dkuoj_bridge_v2 import V1, call, fake, oj  # noqa: F401  (fixtures)

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
BOX = "https://duke.app.box.com/folder/0"


def create(client, teacher, **extra):
    r = client.post("/api/exams", json={**SAMPLE_EXAM, **extra}, headers=teacher)
    assert r.status_code == 201, r.text
    return r.json()


def upload(client, h, eid, name="notes.pdf", data=PDF):
    return client.post(f"/api/student/exams/{eid}/materials", headers=h,
                       files={"file": (name, data, "application/pdf")})


def listed(client, h, eid):
    return next((x for x in client.get("/api/student/exams", headers=h).json() if x["id"] == eid), None)


def window_exam(client, teacher, in_hours=24, days=2, **extra):
    at = (utcnow() + timedelta(hours=in_hours)).replace(microsecond=0)
    return create(client, teacher, open_book=True, upload_opens_days=days, scheduled_at=iso(at), **extra)


# ---------------------------------------------------------------- links
@pytest.mark.parametrize("given,host,url,title", [
    ("https://duke.app.box.com/folder/0", "duke.app.box.com", "https://duke.app.box.com/folder/0", None),
    ("http://Duke.App.Box.com/folder/0/somewherenew/123", "duke.app.box.com",
     "https://duke.app.box.com/folder/0/somewherenew/123", None),
    ("Course folder https://duke.app.box.com/folder/9", "duke.app.box.com", "https://duke.app.box.com/folder/9",
     "Course folder"),
    ("https://docs.python.org/3/library/?q=1#x", "docs.python.org", "https://docs.python.org/3/library/?q=1#x", None),
])
def test_full_link_gives_host_and_start_page(given, host, url, title):
    h, link = parse_website_entry(given)
    assert h == host and link == {"host": host, "url": url, "title": title}


@pytest.mark.parametrize("plain", ["docs.python.org", "https://docs.python.org/", "*.python.org"])
def test_plain_site_has_no_start_page(plain):
    assert parse_website_entry(plain)[1] is None


@pytest.mark.parametrize("bad", ["https://*.box.com/folder/0", "https://box.com:8443/x", "https://u@box.com/x",
                                 "https://box.com/a b", "docs.python.org/3/library", "two words"])
def test_bad_links_refused(bad):
    with pytest.raises(ValueError):
        parse_website_entry(bad)


def test_links_share_one_host():
    hosts, links = split_websites([BOX, "Week 3 https://duke.app.box.com/folder/123", "docs.python.org"])
    assert hosts == ["duke.app.box.com", "docs.python.org"]
    assert [x["url"] for x in links] == [BOX, "https://duke.app.box.com/folder/123"]


def test_teacher_saves_links_and_sees_companions(client, teacher, alice):
    e = create(client, teacher, websites=[f"Course folder {BOX}", "docs.python.org"])
    # the apps' list keeps its format: plain hosts
    assert e["websites"] == ["duke.app.box.com", "docs.python.org"]
    assert e["website_links"] == [{"host": "duke.app.box.com", "url": BOX, "title": "Course folder"}]
    comp = e["companions"]
    assert [c["name"] for c in comp] == ["Duke Box"] and comp[0]["for"] == "duke.app.box.com"
    for s in ("app.box.com", "account.box.com", "duke.account.box.com", "sso.services.box.net",
              "*.boxcdn.net", "*.boxcloud.com", "shib.oit.duke.edu", "*.duosecurity.com"):
        assert s in comp[0]["sites"], s
    # a PUT without websites keeps them and their links
    r = client.put(f"/api/exams/{e['id']}", json={**SAMPLE_EXAM, "title": "T2"}, headers=teacher).json()
    assert r["website_links"] == e["website_links"]
    # the student gets the start page and, for the lock, the websites + companions
    e = client.post(f"/api/exams/{e['id']}/open", headers=teacher).json()
    t = listed(client, alice, e["id"])["tools"]
    assert t["websites"] == ["duke.app.box.com", "docs.python.org"]
    assert t["website_links"] == [{"host": "duke.app.box.com", "url": BOX, "title": "Course folder"}]
    assert t["allowed_sites"][:2] == t["websites"] and "*.boxcdn.net" in t["allowed_sites"]


def test_site_check_names_companions(client, teacher, monkeypatch):
    import app.site_check as sc

    async def fake_check(entry, own):
        return {"host": entry, "frameable": True, "reason": ""}
    monkeypatch.setattr(sc, "check_site", fake_check)
    r = client.get("/api/tools/site-check", params={"host": BOX}, headers=teacher).json()
    assert r["host"] == "duke.app.box.com" and r["companions"]["name"] == "Duke Box"
    r = client.get("/api/tools/site-check", params={"host": "docs.python.org"}, headers=teacher).json()
    assert r["companions"] is None


def test_companions_table(monkeypatch):
    name, sites = companions_for("app.box.com")
    assert name == "Box" and "{tenant}" not in "".join(sites) and "app.box.com" not in sites
    assert companions_for("docs.python.org") == (None, [])
    # never more than the Windows app reads
    many = [f"t{i}.app.box.com" for i in range(20)]
    assert MAX_LOCK_SITES == 64 and len(allowed_sites(many)) == 46      # 20 + 6 shared + 20 tenants
    import app.site_companions as sc
    monkeypatch.setattr(sc, "MAX_LOCK_SITES", 30)
    assert len(allowed_sites(many)) == 30 and allowed_sites(many)[:20] == many
    assert sum(len(c["sites"]) for c in companions(many)) == 10


# ---------------------------------------------------------------- site policy (external exams)
def test_duke_box_allowed_other_storage_refused(monkeypatch):
    monkeypatch.delenv(site_policy.ENV_EXCEPTIONS, raising=False)
    monkeypatch.delenv(site_policy.ENV_ALLOWLIST, raising=False)
    assert site_policy.denied("duke.app.box.com") is None and site_policy.denied("duke.box.com") is None
    for bad in ("app.box.com", "box.com", "*.box.com", "*.app.box.com", "other.app.box.com",
                "www.dropbox.com", "drive.google.com", "onedrive.live.com"):
        assert site_policy.denied(bad), bad
    monkeypatch.setenv(site_policy.ENV_EXCEPTIONS, "")
    assert site_policy.denied("duke.app.box.com")


# ---------------------------------------------------------------- upload window, native exams
def test_upload_window_shows_only_the_upload(client, teacher, alice):
    e = window_exam(client, teacher)
    assert e["upload_opens_days"] == 2 and e["status"] == "draft"
    row = listed(client, alice, e["id"])
    assert row is not None and row["status"] == "draft"
    assert row["upload_window"]["closes_at"] == e["scheduled_at"]
    # nothing about the questions, no package, no start
    assert row["question_count"] is None and row["package_id"] is None
    assert "questions" not in row and "start_code" not in row
    assert client.post(f"/api/student/exams/{e['id']}/start", json={"start_code": "000000"},
                       headers=alice).status_code == 409
    assert client.get(f"/api/student/exams/{e['id']}/package", headers=alice).status_code == 409
    assert client.get(f"/api/student/exams/{e['id']}/package-key", headers=alice).status_code == 409
    # the course card's next exam says nothing about the questions either
    k = next(x for x in client.get("/api/student/classes", headers=alice).json() if x["id"] == e["class_id"])
    assert k["next_exam"]["title"] == e["title"] and k["next_exam"]["question_count"] is None
    m = client.get(f"/api/student/exams/{e['id']}/materials", headers=alice).json()
    assert m["can_change"] is True and m["upload_closes_at"] == e["scheduled_at"]
    assert set(m) >= {"files", "used_bytes", "max_files", "upload_opens_at"} and "questions" not in m
    # upload, list, remove, upload again
    f = upload(client, alice, e["id"])
    assert f.status_code == 201, f.text
    assert client.delete(f"/api/student/exams/{e['id']}/materials/{f.json()['id']}", headers=alice).status_code == 204
    assert upload(client, alice, e["id"]).status_code == 201
    # the teacher opens it: uploads close, the files stay
    client.post(f"/api/exams/{e['id']}/open", headers=teacher)
    m = client.get(f"/api/student/exams/{e['id']}/materials", headers=alice).json()
    assert m["can_change"] is False and m["reason"] == "Uploads for this exam have closed"
    assert len(m["files"]) == 1
    assert upload(client, alice, e["id"]).status_code == 409
    row = listed(client, alice, e["id"])
    assert row["upload_window"] is None and row["question_count"] == 3


def test_upload_window_not_yet_off_or_not_member(client, teacher, alice):
    later = window_exam(client, teacher, in_hours=24 * 5, days=2)       # opens in 3 days
    assert listed(client, alice, later["id"]) is None
    r = upload(client, alice, later["id"])
    assert r.status_code == 409 and r.json()["detail"] == "Uploads for this exam have not opened yet"
    off = window_exam(client, teacher, days=0)
    assert listed(client, alice, off["id"]) is None
    assert upload(client, alice, off["id"]).status_code == 409        # a draft without a window
    closed_book = create(client, teacher, upload_opens_days=2,
                         scheduled_at=iso((utcnow() + timedelta(hours=5)).replace(microsecond=0)))
    assert listed(client, alice, closed_book["id"]) is None
    other = window_exam(client, teacher)
    from .conftest import login
    outsider = login(client, "bob")
    # bob is in the default class too: take him out
    k = other["class_id"]
    client.delete(f"/api/classes/{k}/members/bob", headers=teacher)
    assert listed(client, outsider, other["id"]) is None


def test_upload_window_closes_at_the_date(client, teacher, alice, monkeypatch):
    e = window_exam(client, teacher, in_hours=2, days=1)
    assert upload(client, alice, e["id"]).status_code == 201
    import app.materials as mat
    import app.routes_student as rs
    later = utcnow() + timedelta(hours=3)
    monkeypatch.setattr(mat, "utcnow", lambda: later)
    monkeypatch.setattr(rs, "utcnow", lambda: later)
    assert upload(client, alice, e["id"]).status_code == 409
    assert listed(client, alice, e["id"]) is None


def test_opened_before_the_window_takes_files_as_usual(client, teacher, alice):
    e = window_exam(client, teacher, in_hours=24 * 5, days=1)
    client.post(f"/api/exams/{e['id']}/open", headers=teacher)
    assert upload(client, alice, e["id"]).status_code == 201


def test_upload_days_validated_and_cleared_for_website_exams(client, teacher):
    for bad in (-1, 8, "x"):
        assert client.post("/api/exams", json={**SAMPLE_EXAM, "upload_opens_days": bad},
                           headers=teacher).status_code == 422
    body = {**SAMPLE_EXAM, "questions": [], "mode": "external", "external_url": "https://canvas.dku.edu/q",
            "open_book": True, "upload_opens_days": 3, "websites": [BOX]}
    e = client.post("/api/exams", json=body, headers=teacher).json()
    assert e["upload_opens_days"] == 0 and e["website_links"] == [] and e["websites"] == []


# ---------------------------------------------------------------- DKU OJ: POST /exams/<key>/settings
def test_dkuoj_settings_endpoint(oj, fake):
    path = f"{V1}/exams/{KEY}/settings"
    body = {"upload_opens_days": 2, "websites": [f"Course folder {BOX}", "docs.python.org", "www.dropbox.com"]}
    # not synced yet: kept for the exam (v2 §11) and shown as not synced
    v = call(oj, "POST", path, body).json()
    assert v["synced"] is False and v["settings"]["upload_opens_days"] == 2
    assert v["settings"]["websites"] == ["duke.app.box.com", "docs.python.org"]
    base = fake.listing

    def listing():
        x = base()
        x["settings"] = {**x["settings"], "open_book": True}
        return x
    fake.listing = listing
    run(oj)
    for who in ("alice", "nobody", ""):
        assert call(oj, "POST", path, body, acting=who).status_code == 403
    for bad in ({}, {"upload_opens_days": 8}, {"upload_opens_days": True}, {"upload_opens_days": "2"},
                {"websites": "docs.python.org"}, {"websites": [1]}, {"websites": ["x"] * 101}):
        assert call(oj, "POST", path, bad).status_code == 400, bad
    v = call(oj, "POST", path, body).json()
    s = v["settings"]
    assert s["upload_opens_days"] == 2 and s["set_in_fairplay"] == ["upload_opens_days", "websites"]
    assert s["websites"] == ["duke.app.box.com", "docs.python.org"]
    assert s["website_links"] == [{"host": "duke.app.box.com", "url": BOX, "title": "Course folder"}]
    assert s["companions"][0]["name"] == "Duke Box"
    assert [b["site"] for b in s["websites_blocked"]] == ["www.dropbox.com"]
    # the next sync keeps them (FairPlay-set wins over the exam list)
    run(oj)
    s = call(oj, "GET", f"{V1}/exams/{KEY}").json()["settings"]
    assert s["upload_opens_days"] == 2 and s["websites"] == ["duke.app.box.com", "docs.python.org"]
    # the student sees "Upcoming - upload your files" (DKU OJ start is in one hour)
    from .conftest import login
    alice = login(oj, "alice")
    e = next(x for x in oj.get("/api/student/exams", headers=alice).json() if x.get("source") == "dkuoj")
    assert e["upload_window"] and e["question_count"] is None and e["package_id"] is None
    assert e["tools"]["website_links"][0]["url"] == BOX
    assert upload(oj, alice, e["id"]).status_code == 201
    # only one field sent: the other stays
    s = call(oj, "POST", path, {"upload_opens_days": 1}).json()["settings"]
    assert s["upload_opens_days"] == 1 and s["websites"][0] == "duke.app.box.com"
    # null hands a field back to DKU OJ
    s = call(oj, "POST", path, {"websites": None}).json()["settings"]
    assert s["websites"] == ["docs.python.org"] and s["website_links"] == [] and s["set_in_fairplay"] == ["upload_opens_days"]
    assert [b["site"] for b in s["websites_blocked"]] == ["*.com"]
    s = call(oj, "POST", path, {"upload_opens_days": None}).json()["settings"]
    assert s["upload_opens_days"] == 0 and s["set_in_fairplay"] == []
    # once a student started: fixed
    call(oj, "POST", path, {"upload_opens_days": 2})
    v = call(oj, "POST", f"{V1}/exams/{KEY}/open", {"open_to": "all"}).json()
    a = oj.post(f"/api/student/exams/{e['id']}/start", json={"start_code": v["start_code"]}, headers=alice)
    assert a.status_code == 200, a.text
    assert call(oj, "POST", path, {"upload_opens_days": 3}).status_code == 409
