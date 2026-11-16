"""Default allowed websites of an open-book DKU OJ exam (SEB-style list: the OJ, Duke Shibboleth, Duke Box), seeded
when an exam becomes open book, never for a closed-book exam; every default passes the site policy; the lock gets the
pages and the Box sign-in chain a real login needs."""
import re
from urllib.parse import urlsplit

import pytest

from app.integrations.dkuoj import open_book_defaults as obd
from app.integrations.dkuoj.lock_sites import build_lock_sites
from app.integrations.site_policy import filter_sites
from app.schemas import parse_website_entry

from .test_dkuoj_bridge import KEY
from .test_dkuoj_bridge_v2 import V1, call, oj  # noqa: F401  (fixtures)
from .test_dkuoj_bridge import run
from .test_dkuoj_zero_schema_2026_10_24 import exam_row, fake, settings_of  # noqa: F401

HOSTS = ["oj.dukekunshan.edu.cn", "shib.oit.duke.edu", "duke.app.box.com", "duke.account.box.com",
         "sso.services.box.net", "duke.box.com"]


def post(oj, body, key=KEY):
    return call(oj, "POST", f"{V1}/exams/{key}/settings", body)


def seb_regex(row: str):
    """The SEB row read the way the filter meant it ("host/*": the host, any port, any path, any case; a row with
    https:// only https, a row without a scheme both)."""
    scheme = r"https://" if row.startswith("https://") else r"https?://"
    host = re.escape(row.replace("https://", "").rsplit("/*", 1)[0])
    return re.compile(rf"^{scheme}{host}(:[0-9]+)?(/.*)?$", re.I)


def test_the_default_list_is_the_seb_list():
    assert list(obd.DEFAULT_SITES) == HOSTS
    assert len(obd.SEB_FILTER) == 6 and all(r.endswith("/*") for r in obd.SEB_FILTER)
    assert [r.replace("https://", "").rsplit("/*", 1)[0] for r in obd.SEB_FILTER] == HOSTS


@pytest.mark.parametrize("host", HOSTS)
def test_each_default_is_a_valid_unrefused_site(host):
    assert parse_website_entry(host) == (host, None)
    kept, blocked = filter_sites([host])
    assert kept == [host] and blocked == []


@pytest.mark.parametrize("variant", ["HTTPS://DUKE.BOX.COM/", "https://Duke.Box.com", "http://duke.box.com/",
                                     "https://duke.box.com/", "duke.box.com/", "DUKE.box.com"])
def test_spellings_of_a_default_are_the_same_site(variant):
    assert parse_website_entry(variant)[0] == "duke.box.com"


@pytest.mark.parametrize("bad", ["https://duke.box.com:8443/", "https://user@duke.box.com/", "duke.box.com:443",
                                 "https://duke.box.com/folder/1 extra", "*.duke.box.com/x"])
def test_ports_userinfo_and_odd_forms_are_refused(bad):
    with pytest.raises(ValueError):
        parse_website_entry(bad)


@pytest.mark.parametrize("row,url,hit", [
    ("oj.dukekunshan.edu.cn/*", "https://oj.dukekunshan.edu.cn/", True),
    ("oj.dukekunshan.edu.cn/*", "http://OJ.dukekunshan.edu.cn:8000/contest/x", True),
    ("oj.dukekunshan.edu.cn/*", "https://oj.dukekunshan.edu.cn.evil.example/", False),
    ("https://duke.box.com/*", "https://duke.box.com/s/abc", True),
    ("https://duke.box.com/*", "https://DUKE.BOX.COM/Folder/1", True),
    ("https://duke.box.com/*", "http://duke.box.com/s/abc", False),
    ("https://duke.box.com/*", "https://evilduke.box.com/", False),
    ("https://sso.services.box.net/*", "https://sso.services.box.net/sp/ACS.saml2", True),
    ("https://duke.account.box.com/*", "https://duke.account.box.com/login", True),
    ("shib.oit.duke.edu/*", "https://shib.oit.duke.edu/idp/profile/SAML2/Redirect/SSO", True),
])
def test_seb_rows_as_regexes_match_what_the_filter_meant(row, url, hit):
    assert bool(seb_regex(row).match(url)) is hit


def test_seb_rows_cover_the_hosts_of_the_defaults():
    for row, host in zip(obd.SEB_FILTER, obd.DEFAULT_SITES):
        assert seb_regex(row).match(f"https://{host}/any/path?x=1")


# ---------------------------------------------------------------- seeding
def test_opening_the_book_seeds_the_defaults(oj, fake):
    run(oj)
    r = post(oj, {"open_book": True})
    assert r.status_code == 200, r.text
    s = r.json()["settings"]
    assert s["open_book"] is True
    assert s["websites"][:1] == ["oj.dukekunshan.edu.cn"]
    # the Box account / SSO hosts are listed for the instructor to see and edit
    assert settings_of(oj)["websites_input"] == HOSTS
    assert "websites" in s["set_in_fairplay"] and s["websites_blocked"] == []
    assert exam_row(oj).websites == s["websites"]


