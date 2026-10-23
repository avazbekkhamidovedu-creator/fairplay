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

from ..models import ClassMember, Exam, SchoolClass, User
from ..schemas import normalize_website
from ..services import invalidate_exam, new_id
from ..store import get_store
from ..timeutil import iso, parse_iso, utcnow
from .adapter import AdapterError, asset_names, questions_from_package
from .client import DkuojClient, OjError
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


def _username(p: dict) -> str | None:
    u = str(p.get("username") or "").strip()
    return u[:64] if u and not u.startswith("__") else None


async def ensure_user(session: AsyncSession, person: dict, role: str) -> User | None:
    """Match by username (= DKU OJ username / NetID); create a missing one with an
    unusable password. An existing account with another role is left alone (None)."""
    username = _username(person)
    if username is None:
        return None
    u = (await session.execute(select(User).where(User.username == username))).scalar_one_or_none()
    if u is None:
        u = User(username=username, name=_name(person) or username, role=role,
                 password_hash=IMPORTED_PASSWORD, created_at=utcnow())
        session.add(u)
        try:
            await session.flush()
        except IntegrityError:          # created by a parallel request
            await session.rollback()
            u = (await session.execute(select(User).where(User.username == username))).scalar_one_or_none()
            if u is None:
                return None
    if u.role != role:
        log.warning("DKU OJ %s %s is a FairPlay %s here; skipped", role, username, u.role)
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


def _settings(item: dict) -> dict:
    s = item.get("settings") or {}
    calc = s.get("calculator")
    sites = []
    for w in s.get("websites") or []:
        try:
            h = normalize_website(str(w))
        except ValueError:
            log.warning("DKU OJ exam %s: website %r skipped", item.get("key"), w)
            continue
        if h not in sites:
            sites.append(h)
    return {"calculator": calc if calc in ("off", "scientific", "graphing") else "off",
            "open_book": bool(s.get("open_book")), "websites": sites[:20],
            "require_strong_lock": bool(s.get("strong_lock")),
            "resume_needs_approval": s.get("resume_needs_approval") is not False}


def _duration_min(item: dict) -> int:
    tl = item.get("time_limit_seconds")
    if isinstance(tl, (int, float)) and tl > 0:
        return max(1, math.ceil(tl / 60))
    start, end = _time(item.get("start_time")), _time(item.get("end_time"))
    if start and end and end > start:
        return max(1, math.ceil((end - start).total_seconds() / 60))
    return 60


META_KEYS = ("course", "classes", "start_time", "end_time", "time_limit_seconds", "results_hidden",
             "package_version", "updated_at", "settings")
KEEP_META = ("results_cursor", "package_retry_at", "package_error", "description_md", "assets")


# ---------------------------------------------------------------- listing -> exam
async def apply_listing(session: AsyncSession, item: dict, settings, now: datetime) -> Exam | None:
    """Create or update the FairPlay exam for one listing entry (no commit)."""
    key = item.get("key")
    if not isinstance(key, str) or not key:
        return None
    e = (await session.execute(select(Exam).where(Exam.oj_key == key, Exam.source == "dkuoj"))).scalar_one_or_none()
    if e is None and item.get("removed"):
        return None
    editors = [u for u in [await ensure_user(session, p, "teacher") for p in item.get("editors") or []
                           if isinstance(p, dict)] if u is not None]
    students = []
    for p in item.get("roster") or []:
        if isinstance(p, dict):
            u = await ensure_user(session, p, "student")
            if u is not None:
                try:
                    extra = max(0, int(p.get("extra_time_seconds") or 0))
                except (TypeError, ValueError):
                    extra = 0
                students.append((u, extra))
    await session.flush()
    meta = {k: item.get(k) for k in META_KEYS}
    meta["url"] = f"{settings.dkuoj_url}/contest/{key}"
    if e is None:
        owner = editors[0] if editors else await _owner(session)
        e = Exam(id=new_id("e"), teacher_id=owner.id, status="draft", created_at=now, source="dkuoj",
                 oj_key=key, delivery="computer", mode="questions", questions=[], question_count=0,
                 total_points=0.0, title="", duration_min=60, allowed_apps=[], allowed_domains=[],
                 oj_meta={})
        session.add(e)
    for k in KEEP_META:
        if k in (e.oj_meta or {}):
            meta[k] = e.oj_meta[k]
    e.oj_meta = meta
    e.oj_removed = bool(item.get("removed"))
    e.oj_synced_at = now
    if not e.oj_frozen_version:
        # Not started yet: DKU OJ's name, times and settings win. Once a student started,
        # the exam runs as it started (only roster, editors and times for results change).
        e.title = str(item.get("name") or key)[:200]
        e.course = str((item.get("course") or {}).get("name") or "")[:64]
        e.scheduled_at = _time(item.get("start_time"))
        e.duration_min = _duration_min(item)
        for f, v in _settings(item).items():
            setattr(e, f, v)
    e.updated_at = now
    await session.flush()
    if editors and e.teacher_id not in {u.id for u in editors}:
        e.teacher_id = editors[0].id     # the owner is always a current editor
    await _replace(session, OjEditor, e.id, {u.id: {} for u in editors})
    await _replace(session, OjRosterEntry, e.id, {u.id: {"extra_time_seconds": x} for u, x in students})
    await _classes(session, e, item, [u for u, _ in students])
    return e


