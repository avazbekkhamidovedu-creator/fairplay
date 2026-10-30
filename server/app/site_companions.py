"""2026-10-24: companion sites of an allowed website.

Some services only work when a few other hosts load too: Box shows a folder from
duke.app.box.com, but signs in through account.box.com / sso.services.box.net and the
university's sign-in (Duke: shib.oit.duke.edu, then Duo), serves its scripts from
*.boxcdn.net and its previews and downloads from *.boxcloud.com. When a teacher (or DKU OJ)
allows a site that matches a known service, the sites the exam lock opens (`allowed_sites`
for the apps) also get that service's companions. The teacher sees them listed separately
("Also opened for Duke Box to work: ..."); students never see them as sites of their own.

Kept small and data-driven: SERVICES below. Sources (2026-10-24):
- Box, "Configuring a firewall for Box applications and services":
  https://support.box.com/hc/en-us/articles/33552262497683 (core *.box.com, *.app.box.com,
  *.box.net, *.boxcdn.net, *.boxcloud.com; login account.box.com, sso.services.box.net,
  {subdomain}.account.box.com; uploads upload.app.box.com; Box Notes notes.services.box.com)
- Duke OIT, Shibboleth IdP for service providers: https://authentication.oit.duke.edu/manager/documentation
  (IdP shib.oit.duke.edu); Duke MFA is Duo (https://oit.duke.edu/service/duo/), whose prompt is
  served from api-<id>.duosecurity.com.

Left out on purpose: Box uploads (upload.*.box.com, fupload-*) and Box Notes
(notes.services.box.com): an exam pane needs to read files, not to write or chat.

Companion entries use the apps' allow-list form: an exact host ("account.box.com", which
also covers "www.account.box.com") or "*.base" (base and every subdomain). "{tenant}" is
the matched host's tenant label (duke in duke.app.box.com); an entry with {tenant} is left
out when the host has none (app.box.com).
"""
from __future__ import annotations

from dataclasses import dataclass

WILDCARD = "*."
# The most sites the lock is handed (allowed websites + companions): the Windows app reads at
# most 64 entries. The allowed websites themselves are at most schemas.MAX_WEBSITES (20), so
# only companions are ever cut, last ones first.
MAX_LOCK_SITES = 64


@dataclass(frozen=True)
class Service:
    id: str
    name: str
    # host patterns: an exact host, or "*.base" for the hosts UNDER base (not base itself)
    matches: tuple[str, ...]
    companions: tuple[str, ...]
    # more specific services win the display name (Duke Box over Box)
    rank: int = 0


# 2026-10-30: empty on purpose. Duke Box used to be listed here (sign-in, Duo, scripts); that
# widened the frame allow-list and never worked, because Box sign-in refuses framing
# (X-Frame-Options SAMEORIGIN). A Box link now opens in its own pane, see app.box_profiles.
SERVICES: tuple[Service, ...] = ()

# Labels of box.com hosts that are Box's own, not a tenant ("www.box.com", "app.box.com").
_NOT_TENANT = {"www", "app", "account", "api", "upload", "dl", "public", "cdn01", "notes",
               "services", "developer", "support", "community", "ent", "m", "sso"}


def _host_of(entry: str) -> str:
    e = entry.strip().lower().rstrip(".")
    return e[len(WILDCARD):] if e.startswith(WILDCARD) else e


def _matches(host: str, pattern: str) -> bool:
    if pattern.startswith(WILDCARD):
        return host.endswith("." + pattern[len(WILDCARD):])
    return host == pattern or host == "www." + pattern


def _tenant(host: str) -> str | None:
    """duke.app.box.com / duke.box.com / duke.account.box.com -> "duke"."""
    labels = host.split(".")
    if len(labels) >= 3 and labels[-2:] == ["box", "com"]:
        first = labels[0]
        if first not in _NOT_TENANT:
            return first
    return None


def services_for(entry: str) -> list[Service]:
    host = _host_of(entry)
    return [s for s in SERVICES if any(_matches(host, p) for p in s.matches)]


def companions_for(entry: str) -> tuple[str | None, list[str]]:
    """(display name of the most specific service, its companion entries) for one allowed
    entry; (None, []) when it matches no known service."""
    found = services_for(entry)
    if not found:
        return None, []
    host = _host_of(entry)
    tenant = _tenant(host)
    out: list[str] = []
    for s in sorted(found, key=lambda s: s.rank):
        for c in s.companions:
            if "{tenant}" in c:
                if tenant is None:
                    continue
                c = c.replace("{tenant}", tenant)
            if c != host and c not in out:
                out.append(c)
    return max(found, key=lambda s: s.rank).name, out


def companions(websites: list[str]) -> list[dict]:
    """[{"for": entry, "name": "Duke Box", "sites": [...]}] for the entries that match a
    service; companion sites already on the list (or given for an earlier entry) are left
    out, and none past MAX_LOCK_SITES in all."""
    have = set(websites)
    room = MAX_LOCK_SITES - len(websites)
    out: list[dict] = []
    for w in websites:
        name, sites = companions_for(w)
        sites = [c for c in sites if c not in have][:max(0, room)]
        if name and sites:
            have.update(sites)
            room -= len(sites)
            out.append({"for": w, "name": name, "sites": sites})
    return out


def allowed_sites(websites: list[str]) -> list[str]:
    """What the exam lock opens: the allowed websites, then their companions."""
    out = list(websites)
    for c in companions(websites):
        out.extend(c["sites"])
    return out[:MAX_LOCK_SITES]
