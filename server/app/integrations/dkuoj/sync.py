"""Background sync with DKU OJ (contract §5).

Every DKUOJ_SYNC_SECONDS: list changed FairPlay assignments (create / update the
FairPlay exam, its classes, roster and editor accounts), fetch each package from 24 h
before its start (425 -> retry when DKU OJ says), download and check its assets, and
pull released results of ended exams. Every few seconds: send due outbox rows. One
worker runs it at a time (row lock in oj_state with an expiry, renewed every tick).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, false, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import ClassMember, Exam, SchoolClass, User
from ...schemas import MAX_WEBSITE_LINKS, MAX_WEBSITES, clean_website_links, parse_website_entry
from ..sanitize import clean_markdown
from ..site_policy import filter_sites
from ...services import invalidate_exam, new_id
from ...store import get_store
from ...timeutil import iso, parse_iso, utcnow
from .adapter import AdapterError, asset_names, questions_from_package
from .client import DkuojClient, OjError
from .guard import exam_guard
from .models import OjAsset, OjEditor, OjRosterEntry, OjState

log = logging.getLogger("proctora.dkuoj")

IMPORTED_PASSWORD = "!dkuoj-import"     # not a pbkdf2 hash: verify_password() always fails
OWNER_USERNAME = "__dkuoj__"            # owner of an exam that lists no editor (never signs in)
PACKAGE_LEAD = timedelta(hours=24)
RESULTS_FOR = timedelta(days=60)        # poll released results (regrades) this long after the end
MAX_ASSET_BYTES = 10 * 1024 * 1024
MAX_EXAM_ASSET_BYTES = 50 * 1024 * 1024
MAX_PAGES = 200
LOCK_NAME = "runner"
LOCK_TTL = timedelta(seconds=60)
DELIVERY_TICK_S = 5.0
CURSOR = "exams_cursor"
# Tests switch the background loop off and drive run_once themselves.
BACKGROUND = True


# ---------------------------------------------------------------- one-runner lock
async def acquire_lock(session: AsyncSession, holder: str, now: datetime, ttl: timedelta = LOCK_TTL) -> bool:
    """Take or renew the runner lock. True = this worker runs the loop now."""
    res = await session.execute(
        update(OjState).where(OjState.name == LOCK_NAME,
                              (OjState.holder == holder) | (OjState.expires_at.is_(None)) | (OjState.expires_at < now))
        .values(holder=holder, expires_at=now + ttl))
    await session.commit()
    if res.rowcount == 1:
        return True
    session.add(OjState(name=LOCK_NAME, holder=holder, expires_at=now + ttl))
    try:
        await session.commit()
        return True
    except IntegrityError:
        await session.rollback()
        return False


async def release_lock(session: AsyncSession, holder: str) -> None:
    await session.execute(update(OjState).where(OjState.name == LOCK_NAME, OjState.holder == holder)
                          .values(holder=None, expires_at=None))
    await session.commit()


async def get_state(session: AsyncSession, name: str) -> str | None:
    row = await session.get(OjState, name)
    return row.value if row else None


async def set_state(session: AsyncSession, name: str, value: str | None) -> None:
    row = await session.get(OjState, name)
    if row is None:
        session.add(OjState(name=name, value=value))
    else:
        row.value = value
    await session.commit()


# ---------------------------------------------------------------- helpers
def _time(v) -> datetime | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        return parse_iso(v)
    except ValueError:
        return None


def _name(p: dict) -> str:
    n = " ".join(x for x in (str(p.get("first_name") or "").strip(), str(p.get("last_name") or "").strip()) if x)
    return (n or str(p.get("username") or "")).strip()[:200]


def _netid(p: dict) -> str | None:
    u = str(p.get("username") or "").strip()
    return u[:64] if u and not u.startswith("__") and ":" not in u else None


def extra_close_minutes(e: Exam) -> int:
    """Minutes added with "Add time" while the exam ran (exam_control.extend_exam): the
    close time is DKU OJ's end time plus these (source.closes_at)."""
    try:
        return max(0, int((e.external_meta or {}).get("extra_close_minutes") or 0))
    except (TypeError, ValueError):
        return 0


def placeholder_username(netid: str) -> str:
    """The username of an account that never set up: "oj:<netid>" can't be typed as a
    username (setup usernames are [a-z0-9._-]) and its password is unusable."""
    return f"oj:{netid}"[:64]


async def legacy_account(session: AsyncSession, netid: str) -> User | None:
    """An older account named like the NetID that has no netid yet, but ONLY if the bridge
    made or used it before: the v1 sync matched by username, creating accounts with
    username = NetID and the unusable IMPORTED_PASSWORD, or taking an existing account,
    which then has roster / editor rows. A normal FairPlay account that only happens to
    have the same username (e.g. the dev server's demo alice/bob/carol, with real
    passwords) is never adopted: the NetID gets its own "oj:<netid>" account instead."""
    if not netid:
        return None
    u = (await session.execute(select(User).where(User.username == netid, User.netid.is_(None)))
         ).scalar_one_or_none()
    if u is None:
        return None
    if u.password_hash == IMPORTED_PASSWORD:
        return u
    used = (await session.execute(select(OjRosterEntry.id).where(OjRosterEntry.user_id == u.id).limit(1))).first() \
        or (await session.execute(select(OjEditor.id).where(OjEditor.user_id == u.id).limit(1))).first()
    return u if used else None


