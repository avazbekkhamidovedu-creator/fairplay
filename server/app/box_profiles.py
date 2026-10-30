"""Open an exam with Duke Box: the shared link of an exam and the hosts the apps let it use.

A professor sets `box_link` (a Duke Box shared link, https://duke.box.com/s/<token>) on an
exam. FairPlay stores it as https://duke.app.box.com/s/<token> and hands a running attempt
`tools.box` so the app opens that page in its own browser pane. The pane may load only the
shared item (path_prefix), the sign-in hosts for the sign-in pages, and the asset hosts for
scripts and previews; blocked_requests lists what stays refused even there.

The host lists below are DATA, selected by the BOX_PROFILE setting ("duke" by default;
"fakebox" is a test profile and only allowed when FAIRPLAY_DEV=1). They come from a real
Box page and Box's firewall documentation, not from a recorded login: CONFIRM WITH HAR
(scripts/box_trace.py prints the hosts of a browser HAR) and refine here.
"""
from __future__ import annotations

import logging
import os
import re
from urllib.parse import urlsplit

log = logging.getLogger("fairplay.box")

APP_HOSTS = ("duke.app.box.com", "duke.box.com")
CANONICAL_HOST = "duke.app.box.com"
TOKEN_RE = re.compile(r"[A-Za-z0-9]{10,64}")
MAX_LINK = 2048

# sign_in_hosts: {"host", "paths"}; paths null = any path, else path globs ("*" at the end
# only). A host "*.base" is every subdomain of base. confirm with HAR.
# blocked_requests: {"host", "path"} (+ optional "methods"); host "*" = any host the pane may
# load; path is a glob ("*" at the end only). Refined after a real session is traced.
PROFILES: dict[str, dict] = {
    "duke": {
        "sign_in_hosts": [
            {"host": "duke.account.box.com", "paths": ["/login*"]},
            {"host": "account.box.com", "paths": ["/login*"]},
            {"host": "sso.services.box.net", "paths": None},
            {"host": "shib.oit.duke.edu", "paths": None},
            {"host": "*.duosecurity.com", "paths": None},
        ],
        "asset_hosts": ["cdn01.boxcdn.net", "e3.boxcdn.net", "*.boxcloud.com", "api.box.com"],
        "blocked_requests": [
            {"host": "*", "path": "/upload*"},
            {"host": "*", "path": "/api/2.0/files/content*"},
            {"host": "*", "path": "/api/2.0/files/upload_sessions*"},
            {"host": "*", "path": "/api/2.0/files/*/upload_sessions*"},
            {"host": "*", "path": "/api/2.0/folders", "methods": ["POST"]},
            {"host": "*", "path": "/api/2.0/shared_links*"},
            {"host": "*", "path": "/api/2.0/comments*"},
            {"host": "*", "path": "/api/2.0/collaborations*"},
            {"host": "*", "path": "/api/2.0/collaboration_whitelist*"},
            {"host": "*", "path": "/api/2.0/ai*"},
            {"host": "*", "path": "/api/2.0/web_links*"},
            {"host": "*", "path": "/app-api/ai*"},
            {"host": "*", "path": "/app-api/enduserapp/shared-item*", "methods": ["PUT", "DELETE"]},
            {"host": "*", "path": "/box-ai*"},
            {"host": "*", "path": "/invite*"},
            {"host": "*", "path": "/collab*"},
            {"host": "*", "path": "/comments*"},
            {"host": "*", "path": "/settings*"},
            {"host": "*", "path": "/account*"},
            {"host": "*", "path": "/master*"},
            {"host": "*", "path": "/folder/*"},
            {"host": "*", "path": "/file/*"},
            {"host": "*", "path": "/notes*"},
        ],
    },
    "fakebox": {
        "sign_in_hosts": [{"host": "fakebox-login.test", "paths": ["/login*"]}],
        "asset_hosts": ["fakebox-assets.test"],
        "blocked_requests": [{"host": "*", "path": "/upload*"}, {"host": "*", "path": "/blocked*"}],
    },
}
DEFAULT_PROFILE = "duke"
DEV_ONLY = frozenset({"fakebox"})


class BoxLinkError(ValueError):
    """A Box link FairPlay refuses; str() is a plain message for the professor."""


class BoxProfileError(ValueError):
    """BOX_PROFILE names no profile, or one that is not allowed here."""


