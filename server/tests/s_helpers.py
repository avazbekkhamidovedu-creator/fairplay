"""Shared helpers for the S1-S6 tests (2026-10-20)."""
import asyncio
from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import create_async_engine

from app.models import Attempt


def db_update(client, aid: str, **values) -> None:
    """Change an attempt row behind the API (e.g. move its deadline into the past). A
    fresh engine: the app's own pool belongs to the TestClient's event loop."""
    async def go():
        eng = create_async_engine(client.app.state.settings.database_url)
        async with eng.begin() as conn:
            await conn.execute(update(Attempt).where(Attempt.id == aid).values(**values))
        await eng.dispose()
    asyncio.run(go())


def shift(dt, **kw):
    return dt + timedelta(**kw)


def forget_double_click(aid: str) -> None:
    """Let REENTRY_QUIET_S pass: the next start from the same device is a new app run."""
    from app.store import get_store
    asyncio.run(get_store().delete(f"entry:{aid}"))


def drop_attempt_cache(aid: str) -> None:
    from app.services import attempt_key
    from app.store import get_store
    asyncio.run(get_store().delete(attempt_key(aid)))