async def find_by_netid(session: AsyncSession, netid: str) -> User | None:
    """The account of a NetID. Older databases: a v1 bridge account (legacy_account) is
    linked to it now (once)."""
    u = (await session.execute(select(User).where(User.netid == netid))).scalar_one_or_none()
    if u is not None:
        return u
    u = await legacy_account(session, netid)
    if u is not None:
        u.netid = netid
        await session.flush()
    return u


async def ensure_user(session: AsyncSession, person: dict, role: str) -> User | None:
    """Match by netid (= DKU OJ username), or a v1 bridge account (legacy_account); create
    a missing one with the placeholder username and an unusable password (it can't sign
    in until the student sets up). An existing account with another role is left alone
    (None)."""
    netid = _netid(person)
    if netid is None:
        return None
    u = (await session.execute(select(User).where(User.netid == netid))).scalar_one_or_none()
    if u is None:
        legacy = await legacy_account(session, netid)
        if legacy is not None:
            if legacy.role != role:
                log.warning("DKU OJ %s %s is a FairPlay %s here; skipped", role, netid, legacy.role)
                return None
            legacy.netid = netid
            u = legacy
            await session.flush()
    if u is None:
        u = User(username=placeholder_username(netid), netid=netid, name=_name(person) or netid, role=role,
                 password_hash=IMPORTED_PASSWORD, created_at=utcnow())
        session.add(u)
        try:
            await session.flush()
        except IntegrityError:          # created by a parallel request
            await session.rollback()
            u = (await session.execute(select(User).where(User.netid == netid))).scalar_one_or_none()
            if u is None:
                return None
    if u.role != role:
        log.warning("DKU OJ %s %s is a FairPlay %s here; skipped", role, netid, u.role)
        return None
    name = _name(person)
    if u.password_hash == IMPORTED_PASSWORD and name and u.name != name:
        u.name = name
    return u


async def _owner(session: AsyncSession) -> User:
    u = (await session.execute(select(User).where(User.username == OWNER_USERNAME))).scalar_one_or_none()
    if u is None:
        u = User(username=OWNER_USERNAME, name="DKU OJ", role="system", password_hash="!", created_at=utcnow())
        session.add(u)
        await session.flush()
    return u


def parse_sites(websites, website_links, key=None) -> tuple[list[str], list[dict], list[dict]]:
    """Allowed websites from DKU OJ (the exam list's settings, or POST /exams/<key>/settings)
    -> (hosts, start pages, refused [{"site", "reason"}]).
    2026-10-24: `websites` are host names or full links ("https://duke.app.box.com/folder/0",
    optionally after a title); `website_links` [{url, title}] more start pages. A link's host
    goes on the list (every page of it allowed), the link becomes a start page."""
    sites, unusable, links = [], [], []
    raw_links = [x for x in website_links or [] if isinstance(x, dict)][:MAX_WEBSITE_LINKS]
    entries = [((w.get("url"), w.get("title")) if isinstance(w, dict) else (w, None))
               for w in websites or []]
    entries += [(x.get("url"), x.get("title")) for x in raw_links]
    for w, title in entries:
        try:
            h, link = parse_website_entry(str(w or ""))
        except ValueError as exc:
            log.warning("DKU OJ exam %s: website %r skipped", key, w)
            unusable.append({"site": str(w)[:200], "reason": str(exc)[:200]})
            continue
        if h not in sites:
            sites.append(h)
        if link is not None:
            if isinstance(title, str) and title.strip():
                link["title"] = title
            links.append(link)
    # Security review 2026-10-24: FairPlay's own policy for sites an external source opens
    # in the lock (app.integrations.site_policy); the dropped ones go to websites_blocked.
    sites, blocked = filter_sites(sites[:MAX_WEBSITES])
    links = clean_website_links(links, sites)
    blocked = (unusable + blocked)[:40]
    if blocked:
        log.warning("DKU OJ exam %s: websites refused by FairPlay: %s", key,
                    ", ".join(b["site"] for b in blocked))
    return sites, links, blocked


def upload_days_of(v) -> int:
    """upload_opens_days from DKU OJ: a whole number 0..7, anything else 0."""
    if isinstance(v, bool):
        return 0
    try:
        days = int(v or 0)
    except (TypeError, ValueError):
        return 0
    return days if 0 <= days <= 7 else 0


def _settings(item: dict) -> dict:
    s = item.get("settings") or {}
    calc = s.get("calculator")
    sites, links, blocked = parse_sites(s.get("websites"), s.get("website_links"), item.get("key"))
    return {"calculator": calc if calc in ("off", "scientific", "graphing") else "off",
            "open_book": bool(s.get("open_book")), "websites": sites, "websites_blocked": blocked,
            "website_links": links,
            # 2026-10-24: open book: students may upload this many days before start_time
            "upload_opens_days": upload_days_of(s.get("upload_opens_days")),
            "require_strong_lock": bool(s.get("strong_lock")),
            "resume_needs_approval": s.get("resume_needs_approval") is not False,
            "box_link": None}       # 2026-10-30: only ever set in FairPlay


