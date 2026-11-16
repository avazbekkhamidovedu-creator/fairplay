"""Default allowed websites of an open-book DKU OJ exam.

They come from the Safe Exam Browser URL filter the university used (every row Active, Regex, Allow;
"Filter also embedded content" off):

    oj.dukekunshan.edu.cn/*   shib.oit.duke.edu/*   https://duke.app.box.com/*
    https://duke.account.box.com/*   https://sso.services.box.net/*   https://duke.box.com/*

FairPlay's own form of an allowed site is a host name (every page of the host, https only), so each row is
kept as its host (DEFAULT_SITES, what the instructor sees and may edit) and SEB_FILTER keeps the original
regular expressions for the documentation and the tests.

The list is SEEDED, not forced: an exam that becomes open book (settings call with open_book true) with no
websites yet gets these as its websites; the instructor can then remove or add lines. A closed-book exam
never gets them. `seed` is the only place that decides this.

Sign-in hosts (Box account / SSO) are in the list so the instructor sees them, but they are never an
approved page of their own: the apps open them only as the sign-in chain of a Box tab (BoxSignIn), see
lock_sites.CHAIN_ONLY_HOSTS and docs/open-book-default-sites.md.
"""
from __future__ import annotations

DEFAULT_SITES: tuple[str, ...] = (
    "oj.dukekunshan.edu.cn",
    "shib.oit.duke.edu",
    "duke.app.box.com",
    "duke.account.box.com",
    "sso.services.box.net",
    "duke.box.com",
)

# The rows of the SEB filter as given (Regex on, Action Allow).
SEB_FILTER: tuple[str, ...] = (
    "oj.dukekunshan.edu.cn/*",
    "shib.oit.duke.edu/*",
    "https://duke.app.box.com/*",
    "https://duke.account.box.com/*",
    "https://sso.services.box.net/*",
    "https://duke.box.com/*",
)

# Hosts that are listed for the instructor and for the sign-in chain but are not pages a student may open.
CHAIN_ONLY_HOSTS = frozenset({"duke.account.box.com", "account.box.com", "sso.services.box.net"})


def is_default_host(host: str) -> bool:
    return host.strip().lower().rstrip(".") in DEFAULT_SITES


def seed(sent: list[str], ov: dict, *, was_open_book: bool, has_sites: bool) -> bool:
    """Called with the fields of one settings call (`sent`, `ov` as _settings_body returns them).
    When the call turns open book ON (it was off), the call sends no websites and the exam has none,
    the defaults become its websites. Returns True when it did. Mutates `sent` and `ov`."""
    if ov.get("open_book") is not True or was_open_book or has_sites or "websites" in sent:
        return False
    from ...schemas import MAX_WEBSITES  # noqa: F401  (the defaults are well inside the cap)
    from .sync import parse_sites
    sites, links, blocked = parse_sites(list(DEFAULT_SITES), None, None)
    sent.append("websites")
    ov.update(websites=sites, website_links=links, websites_blocked=blocked, websites_input=list(DEFAULT_SITES))
    return True