async def _replace(session: AsyncSession, model, exam_id: str, wanted: dict[int, dict]) -> None:
    have = {r.user_id: r for r in (await session.execute(select(model).where(model.exam_id == exam_id))).scalars()}
    for uid, row in have.items():
        if uid not in wanted:
            await session.delete(row)
    for uid, values in wanted.items():
        if uid in have:
            for k, v in values.items():
                setattr(have[uid], k, v)
        else:
            session.add(model(exam_id=exam_id, user_id=uid, **values))
    await session.flush()


async def _classes(session: AsyncSession, e: Exam, item: dict, students: list[User]) -> None:
    """One FairPlay class per DKU OJ class (by its id), owned by the exam's owner. The
    roster carries no class per student, so members are added only when the exam has a
    single class. Who may take the exam is decided by the roster (access.member_of_exam)."""
    from ..routes_classes import new_join_code
    first = None
    classes = [c for c in item.get("classes") or [] if isinstance(c, dict) and isinstance(c.get("id"), int)]
    for c in classes:
        k = (await session.execute(select(SchoolClass).where(SchoolClass.oj_class_id == c["id"]))).scalars().first()
        if k is None:
            k = SchoolClass(teacher_id=e.teacher_id, name=str(c.get("name") or f"DKU OJ class {c['id']}")[:120],
                            join_code=new_join_code(), oj_class_id=c["id"], created_at=utcnow(),
                            term="DKU OJ")
            session.add(k)
            await session.flush()
        elif c.get("name") and k.name != str(c["name"])[:120]:
            k.name = str(c["name"])[:120]
        first = first or k
    if first is None:
        return
    e.class_id = first.id
    if len(classes) == 1 and students:
        have = set((await session.execute(select(ClassMember.user_id).where(
            ClassMember.class_id == first.id))).scalars())
        for u in students:
            if u.id not in have:
                session.add(ClassMember(class_id=first.id, user_id=u.id))
    await session.flush()


async def sync_exams(sessionmaker, client: DkuojClient, settings, now: datetime) -> int:
    """Page through changed exams since the stored cursor. Returns how many were applied."""
    async with sessionmaker() as session:
        cursor = await get_state(session, CURSOR)
    newest = cursor
    n = 0
    page = 1
    for _ in range(MAX_PAGES):
        data = await client.list_exams(cursor, page)
        for item in data.get("exams") or []:
            if not isinstance(item, dict):
                continue
            async with sessionmaker() as session:
                e = await apply_listing(session, item, settings, now)
                await session.commit()
                if e is not None:
                    n += 1
                    await invalidate_exam(get_store(), e.id)
            up = item.get("updated_at")
            if isinstance(up, str) and (newest is None or _time(up) and _time(up) > (_time(newest) or datetime.min)):
                newest = up
        nxt = data.get("next_page")
        if not nxt:
            break
        page = int(nxt) if isinstance(nxt, (int, str)) and str(nxt).isdigit() else page + 1
    if newest != cursor:
        async with sessionmaker() as session:
            await set_state(session, CURSOR, newest)
    return n