# 2026-10-24: settings set in FairPlay for a DKU OJ exam (POST /exams/<key>/settings, v2 §3),
# kept in external_meta[FAIRPLAY_SETTINGS]: {"calculator": ..., "open_book": ..., "strong_lock": ...,
# "resume_needs_approval": ..., "upload_opens_days": n, "websites": [...], "website_links": [...],
# "websites_blocked": [...], "websites_input": [...], "updated_at": iso}, each field only when set.
# A field set here wins over the same field in the exam list (every later sync applies it again);
# sending it as null hands it back to DKU OJ's value (the exam list's, or the default).
# 2026-10-24 (§11): DKU OJ keeps no FairPlay settings any more; its exam list has no "settings"
# and every setting comes through POST /exams/<key>/settings.
FAIRPLAY_SETTINGS = "fairplay_settings"
OVERRIDABLE = ("calculator", "open_book", "websites", "strong_lock", "resume_needs_approval", "upload_opens_days",
               "box_link")
# setting name in the API -> Exam attribute
EXAM_FIELD = {"calculator": "calculator", "open_book": "open_book", "strong_lock": "require_strong_lock",
              "resume_needs_approval": "resume_needs_approval", "upload_opens_days": "upload_opens_days",
              "box_link": "box_link"}
PENDING_PREFIX = "pending:"     # oj_state rows: settings for an exam not synced yet (§11)


def apply_fairplay_settings(e: Exam, meta: dict) -> None:
    """Put the FairPlay-set fields (FAIRPLAY_SETTINGS) on the exam, over the synced ones."""
    ov = meta.get(FAIRPLAY_SETTINGS)
    if not isinstance(ov, dict):
        return
    if "calculator" in ov:
        e.calculator = ov["calculator"] if ov["calculator"] in ("off", "scientific", "graphing") else "off"
    for f in ("open_book", "strong_lock", "resume_needs_approval"):
        if f in ov:
            setattr(e, EXAM_FIELD[f], bool(ov[f]))
    if "upload_opens_days" in ov:
        e.upload_opens_days = upload_days_of(ov.get("upload_opens_days"))
    if "box_link" in ov:
        from ...box_profiles import checked_link
        e.box_link = checked_link(ov["box_link"])[0]
    if isinstance(ov.get("websites"), list):
        e.websites = [w for w in ov["websites"] if isinstance(w, str)][:MAX_WEBSITES]
        e.website_links = clean_website_links(ov.get("website_links") or [], e.websites)
        meta["websites_blocked"] = [b for b in ov.get("websites_blocked") or [] if isinstance(b, dict)][:40]


async def take_pending(session: AsyncSession, e: Exam, meta: dict, editor_netids: set | None = None) -> tuple | None:
    """§11: settings DKU OJ saved for this key before FairPlay had the exam: they become its
    FairPlay settings now, and the pending row goes. Returns the pending (review, updated_at)
    for the caller to apply last (apply_review replaces external_meta), or None."""
    import json as _json
    row = await session.get(OjState, PENDING_PREFIX + (e.external_key or ""))
    if row is None:
        return None
    try:
        pending = _json.loads(row.value or "{}")
    except ValueError:
        pending = {}
    if not isinstance(pending, dict):
        await session.delete(row)
        return None
    editors = {n.casefold() for n in (editor_netids or set())}
    saver = pending.pop("saved_by", None)
    if not editors:
        log.warning("pending settings for exam %s kept: no editors known to check saver %r", e.external_key, saver)
        return None
    if saver is None:
        log.warning("pending settings for exam %s have no saver (saved before the check); applied", e.external_key)
    else:
        norm = _netid({"username": saver}) if isinstance(saver, str) else None
        if norm is None or norm.casefold() not in editors:
            log.warning("pending settings for exam %s rejected: saved by %r, not an editor", e.external_key, saver)
            await session.delete(row)
            return None
    await session.delete(row)
    review = pending.pop("review", None), pending.pop("review_updated_at", None)
    if not e.external_frozen_version:
        have = dict(meta.get(FAIRPLAY_SETTINGS) or {})
        have.update({k: v for k, v in pending.items() if k in OVERRIDABLE or k in (
            "website_links", "websites_blocked", "websites_input", "updated_at")})
        meta[FAIRPLAY_SETTINGS] = have
    return review if review[0] is not None else None


def _duration_min(item: dict) -> int:
    tl = item.get("time_limit_seconds")
    if isinstance(tl, (int, float)) and tl > 0:
        return max(1, math.ceil(tl / 60))
    start, end = _time(item.get("start_time")), _time(item.get("end_time"))
    if start and end and end > start:
        return max(1, math.ceil((end - start).total_seconds() / 60))
    return 60


META_KEYS = ("course", "classes", "audience", "start_time", "end_time", "time_limit_seconds", "results_hidden",
             "package_version", "updated_at", "settings")