def test_the_instructor_can_edit_and_extend_the_defaults(oj, fake):
    run(oj)
    post(oj, {"open_book": True})
    r = post(oj, {"websites": [*HOSTS[:3], "docs.python.org"]})
    assert r.status_code == 200
    assert r.json()["settings"]["websites_input"] == [*HOSTS[:3], "docs.python.org"]
    post(oj, {"calculator": "scientific"})                      # an unrelated call does not seed again
    assert settings_of(oj)["websites_input"] == [*HOSTS[:3], "docs.python.org"]


def test_websites_sent_with_open_book_win_over_the_defaults(oj, fake):
    run(oj)
    r = post(oj, {"open_book": True, "websites": ["docs.python.org"]})
    assert r.json()["settings"]["websites_input"] == ["docs.python.org"]


def test_closed_book_gets_no_defaults(oj, fake):
    run(oj)
    s = post(oj, {"open_book": False}).json()["settings"]
    assert s["websites"] == [] and "websites_input" not in s and s["open_book"] is False
    s = post(oj, {"calculator": "graphing"}).json()["settings"]
    assert s["websites"] == [] and s["lock_sites"] == {"pages": [], "resources": []}


def test_an_exam_that_already_has_sites_is_not_reseeded(oj, fake):
    run(oj)
    post(oj, {"websites": ["docs.python.org"]})
    s = post(oj, {"open_book": True}).json()["settings"]
    assert s["websites"] == ["docs.python.org"]


def test_pending_settings_seed_too(oj, fake):
    r = post(oj, {"open_book": True}, key="later-key")
    assert r.json()["synced"] is False
    assert r.json()["settings"]["websites_input"] == HOSTS
    assert post(oj, {"open_book": False}, key="other-key").json()["settings"]["websites"] == []


# ---------------------------------------------------------------- what the lock gets
def test_lock_sites_of_the_defaults():
    sites = build_lock_sites(HOSTS)
    pages = sites["pages"]
    assert pages == ["https://oj.dukekunshan.edu.cn/", "https://shib.oit.duke.edu/", "https://duke.app.box.com/",
                     "https://duke.box.com/"]
    # Box account and SSO are the sign-in chain of a Box tab, never pages
    assert not any(urlsplit(p).hostname in obd.CHAIN_ONLY_HOSTS for p in pages)
    res = set(sites["resources"])
    for want in ("https://duke.account.box.com", "https://sso.services.box.net", "https://*.boxcdn.net",
                 "https://*.boxcloud.com", "https://*.duosecurity.com"):
        assert want in res, want
    assert not any(r.startswith("https://duke.app.box.com") or r == "https://duke.box.com" for r in res)


def test_embedded_content_hosts_of_every_login_flow_are_present():
    res = set(build_lock_sites(HOSTS)["resources"])
    # Box sign-in -> Duke Shibboleth (a page) -> Duo prompt (*.duosecurity.com) -> back; Box scripts and previews
    for want in ("https://cdn01.boxcdn.net", "https://e3.boxcdn.net", "https://*.boxcdn.net", "https://*.boxcloud.com"):
        assert want in res, want
    # (api.box.com is denied by the deny-list as a resource; the apps' Box-tab chain allows it by itself)
    assert "https://shib.oit.duke.edu/" in build_lock_sites(HOSTS)["pages"]


def test_without_a_box_page_there_is_no_box_chain():
    s = build_lock_sites(["oj.dukekunshan.edu.cn", "shib.oit.duke.edu"])
    assert s["pages"] == ["https://oj.dukekunshan.edu.cn/", "https://shib.oit.duke.edu/"]
    assert not any("box" in r for r in s["resources"])


def test_closed_book_lock_sites_are_empty():
    assert build_lock_sites([]) == {"pages": [], "resources": []}


def test_defaults_pass_the_deny_list_unless_the_administrator_clears_the_exceptions(monkeypatch):
    monkeypatch.delenv("FAIRPLAY_SITE_POLICY_EXCEPTIONS", raising=False)
    kept, blocked = filter_sites(list(HOSTS))
    assert kept == HOSTS and blocked == []
    kept, blocked = filter_sites(["app.box.com", "box.com", "*.box.com", "other.box.com", "account.box.com"])
    assert kept == [] and len(blocked) == 5          # Box in general stays refused
    monkeypatch.setenv("FAIRPLAY_SITE_POLICY_EXCEPTIONS", "")   # the administrator's switch still wins
    assert [b["site"] for b in filter_sites(HOSTS)[1]] == ["duke.app.box.com", "duke.account.box.com", "duke.box.com"]


def test_a_box_folder_link_with_the_defaults_reports_no_refusal(oj, fake):
    from .test_box_exam_2026_10_30 import CANON
    run(oj)
    post(oj, {"open_book": True})
    s = post(oj, {"box_link": CANON}).json()["settings"]
    assert s["box_link"] == CANON and s["websites_blocked"] == []
    assert s["lock_sites"]["pages"][0] == CANON
