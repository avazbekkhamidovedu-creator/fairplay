"""A student's DKU OJ classes for the course cards (docs/dkuoj-bridge-v2.md §2, §4).

GET /api/student/classes (routes_classes) merges these with the student's FairPlay classes.
The list comes from DKU OJ (GET students/<netid>/classes, signed fp->oj, one try, 3 s),
kept 60 s per student in the store. When DKU OJ can't be reached, the classes FairPlay
knows locally are used (kept 15 s, so a down DKU OJ doesn't cost every page 3 s): the
class the student set up through (class_login_events setup_ok) and the classes of synced
exams whose roster has the student. Enrollment is managed in DKU OJ (can_leave false).
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta

from sqlalchemy import false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import ClassMember, Exam, SchoolClass, User
from ...store import get_store
from ...timeutil import utcnow
from .models import ClassLogin, ClassLoginEvent, OjRosterEntry

log = logging.getLogger("proctora.dkuoj")

CACHE_S = 60
FALLBACK_CACHE_S = 15
TIMEOUT_S = 3.0
EXAM_LINK_DAYS = 180
MAX_CLASSES = 200
MIRROR_PREFIX = "dkuoj:"


def _cache_key(user_id: int) -> str:
    return f"oj:student-classes:{user_id}"


def _clean(items) -> list[dict]:
    """DKU OJ's answer -> [{id, name, course, teacher}] (anything malformed dropped)."""
    out = []
    for c in items if isinstance(items, list) else []:
        if not isinstance(c, dict) or not isinstance(c.get("id"), int) or isinstance(c.get("id"), bool):
            continue
        course = c.get("course")
        course_name = course.get("name") if isinstance(course, dict) else course
        teachers = [str(t)[:150] for t in c.get("teachers") or [] if isinstance(t, str) and t.strip()]
        out.append({"id": c["id"], "name": str(c.get("name") or f"Class {c['id']}")[:200],
                    "course": str(course_name)[:200] if course_name else None,
                    "teacher": ", ".join(teachers) or None})
        if len(out) >= MAX_CLASSES:
            break
    return out


async def _local(session: AsyncSession, user: User) -> list[dict]:
    """The DKU OJ classes FairPlay knows for this student without asking DKU OJ."""
    found: dict[int, dict] = {}
    if user.netid:
        ids = set((await session.execute(select(ClassLoginEvent.class_id).where(
            ClassLoginEvent.kind == "setup_ok", ClassLoginEvent.netid == user.netid))).scalars())
        for cid in ids:
            cl = (await session.execute(select(ClassLogin).where(ClassLogin.class_id == cid)
                                        .order_by(ClassLogin.id.desc()).limit(1))).scalar_one_or_none()
            found[cid] = {"id": cid, "name": (cl.class_name if cl else "") or f"Class {cid}",
                          "course": (cl.course_name if cl else "") or None, "teacher": None}
    since = utcnow() - timedelta(days=EXAM_LINK_DAYS)
    metas = (await session.execute(select(Exam.external_meta).join(
        OjRosterEntry, OjRosterEntry.exam_id == Exam.id).where(
        OjRosterEntry.user_id == user.id, Exam.source == "dkuoj",
        or_(Exam.status != "closed", Exam.updated_at >= since)))).scalars().all()
    for meta in metas:
        meta = meta or {}
        course = meta.get("course")
        course_name = course.get("name") if isinstance(course, dict) else None
        for c in meta.get("classes") or []:
            if isinstance(c, dict) and isinstance(c.get("id"), int) and c["id"] not in found:
                found[c["id"]] = {"id": c["id"], "name": str(c.get("name") or f"Class {c['id']}")[:200],
                                  "course": course_name, "teacher": None}
    # the FairPlay mirror classes of DKU OJ classes the student is a member of (synced)
    rows = (await session.execute(select(SchoolClass.external_ref, SchoolClass.name).join(
        ClassMember, ClassMember.class_id == SchoolClass.id).where(
        ClassMember.user_id == user.id, SchoolClass.external_ref.like(MIRROR_PREFIX + "%")))).all()
    for ref, name in rows:
        try:
            cid = int(ref[len(MIRROR_PREFIX):])
        except ValueError:
            continue
        found.setdefault(cid, {"id": cid, "name": name, "course": None, "teacher": None})
    return list(found.values())


async def dkuoj_classes(session: AsyncSession, user: User) -> list[dict]:
    """[{id (DKU OJ class id), name, course, teacher}] for a student; [] while the bridge is off."""
    from .client import DkuojClient, OjError
    from .source import SOURCE
    if not SOURCE.enabled:
        return []
    store = get_store()
    key = _cache_key(user.id)
    try:
        cached = await store.get(key)
        if cached:
            return json.loads(cached)
    except ValueError:
        pass
    classes, ttl = None, CACHE_S
    if user.netid:
        await session.commit()        # no database connection held while DKU OJ answers
        try:
            async with DkuojClient.from_config(SOURCE.config) as client:
                classes = _clean((await client.student_classes(user.netid, timeout=TIMEOUT_S)).get("classes"))
        except OjError as err:
            log.info("DKU OJ classes of a student not available: %s", err.message)
    if classes is None:
        classes, ttl = await _local(session, user), FALLBACK_CACHE_S
    await store.set(key, json.dumps(classes), ttl)
    return classes


async def forget(user_id: int) -> None:
    await get_store().delete(_cache_key(user_id))


async def exams_by_class(session: AsyncSession, user_id: int) -> dict[int, list[Exam]]:
    """DKU OJ class id -> the student's not-closed synced exams of that class."""
    exams = (await session.execute(select(Exam).join(OjRosterEntry, OjRosterEntry.exam_id == Exam.id).where(
        OjRosterEntry.user_id == user_id, Exam.source == "dkuoj", Exam.status != "closed",
        Exam.is_trial == false()))).scalars().all()
    out: dict[int, list[Exam]] = {}
    for e in exams:
        for c in (e.external_meta or {}).get("classes") or []:
            if isinstance(c, dict) and isinstance(c.get("id"), int):
                out.setdefault(c["id"], []).append(e)
    return out


async def is_class_login_username(session: AsyncSession, typed: str) -> bool:
    name = (typed or "").strip().lower()
    if "-" not in name or len(name) > 64:
        return False
    return (await session.execute(select(ClassLogin.id).where(ClassLogin.username == name).limit(1))).first() \
        is not None