KEEP_META = ("results_cursor", "package_retry_at", "package_error", "package_failed_version",
             "description_md", "assets", "extra_close_minutes", "review_updated_at", "websites_blocked",
             "fairplay_settings")      # 2026-10-24: FAIRPLAY_SETTINGS


def apply_review(e: Exam, value, updated_at) -> bool:
    """2026-10-24: DKU OJ's "Student review after hand-in" (app.review), from the exam list's
    settings or the direct POST /exams/<key>/review. Both come from DKU OJ, so its newest value
    wins: one older than what FairPlay has (a listing fetched just before the professor
    changed it) is ignored. Applies also to running and closed exams. True when applied."""
    from ...review import LEVELS
    if value not in LEVELS:
        return False
    meta = dict(e.external_meta or {})
    have, new = _time(meta.get("review_updated_at")), _time(updated_at)
    if have is not None and (new is None or new < have):
        return False
    e.review_after_handin = value
    if new is not None:
        meta["review_updated_at"] = iso(new)
    e.external_meta = meta
    return True


# ---------------------------------------------------------------- listing -> exam
async def apply_listing(session: AsyncSession, item: dict, cfg, now: datetime) -> Exam | None:
    """Create or update the FairPlay exam for one listing entry (no commit)."""
    key = item.get("key")
    if not isinstance(key, str) or not key:
        return None
    e = (await session.execute(select(Exam).where(Exam.external_key == key, Exam.source == "dkuoj")
                               .with_for_update())).scalar_one_or_none()
    if item.get("removed"):
        # A removed entry carries only key, name and times (and only in incremental
        # polls): mark the exam, keep everything else (editors still see it).
        if e is not None:
            e.external_removed = True
            e.external_synced_at = now
            e.updated_at = now
            await revoke_authority(session, e)
            await session.flush()
        return e
    editors = [u for u in [await ensure_user(session, p, "teacher") for p in item.get("editors") or []
                           if isinstance(p, dict)] if u is not None]
    students = []
    from .isolation import audience_valid
    roster_names: dict[int, dict] = {}
    for p in (item.get("roster") or []) if audience_valid(item) else []:
        if isinstance(p, dict):
            u = await ensure_user(session, p, "student")
            if u is not None:
                try:
                    extra = max(0, int(p.get("extra_time_seconds") or 0))
                except (TypeError, ValueError):
                    extra = 0
                students.append((u, extra))
                roster_names[u.id] = {"first_name": str(p.get("first_name") or "").strip()[:200] or None,
                                      "last_name": str(p.get("last_name") or "").strip()[:200] or None}
    await session.flush()
    meta = {k: item.get(k) for k in META_KEYS}
    meta["url"] = f"{cfg.url}/contest/{key}"
    if e is None:
        owner = editors[0] if editors else await _owner(session)
        e = Exam(id=new_id("e"), teacher_id=owner.id, status="draft", created_at=now, source="dkuoj",
                 external_key=key, delivery="computer", mode="questions", questions=[], question_count=0,
                 total_points=0.0, title="", duration_min=60, allowed_apps=[], allowed_domains=[],
                 external_meta={})
        session.add(e)
    old_end = _time((e.external_meta or {}).get("end_time"))
    for k in KEEP_META:
        if k in (e.external_meta or {}):
            meta[k] = e.external_meta[k]
    # §11: settings saved before the exam arrived (before external_meta is set: the query may flush)
    pending_review = await take_pending(session, e, meta, {n for n in (_netid(p) for p in item.get("editors") or [] if isinstance(p, dict)) if n})
    e.external_meta = meta
    e.external_removed = bool(item.get("removed"))
    e.external_synced_at = now
    if not e.external_frozen_version:
        # Not started yet: DKU OJ's name, times and settings win. Once a student started,
        # the exam runs as it started (only roster, editors and times for results change).
        e.title = str(item.get("name") or key)[:200]
        e.course = str((item.get("course") or {}).get("name") or "")[:64]
        e.scheduled_at = _time(item.get("start_time"))
        # minutes added with "Add time" stay added (exam_control.extend_exam)
        e.duration_min = _duration_min(item) + extra_close_minutes(e)
        if isinstance(item.get("settings"), dict):
            # An exam list that still carries settings (DKU OJ before §11).
            tools = _settings(item)
            meta["websites_blocked"] = tools.pop("websites_blocked")
            for f, v in tools.items():
                setattr(e, f, v)
        apply_fairplay_settings(e, meta)     # 2026-10-24: set in FairPlay -> wins
    oj_settings = item.get("settings") or {}
    if isinstance(oj_settings, dict) and "review" in oj_settings:
        apply_review(e, oj_settings.get("review"), oj_settings.get("review_updated_at"))
    if pending_review is not None:
        apply_review(e, *pending_review)
    e.updated_at = now
    await session.flush()
    if editors and e.teacher_id not in {u.id for u in editors}:
        e.teacher_id = editors[0].id     # the owner is always a current editor
    await _replace(session, OjEditor, e.id, {u.id: {} for u in editors})
    confirmed = audience_valid(item)
    if confirmed:
        await _replace(session, OjRosterEntry, e.id, {u.id: {"extra_time_seconds": x, **roster_names.get(u.id, {})}
                                                           for u, x in students})
    await _classes(session, e, item, [u for u, _ in students])
    new_end = _time(meta.get("end_time"))
    if e.status == "live" and old_end is not None and new_end is not None and new_end > old_end:
        e.moved_attempts = await follow_end_time(session, e, new_end + timedelta(minutes=extra_close_minutes(e)))
    return e


