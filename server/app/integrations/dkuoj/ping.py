"""Immediate sync of one exam after DKU OJ saved it (docs/dkuoj-bridge-v2.md §13).

POST /sync answers at once; the work runs as a background task under the per-exam guard
(guard.py), so it never overlaps the periodic loop on the same exam. A ping that finds the
exam busy leaves a follow-up mark and the holder runs it when it lets go."""
from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Annotated

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from ...store import get_store
from ...timeutil import utcnow
from . import guard
from .client import DkuojClient, OjError

log = logging.getLogger("proctora.dkuoj")

PER_MINUTE = 30
PER_MINUTE_EXAM = 6
WINDOW_S = 60
MAX_FOLLOW_UPS = 3
TOO_MANY = "Too many sync requests. Try again in a minute."

_tasks: set[asyncio.Task] = set()


class PingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exam_key: str | None = Field(default=None, min_length=1, max_length=200)
    class_id: StrictInt | None = Field(default=None, gt=0)
    installation_id: str | None = None
    event: str = Field(default="changed", pattern="^(changed|removed|audience_revoked)$")
    exam_keys: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(default_factory=list, max_length=2000)
    class_ids: list[Annotated[StrictInt, Field(gt=0)]] = Field(default_factory=list, max_length=2000)
    organization_ids: list[Annotated[StrictInt, Field(gt=0)]] = Field(default_factory=list, max_length=2000)


def parse(data: dict) -> PingIn:
    try:
        return PingIn.model_validate(data)
    except ValidationError as err:
        raise HTTPException(422, "; ".join(f"{'.'.join(map(str, e['loc'])) or 'body'}: {e['msg']}"
                                           for e in err.errors())[:300]) from None


async def check_rate(exam_key: str | None) -> None:
    store = get_store()
    if await store.incr("fpb:sync:ping:all", WINDOW_S) > PER_MINUTE:
        raise HTTPException(429, TOO_MANY, headers={"Retry-After": str(WINDOW_S)})
    if exam_key and await store.incr(f"fpb:sync:ping:exam:{exam_key}", WINDOW_S) > PER_MINUTE_EXAM:
        raise HTTPException(429, TOO_MANY, headers={"Retry-After": str(WINDOW_S)})


async def _step(name: str, fn) -> None:
    try:
        await fn()
    except OjError as err:
        log.warning("DKU OJ ping %s step failed: %s", name, err.message)
    except Exception:  # noqa: BLE001
        log.exception("DKU OJ ping %s step failed", name)


async def _fetch_one(sessionmaker, client: DkuojClient, cfg, key: str) -> None:
    """The listing since the stored cursor, only the entry of this key. The cursor stays
    where it is: the loop reads the same entry again, which changes nothing."""
    from .sync import CURSOR, MAX_PAGES, apply_item, get_state
    async with sessionmaker() as session:
        cursor = await get_state(session, CURSOR)
    page = 1
    for _ in range(MAX_PAGES):
        data = await client.list_exams(cursor, page)
        for item in data.get("exams") or []:
            if isinstance(item, dict) and item.get("key") == key:
                await apply_item(sessionmaker, item, cfg, utcnow())
                return
        nxt = data.get("next_page")
        if not nxt:
            return
        page = int(nxt) if isinstance(nxt, (int, str)) and str(nxt).isdigit() else page + 1


async def work(scope: str, targets: set[str] | None = None) -> None:
    from ...db import get_database
    from .source import SOURCE
    from .sync import fetch_packages, open_due, sync_exams
    sm = get_database().sessionmaker
    cfg = SOURCE.config
    async with DkuojClient.from_config(cfg) as client:
        from .isolation import verify_installation
        await verify_installation(sm, client, cfg)
        if scope == guard.ALL:
            await sync_exams(sm, client, cfg, utcnow(), full=True, targets=targets)
            await _step("packages", lambda: fetch_packages(sm, client, utcnow()))
        else:
            await sync_exams(sm, client, cfg, utcnow(), guard=False, full=True, targets={scope})
            await _step("packages", lambda: fetch_packages(sm, client, utcnow(), only=scope, guard=False))
    await _step("open", lambda: open_due(sm, utcnow()))


async def _run(scope: str, token: str, targets: set[str] | None = None) -> None:
    try:
        for _ in range(MAX_FOLLOW_UPS + 1):
            try:
                await work(scope, targets)
            except Exception:  # noqa: BLE001
                log.exception("DKU OJ ping")
            if not await _again(scope):
                break
    finally:
        if await guard.release(scope, token):
            await start(scope)


async def _again(scope: str) -> bool:
    return await get_store().getdel(guard._again_key(scope)) is not None


async def start(scope: str, targets: set[str] | None = None) -> bool:
    """Run a sync for `scope` (an exam key, or guard.ALL) in the background. False = started;
    True = the exam is busy and a follow-up run was marked."""
    token = secrets.token_hex(8)
    if not await guard.take(scope, token):
        await guard.mark_again(scope)
        return True
    task = asyncio.get_running_loop().create_task(_run(scope, token, targets))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return False
