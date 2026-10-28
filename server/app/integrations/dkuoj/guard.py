"""Per-exam guard shared by the periodic sync and the immediate sync ping (ping.py).

A store key with an expiry, so it also works across workers and clears itself if a holder
dies. The runner lock in sync.py decides which worker runs the loop; this one stops the
loop and a ping from working on the same exam at the same time. A ping that finds the
guard taken leaves a follow-up mark; whoever releases the guard starts that follow-up."""
from __future__ import annotations

import asyncio
import secrets
import time
from contextlib import asynccontextmanager

from ...store import get_store

GUARD_TTL_S = 120
AGAIN_TTL_S = 600
ALL = "*"


def _guard_key(scope: str) -> str:
    return f"fpb:sync:guard:{scope}"


def _again_key(scope: str) -> str:
    return f"fpb:sync:again:{scope}"


async def take(scope: str, token: str) -> bool:
    return await get_store().setnx(_guard_key(scope), token, GUARD_TTL_S)


async def mark_again(scope: str) -> None:
    await get_store().set(_again_key(scope), "1", AGAIN_TTL_S)


async def release(scope: str, token: str) -> bool:
    """Let go of the guard. True when a ping asked for a follow-up meanwhile."""
    store = get_store()
    if await store.get(_guard_key(scope)) == token:
        await store.delete(_guard_key(scope))
    return await store.getdel(_again_key(scope)) is not None


@asynccontextmanager
async def exam_guard(scope: str, wait_s: float = 30.0):
    """Wait up to wait_s for the exam (a ping works on it for a few seconds), then go on
    regardless: the loop must not stall for good on a stuck holder."""
    token = secrets.token_hex(8)
    deadline = time.monotonic() + wait_s
    got = await take(scope, token)
    while not got and time.monotonic() < deadline:
        await asyncio.sleep(0.2)
        got = await take(scope, token)
    try:
        yield
    finally:
        if got and await release(scope, token):
            from .ping import start
            await start(scope)