async def revoke_authority(session, exam):
    await _replace(session, OjEditor, exam.id, {})
    if exam.class_id is not None:
        await session.execute(delete(ClassMember).where(ClassMember.class_id == exam.class_id))


async def follow_end_time(session: AsyncSession, e: Exam, new_end: datetime) -> list[str]:
    """2026-10-23: DKU OJ moved the end time later while the exam runs. Attempts whose
    deadline was capped at the old end time get the new cap (start + duration + extra,
    never past the new end + extra); a deadline is never made shorter here. Returns the
    attempt ids changed (their cached deadline is dropped after the commit)."""
    from ...models import Attempt
    extras = dict((await session.execute(select(OjRosterEntry.user_id, OjRosterEntry.extra_time_seconds)
                                         .where(OjRosterEntry.exam_id == e.id))).all())
    rows = (await session.execute(select(Attempt.id, Attempt.student_id, Attempt.started_at, Attempt.deadline_at)
                                  .where(Attempt.exam_id == e.id, Attempt.status == "in_progress"))).all()
    moved = []
    for aid, sid, started, deadline in rows:
        extra = timedelta(seconds=max(0, int(extras.get(sid) or 0)))
        want = min(started + timedelta(minutes=e.duration_min) + extra, new_end + extra)
        if want > deadline:
            await session.execute(update(Attempt).where(Attempt.id == aid, Attempt.status == "in_progress")
                                  .values(deadline_at=want))
            moved.append(aid)
    return moved


async def _replace(session: AsyncSession, model, exam_id: str, wanted: dict[int, dict]) -> None:
    have = {r.user_id: r for r in (await session.execute(select(model).where(model.exam_id == exam_id))).scalars()}
    revoked = []
    for uid, row in have.items():
        if uid not in wanted:
            if model is OjRosterEntry:
                if row.revoked_at is None:
                    row.revoked_at = utcnow()
                    revoked.append(uid)
            else:
                await session.delete(row)
    for uid, values in wanted.items():
        if model is OjRosterEntry:
            values = {**values, "revoked_at": None}
        if uid in have:
            for k, v in values.items():
                setattr(have[uid], k, v)
        else:
            session.add(model(exam_id=exam_id, user_id=uid, **values))
    if revoked:
        from ...models import Attempt
        ids = (await session.execute(select(Attempt.id).where(
            Attempt.exam_id == exam_id, Attempt.student_id.in_(revoked), Attempt.status == "in_progress"))).scalars().all()
        if ids:
            await session.execute(update(Attempt).where(Attempt.id.in_(ids)).values(exited_at=utcnow()))
            e = await session.get(Exam, exam_id)
            e.revoked_attempts = ids
    await session.flush()


async def _classes(session: AsyncSession, e: Exam, item: dict, students: list[User]) -> None:
    """Internal class labels only: an exam roster never establishes class-wide membership."""
    from ...routes_classes import new_join_code
    first = None
    owner = await _owner(session)
    old = await session.get(SchoolClass, e.class_id) if e.class_id is not None else None
    if old is not None and (old.external_ref or "").startswith("dkuoj:"):
        old.teacher_id = owner.id
        await session.execute(delete(ClassMember).where(ClassMember.class_id == old.id))
    classes = [c for c in item.get("classes") or [] if isinstance(c, dict) and isinstance(c.get("id"), int)]
    for c in classes:
        k = (await session.execute(select(SchoolClass).where(SchoolClass.external_ref == f"dkuoj:{c['id']}"))).scalars().first()
        if k is None:
            k = SchoolClass(teacher_id=owner.id, name=str(c.get("name") or f"DKU OJ class {c['id']}")[:120],
                            join_code=new_join_code(), external_ref=f"dkuoj:{c['id']}", created_at=utcnow(),
                            term="DKU OJ")
            session.add(k)
            await session.flush()
        elif c.get("name") and k.name != str(c["name"])[:120]:
            k.name = str(c["name"])[:120]
        k.teacher_id = owner.id
        await session.execute(delete(ClassMember).where(ClassMember.class_id == k.id))
        first = first or k
    e.class_id = first.id if first else None
    await session.flush()


async def mark_removed(sessionmaker, active: set[str], since: datetime | None) -> list[str]:
    """§11: DKU OJ's first page lists the keys of ALL its FairPlay exams that end after
    `since` (`active_keys`, `active_since`). A DKU OJ exam of ours in that window whose key is
    not listed was switched off (or deleted) there: marked removed, as a `removed` entry did.
    One listed again is no longer removed. Exams that ended before the window are left alone
    (DKU OJ stops listing them, they are not removed). Returns the exam ids changed."""
    changed = []
    async with sessionmaker() as session:
        rows = (await session.execute(select(Exam).where(Exam.source == "dkuoj"))).scalars().all()
        for e in rows:
            key = e.external_key or ""
            if key in active:
                if e.external_removed:
                    e.external_removed = False
                    e.updated_at = utcnow()
                    changed.append(e.id)
                continue
            end = _time((e.external_meta or {}).get("end_time"))
            if e.external_removed or since is None or end is None or end < since + timedelta(days=1):
                continue
            e.external_removed = True
            e.updated_at = utcnow()
            await revoke_authority(session, e)
            changed.append(e.id)
        await session.commit()
    for exam_id in changed:
        await invalidate_exam(get_store(), exam_id)
    if changed:
        log.info("DKU OJ exam list: %d exam(s) switched off or back on", len(changed))
    return changed