# ---------------------------------------------------------------- packages
def package_due(e: Exam, now: datetime) -> bool:
    meta = e.oj_meta or {}
    if e.oj_removed or e.oj_frozen_version:
        return False
    start = _time(meta.get("start_time"))
    if start is None or now < start - PACKAGE_LEAD:
        return False
    retry = _time(meta.get("package_retry_at"))
    if retry is not None and now < retry:
        return False
    return meta.get("package_version") != e.oj_package_version or not e.questions


async def fetch_packages(sessionmaker, client: DkuojClient, now: datetime) -> int:
    async with sessionmaker() as session:
        exams = (await session.execute(select(Exam).where(Exam.source == "dkuoj", Exam.oj_removed == false(),
                                                          Exam.oj_frozen_version.is_(None)))).scalars().all()
        due = [(e.id, e.oj_key, e.oj_package_version if e.questions else None) for e in exams if package_due(e, now)]
    applied = 0
    for exam_id, key, have in due:
        try:
            if await _fetch_package(sessionmaker, client, exam_id, key, have, now):
                applied += 1
        except (OjError, AdapterError) as err:
            msg = getattr(err, "message", None) or str(err)
            log.warning("DKU OJ package of %s not applied: %s", key, msg)
            async with sessionmaker() as session:
                e = await session.get(Exam, exam_id)
                if e is not None:
                    e.oj_meta = {**(e.oj_meta or {}), "package_error": msg[:300],
                                 "package_retry_at": iso(now + timedelta(minutes=2))}
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
            meta = dict(e.oj_meta or {})
            if status == 425:
                wait = retry_after if retry_after is not None else 3600
                meta["package_retry_at"] = iso(now + timedelta(seconds=max(30, min(wait, 86400))))
            elif status == 404:
                meta["package_error"] = "DKU OJ says this is not a FairPlay exam"
                meta["package_retry_at"] = iso(now + timedelta(minutes=10))
            else:
                meta.pop("package_retry_at", None)
                meta["package_version"] = e.oj_package_version     # 304: what we have is current
            e.oj_meta = meta
            await session.commit()
        return False
    assert pkg is not None
    version = str(pkg.get("package_version") or "")[:32]
    if not version:
        raise AdapterError("the package has no package_version")
    async with sessionmaker() as session:
        e = await session.get(Exam, exam_id)
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
        meta = dict((e.oj_meta if e else None) or {})
        meta.pop("package_retry_at", None)
        meta.pop("package_error", None)
        meta["package_version"] = version
        meta["description_md"] = str(pkg.get("description_md") or "")[:20000]
        meta["assets"] = [a["name"] for a in assets]
        # Only while nobody started: the first start freezes the version (same row lock).
        res = await session.execute(
            update(Exam).where(Exam.id == exam_id, Exam.oj_frozen_version.is_(None)).values(
                questions=questions, question_count=len(questions), total_points=total,
                oj_package_version=version, oj_meta=meta, updated_at=now))
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


# ---------------------------------------------------------------- the loop
async def run_once(sessionmaker, settings, now: datetime | None = None, deliver: bool = True,
                   client: DkuojClient | None = None) -> dict:
    """One full pass: listing, packages, released results, outbox. Errors of one step are
    logged and do not stop the others."""
    from .delivery import deliver_due
    from .results import sync_results
    now = now or utcnow()
    out: dict = {}
    own = client is None
    client = client or DkuojClient.from_settings(settings)
    try:
        for name, step in (("exams", lambda: sync_exams(sessionmaker, client, settings, now)),
                           ("packages", lambda: fetch_packages(sessionmaker, client, now)),
                           ("results", lambda: sync_results(sessionmaker, client, now)),
                           ("delivery", lambda: deliver_due(sessionmaker, client, settings, now))):
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


async def run_forever(settings) -> None:
    """Started from main.py's lifespan when the bridge is configured."""
    from ..db import get_database
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
                    if loop.time() - last_full >= max(10, settings.dkuoj_sync_seconds):
                        last_full = loop.time()
                        await run_once(db.sessionmaker, settings)
                    else:
                        async with DkuojClient.from_settings(settings) as client:
                            await deliver_due(db.sessionmaker, client, settings)
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
