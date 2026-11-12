"""What the exam lock may open for a DKU OJ exam: whole https pages and the origins they need.

{"pages": [https URLs], "resources": ["https://host", ...]}; empty means a closed-book exam.
Every entry goes through the server's deny-list (site_policy), whatever its source.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from ...box_profiles import APP_HOSTS, BoxProfileError, checked_link, selected_profile, is_box_host
from ...schemas import WILDCARD_PREFIX, is_shared_suffix, parse_website_entry
from ...site_companions import companions
from ..site_policy import filter_sites

MAX_LOCK_PAGES_AND_RESOURCES = 32
CLOSED = {"pages": [], "resources": []}


def _allowed(host: str) -> bool:
    kept, _ = filter_sites([host])
    return bool(kept)


def _origin(host: str, wildcard_ok: bool = False) -> str | None:
    """"https://host" or, for resources, "https://*.suffix" (suffix of 2+ labels, never a shared suffix)."""
    h = host.lower().rstrip(".")
    if h.startswith(WILDCARD_PREFIX):
        suffix = h[len(WILDCARD_PREFIX):]
        if not wildcard_ok or "*" in suffix or is_shared_suffix(suffix):
            return None
        if not _allowed(WILDCARD_PREFIX + suffix) or not _allowed(suffix):
            return None
        return "https://" + h
    if not h or "*" in h or not _allowed(h):
        return None
    return "https://" + h


def _box_hosts() -> list[str]:
    try:
        _, prof = selected_profile()
    except BoxProfileError:
        return []
    return [*APP_HOSTS, *(x["host"] for x in prof["sign_in_hosts"]), *prof["asset_hosts"]]


def build_lock_sites(websites, links=None, box_link=None) -> dict:
    pages: list[str] = []

    def add(url: str, host: str) -> None:
        if _allowed(host) and url not in pages:
            pages.append(url)

    link, _ = checked_link(box_link)
    if link:
        add(link, urlsplit(link).hostname or "")
    hosts: list[str] = []
    for line in websites or []:
        if not isinstance(line, str) or not line.strip():
            continue
        try:
            host, parsed = parse_website_entry(line)
        except ValueError:
            continue
        hosts.append(host)
        if parsed is not None:
            add(parsed["url"], parsed["host"])
        else:
            base = host[len(WILDCARD_PREFIX):] if host.startswith(WILDCARD_PREFIX) else host
            add("https://" + base + "/", base)
    for item in links or []:
        if isinstance(item, dict) and isinstance(item.get("url"), str):
            try:
                host, parsed = parse_website_entry(item["url"])
            except ValueError:
                continue
            if parsed is not None:
                add(parsed["url"], parsed["host"])

    resources: list[str] = []

    def add_resource(host: str) -> None:
        o = _origin(host, wildcard_ok=True)
        if o and o not in resources:
            resources.append(o)

    # Box chain first: a Box page is useless without it.
    if any(is_box_host(urlsplit(p).hostname or "") for p in pages):
        for h in _box_hosts():
            add_resource(h)
    for group in companions(hosts):
        for site in group["sites"]:
            add_resource(site)
    if not pages:
        return {"pages": [], "resources": []}
    # Apps 0.8.5 have no Box list of their own and need the Box sign-in chain and asset hosts as resources; 0.8.6+ drops
    # them client-side (isChainResource; Windows from 0.8.6 on), so emitting them is safe. They never cost a page: pages first,
    # then the resources within what is left of the shared cap.
    pages = pages[:MAX_LOCK_PAGES_AND_RESOURCES]
    page_origins = {"https://" + (urlsplit(p).hostname or "") for p in pages}
    resources = [r for r in resources if r not in page_origins][:MAX_LOCK_PAGES_AND_RESOURCES - len(pages)]
    return {"pages": pages, "resources": resources}