async def apply_item(sessionmaker, item: dict, cfg, now: datetime) -> bool:
    """Apply one listing entry and commit; drops the exam's cached view. True when applied."""
    async with sessionmaker() as session:
        e = await apply_listing(session, item, cfg, now)
        await session.commit()
        if e is None:
            return False
        await invalidate_exam(get_store(), e.id)
        moved = getattr(e, "moved_attempts", None)
        if moved:      # the end time moved later: heartbeats re-read the deadline
            from ...services import attempt_key
            await get_store().delete(*[attempt_key(a) for a in moved])
        revoked = getattr(e, "revoked_attempts", None)
        if revoked:
            from ...services import attempt_key
            await get_store().delete(*[key for aid in revoked for key in (attempt_key(aid), f"entry:{aid}", f"abt:{aid}")])
            for aid in revoked:
                await get_store().hdel(f"ab:{e.id}", aid)
        return True


async def sync_exams(sessionmaker, client: DkuojClient, cfg, now: datetime, guard: bool = True,
                     full: bool = False, targets: set[str] | None = None) -> int:
    """Page through changed exams since the stored cursor. Returns how many were applied."""
    from .isolation import verify_installation
    await verify_installation(sessionmaker, client, cfg)
    async with sessionmaker() as session:
        cursor = await get_state(session, CURSOR)
    if full:
        cursor = None
    newest = cursor
    n = 0
    page = 1
    active: set[str] | None = None
    active_since = None
    complete = False
    seen = set()
    for _ in range(MAX_PAGES):
        data = await client.list_exams(cursor, page)
        if page == 1 and isinstance(data.get("active_keys"), list):
            if any(not isinstance(k, str) or not k for k in data["active_keys"]):
                raise OjError("DKU OJ active exam keys are invalid", 422)
            active = {k for k in data["active_keys"] if isinstance(k, str)}
            active_since = _time(data.get("active_since"))
        if not isinstance(data.get("exams"), list):
            raise OjError("DKU OJ exam listing has no authoritative exam list", 422)
        for item in data["exams"]:
            if not isinstance(item, dict):
                raise OjError("DKU OJ exam listing contains an invalid entry", 422)
            if not isinstance(item.get('key'), str) or not item['key']:
                raise OjError("DKU OJ exam listing contains an invalid key", 422)
            seen.add(item['key'])
            if targets and item['key'] not in targets:
                continue
            if guard and isinstance(item.get("key"), str) and item["key"]:
                async with exam_guard(item["key"]):
                    n += await apply_item(sessionmaker, item, cfg, now)
            else:
                n += await apply_item(sessionmaker, item, cfg, now)
            up = item.get("updated_at")
            if isinstance(up, str) and (newest is None or _time(up) and _time(up) > (_time(newest) or datetime.min)):
                newest = up
        nxt = data.get("next_page")
        if not nxt:
            complete = True
            break
        page = int(nxt) if isinstance(nxt, (int, str)) and str(nxt).isdigit() else page + 1
    if complete and active is not None and not targets:
        await mark_removed(sessionmaker, active, active_since)
    if complete and full and targets:
        for key in targets - seen:
            await apply_item(sessionmaker, {"key": key, "removed": True}, cfg, now)
    if newest != cursor and not targets:
        async with sessionmaker() as session:
            await set_state(session, CURSOR, newest)
    return n


# ---------------------------------------------------------------- packages
def package_due(e: Exam, now: datetime) -> bool:
    meta = e.external_meta or {}
    if e.external_removed or e.external_frozen_version:
        return False
    start = _time(meta.get("start_time"))
    if start is None or now < start - PACKAGE_LEAD:
        return False
    retry = _time(meta.get("package_retry_at"))
    if retry is not None and now < retry:
        return False
    if meta.get("package_failed_version") and meta.get("package_failed_version") == meta.get("package_version"):
        return False        # DKU OJ refused this version (e.g. 422 too large): wait for a new one
    return meta.get("package_version") != e.external_version or not e.questions