def normalize_link(raw) -> str:
    """The stored form https://duke.app.box.com/s/<token> of a Duke Box shared link, or
    BoxLinkError. Only /s/<token> on duke.box.com or duke.app.box.com, https, no user, port,
    extra path; a query or fragment is dropped."""
    if not isinstance(raw, str):
        raise BoxLinkError("The Box link must be text")
    s = raw.strip()
    if not s:
        raise BoxLinkError("Enter a Duke Box shared link")
    if len(s) > MAX_LINK or any(ord(c) < 33 or ord(c) == 127 for c in s):
        raise BoxLinkError("That is not a valid link")
    try:
        u = urlsplit(s)
        port = u.port
    except ValueError:
        raise BoxLinkError("That is not a valid link") from None
    if u.scheme.lower() != "https":
        raise BoxLinkError("The Box link must start with https://")
    if u.username is not None or u.password is not None or "@" in u.netloc:
        raise BoxLinkError("The Box link must not contain a user name or password")
    if port is not None or ":" in u.netloc:
        raise BoxLinkError("The Box link must not contain a port")
    host = (u.hostname or "").lower().rstrip(".")
    if host not in APP_HOSTS:
        raise BoxLinkError("Only Duke Box links are allowed (duke.box.com or duke.app.box.com)")
    m = re.fullmatch(r"/s/([^/]*)/?", u.path)
    if m is None:
        raise BoxLinkError("Use a shared link of the form https://duke.box.com/s/..., not a folder or file link. "
                           "In Box, open the item, choose Share, and copy the shared link")
    if not TOKEN_RE.fullmatch(m.group(1)):
        raise BoxLinkError("That shared link looks incomplete or too long")
    return f"https://{CANONICAL_HOST}/s/{m.group(1)}"


def token_of(link: str) -> str:
    return link.rsplit("/", 1)[-1]


def checked_link(stored) -> tuple[str | None, str | None]:
    """(link, error) for a stored value: an invalid or empty one gives no link."""
    if stored is None or (isinstance(stored, str) and not stored.strip()):
        return None, None
    try:
        return normalize_link(stored), None
    except BoxLinkError as exc:
        return None, str(exc)


def selected_profile(name: str | None = None, dev: bool | None = None) -> tuple[str, dict]:
    """(name, profile) chosen by BOX_PROFILE (default duke); fakebox only under FAIRPLAY_DEV."""
    if name is None:
        name = os.environ.get("BOX_PROFILE", "").strip().lower() or DEFAULT_PROFILE
    if dev is None:
        from .config import _flag
        dev = _flag("FAIRPLAY_DEV", False)
    if name not in PROFILES:
        raise BoxProfileError(f"BOX_PROFILE {name!r} is not a profile ({', '.join(sorted(PROFILES))})")
    if name in DEV_ONLY and not dev:
        raise BoxProfileError(f"BOX_PROFILE {name!r} is only allowed with FAIRPLAY_DEV=1")
    return name, PROFILES[name]


def box_tools(link: str) -> dict | None:
    """`tools.box` for a running attempt, or None when the profile can't be used (the exam
    then simply has no Box pane; the misconfiguration is logged)."""
    try:
        _, prof = selected_profile()
    except BoxProfileError as exc:
        log.error("Box pane off: %s", exc)
        return None
    return {"start_url": link, "app_hosts": list(APP_HOSTS), "path_prefix": "/s/" + token_of(link),
            "sign_in_hosts": [dict(x) for x in prof["sign_in_hosts"]],
            "asset_hosts": list(prof["asset_hosts"]),
            "blocked_requests": [dict(x) for x in prof["blocked_requests"]]}


def is_box_host(host: str) -> bool:
    h = host.lower()
    return h == "box.com" or h.endswith(".box.com")


def without_box_hosts(websites: list[str]) -> list[str]:
    """The Box page opens in its own pane (tools.box), never as an allowed website."""
    return [w for w in websites if not is_box_host(w.lstrip("*.").lower())]


def allowed_host_strings() -> list[str]:
    """Plain host / '*.suffix' strings for the active profile's app, sign-in and asset hosts."""
    _, prof = selected_profile()
    out: list[str] = []
    for h in [*APP_HOSTS, *(x["host"] for x in prof["sign_in_hosts"]), *prof["asset_hosts"]]:
        if h not in out:
            out.append(h)
    return out