async def fetch_packages(sessionmaker, client: DkuojClient, now: datetime, only: str | None = None,
                         guard: bool = True) -> int:
    async with sessionmaker() as session:
        exams = (await session.execute(select(Exam).where(Exam.source == "dkuoj", Exam.external_removed == false(),
                                                          Exam.external_frozen_version.is_(None)))).scalars().all()
        due = [(e.id, e.external_key, e.external_version if e.questions else None) for e in exams
               if package_due(e, now) and (only is None or e.external_key == only)]
    applied = 0
    for exam_id, key, have in due:
        try:
            if guard and key:
                async with exam_guard(key):
                    applied += bool(await _fetch_package(sessionmaker, client, exam_id, key, have, now))
            elif await _fetch_package(sessionmaker, client, exam_id, key, have, now):
                applied += 1
        except (OjError, AdapterError) as err:
            msg = getattr(err, "message", None) or str(err)
            log.warning("DKU OJ package of %s not applied: %s", key, msg)
            refused = isinstance(err, OjError) and not err.retryable
            async with sessionmaker() as session:
                e = await session.get(Exam, exam_id)
                if e is not None:
                    meta = {**(e.external_meta or {}), "package_error": msg[:300],
                            "package_retry_at": iso(now + timedelta(minutes=2))}
                    if refused:     # 4xx (e.g. 422 package over 5 MB): not retried for this version
                        meta["package_failed_version"] = meta.get("package_version")
                    e.external_meta = meta
                    await session.commit()
    return applied


async def _fetch_package(sessionmaker, client: DkuojClient, exam_id: str, key: str, have: str | None,
                         now: datetime) -> bool:
    status, pkg, retry_after = await client.package(key, have)
    if status in (304, 404, 425):
        async with sessionmaker() as session:
            e = await session.get(Exam, exam_id)
            if e is None:
                return False
            meta = dict(e.external_meta or {})
            if status == 425:
                wait = retry_after if retry_after is not None else 3600
                meta["package_retry_at"] = iso(now + timedelta(seconds=max(30, min(wait, 86400))))
            elif status == 404:
                meta["package_error"] = "DKU OJ says this is not a FairPlay exam"
                meta["package_retry_at"] = iso(now + timedelta(minutes=10))
            else:
                meta.pop("package_retry_at", None)
                meta["package_version"] = e.external_version     # 304: what we have is current
            e.external_meta = meta
            await session.commit()
        return False
    assert pkg is not None
    from .isolation import audience_valid
    if not audience_valid(pkg):
        raise AdapterError("the package audience is not explicitly confirmed")
    version = str(pkg.get("package_version") or "")[:32]
    if not version:
        raise AdapterError("the package has no package_version")
    async with sessionmaker() as session:
        e = await session.get(Exam, exam_id)
        if e is None or pkg['audience'] != (e.external_meta or {}).get('audience'):
            raise AdapterError("the package audience differs from the verified exam listing")
        topic = ((e.course or "").strip().lower()[:60] if e else "") or "general"
    questions = questions_from_package(pkg, topic)
    assets = asset_names(pkg)
    total_bytes = 0
    blobs: dict[str, tuple[bytes, str]] = {}
    async with sessionmaker() as session:
        stored = {a.name: a.sha256 for a in (await session.execute(
            select(OjAsset).where(OjAsset.exam_id == exam_id))).scalars()}
    for a in assets:
        if stored.get(a["name"]) == a["sha256"]:
            continue
        data, ctype = await client.asset(key, a["name"])
        if len(data) > MAX_ASSET_BYTES:
            raise AdapterError(f"asset {a['name']} is larger than 10 MB")
        if hashlib.sha256(data).hexdigest() != a["sha256"]:
            raise AdapterError(f"asset {a['name']} does not match its sha256")
        total_bytes += len(data)
        if total_bytes > MAX_EXAM_ASSET_BYTES:
            raise AdapterError("the exam's files are larger than 50 MB")
        blobs[a["name"]] = (data, a["content_type"] or ctype)
    async with sessionmaker() as session:
        try:
            total = float(pkg.get("total_points"))
        except (TypeError, ValueError):
            total = float(sum(float(q["points"]) for q in questions))
        e = await session.get(Exam, exam_id)
        meta = dict((e.external_meta if e else None) or {})
        meta.pop("package_retry_at", None)
        meta.pop("package_error", None)
        meta.pop("package_failed_version", None)
        meta["package_version"] = version
        meta["description_md"] = clean_markdown(str(pkg.get("description_md") or "")[:20000])
        meta["assets"] = [a["name"] for a in assets]
        # Only while nobody started: the first start freezes the version (same row lock).
        res = await session.execute(
            update(Exam).where(Exam.id == exam_id, Exam.external_frozen_version.is_(None)).values(
                questions=questions, question_count=len(questions), total_points=total,
                external_version=version, external_meta=meta, updated_at=now))
        if res.rowcount != 1:
            await session.rollback()
            return False
        names = {a["name"] for a in assets}
        await session.execute(delete(OjAsset).where(OjAsset.exam_id == exam_id, OjAsset.name.not_in(names)
                                                    if names else OjAsset.exam_id == exam_id))
        for name, (data, ctype) in blobs.items():
            await session.execute(delete(OjAsset).where(OjAsset.exam_id == exam_id, OjAsset.name == name))
            session.add(OjAsset(exam_id=exam_id, name=name, sha256=hashlib.sha256(data).hexdigest(),
                                content_type=(ctype or "application/octet-stream")[:100], size=len(data),
                                data=data, created_at=now))
        await session.commit()
    await invalidate_exam(get_store(), exam_id)
    log.info("DKU OJ package %s of %s applied (%d questions)", version, key, len(questions))
    return True


# ---------------------------------------------------------------- auto-open
async def open_due(sessionmaker, now: datetime) -> list[str]:
    """Open every DKU OJ exam whose package is applied once its scheduled start has come.
    Only exams still in draft: a closed exam is never reopened, an exam whose start moved into
    the future waits. The teacher's and the bridge's manual open keep working."""
    from fastapi import HTTPException
    from ...exam_control import open_exam
    from .setup import add_event
    opened: list[str] = []
    async with sessionmaker() as session:
        ids = (await session.execute(select(Exam.id).where(
            Exam.source == "dkuoj", Exam.status == "draft", Exam.external_removed == false(),
            Exam.scheduled_at.is_not(None), Exam.scheduled_at <= now))).scalars().all()
    for exam_id in ids:
        async with sessionmaker() as session:
            e = await session.get(Exam, exam_id)
            if e is None or e.status != "draft" or e.scheduled_at is None or e.scheduled_at > now:
                continue
            if not e.questions or not e.external_version:
                continue
            try:
                await open_exam(session, e, open_to="all")
            except HTTPException as err:
                msg = str(err.detail)[:300]
                if (e.external_meta or {}).get("auto_open_error") != msg:
                    log.info("DKU OJ exam %s not opened at its start: %s", e.external_key, msg)
                    e.external_meta = {**(e.external_meta or {}), "auto_open_error": msg}
                    await session.commit()
                continue
            if (e.external_meta or {}).get("auto_open_error"):
                e.external_meta = {k: v for k, v in e.external_meta.items() if k != "auto_open_error"}
            from .routes_v2 import _event_class
            class_id = _event_class(e)
            if class_id is not None:
                await add_event(session, class_id, "auto_opened", detail=f"exam {e.external_key}: scheduled start reached")
            await session.commit()
            log.info("DKU OJ exam %s opened at its scheduled start", e.external_key)
            opened.append(e.id)
    return opened


# ---------------------------------------------------------------- the loop
async def run_once(sessionmaker, cfg=None, now: datetime | None = None, deliver: bool = True,
                   client: DkuojClient | None = None) -> dict:
    """One full pass: listing, packages, released results, outbox. Errors of one step are
    logged and do not stop the others."""
    from .delivery import deliver_due
    from .results import sync_results
    now = now or utcnow()
    out: dict = {}
    own = client is None
    if cfg is None:
        from .source import SOURCE
        cfg = SOURCE.config
    client = client or DkuojClient.from_config(cfg)
    try:
        from .isolation import verify_installation
        try:
            await verify_installation(sessionmaker, client, cfg)
        except OjError as error:
            return {"installation": {"error": error.message}}
        for name, step in (("exams", lambda: sync_exams(sessionmaker, client, cfg, now)),
                           ("packages", lambda: fetch_packages(sessionmaker, client, now)),
                           ("open", lambda: open_due(sessionmaker, now)),
                           ("results", lambda: sync_results(sessionmaker, client, now)),
                           ("delivery", lambda: deliver_due(sessionmaker, client, cfg, now))):
            if name == "delivery" and not deliver:
                continue
            try:
                out[name] = await step()
            except OjError as err:
                out[name] = {"error": err.message}
                log.warning("DKU OJ %s step failed: %s", name, err.message)
            except Exception as err:  # noqa: BLE001
                out[name] = {"error": str(err)[:300]}
                log.exception("DKU OJ %s step failed", name)
        async with sessionmaker() as session:
            await set_state(session, "last_run", iso(now))
            errors = {k: v["error"] for k, v in out.items() if isinstance(v, dict) and "error" in v}
            await set_state(session, "last_error", "; ".join(f"{k}: {v}" for k, v in errors.items()) or None)
    finally:
        if own:
            await client.aclose()
    return out


async def run_forever() -> None:
    """Started from main.py's lifespan (registry.background_tasks) while DKU OJ is connected."""
    from .source import SOURCE
    from ...db import get_database
    from .delivery import deliver_due
    holder = f"{uuid.uuid4().hex[:12]}-{secrets.token_hex(4)}"
    last_full = 0.0
    loop = asyncio.get_running_loop()
    try:
        while True:
            try:
                db = get_database()
                async with db.sessionmaker() as session:
                    mine = await acquire_lock(session, holder, utcnow())
                if mine:
                    if loop.time() - last_full >= max(10, SOURCE.config.sync_seconds):
                        last_full = loop.time()
                        await run_once(db.sessionmaker, SOURCE.config)
                    else:
                        from .isolation import verify_installation
                        async with DkuojClient.from_config(SOURCE.config) as verifier:
                            await verify_installation(db.sessionmaker, verifier, SOURCE.config)
                        await open_due(db.sessionmaker, utcnow())
                        async with DkuojClient.from_config(SOURCE.config) as client:
                            await deliver_due(db.sessionmaker, client, SOURCE.config)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("DKU OJ loop")
            await asyncio.sleep(DELIVERY_TICK_S)
    finally:
        try:
            async with get_database().sessionmaker() as session:
                await release_lock(session, holder)
        except Exception:  # noqa: BLE001
            pass
