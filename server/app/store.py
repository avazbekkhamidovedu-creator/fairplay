"""Small key/value store for hot-path state: Redis in production, in-memory in dev.

Holds heartbeats / live-room state, event counters, wrong-code counters and lockouts,
cached student-view exam content and cached attempt metadata. Nothing here is the
source of truth for grades or answers (those live in the database).
"""
from __future__ import annotations

import os
import time
from typing import Protocol


class Store(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl: int | None = None) -> None: ...
    async def delete(self, *keys: str) -> None: ...
    async def incr(self, key: str, ttl: int) -> int: ...
    async def getdel(self, key: str) -> str | None: ...
    async def setnx(self, key: str, value: str, ttl: int) -> bool: ...
    async def incrby(self, key: str, amount: int, ttl: int) -> int: ...
    async def hdel(self, key: str, field: str) -> None: ...
    async def ttl(self, key: str) -> int: ...
    async def hset(self, key: str, field: str, value: str, ttl: int) -> None: ...
    async def hgetall(self, key: str) -> dict[str, str]: ...
    async def hincrby(self, key: str, field: str, amount: int, ttl: int) -> int: ...
    async def live_snapshot(self, exam_id: str) -> tuple[dict, dict, dict]: ...
    async def mget(self, *keys: str) -> list[str | None]: ...
    async def batch(self, ops: list[tuple]) -> list: ...
    async def close(self) -> None: ...


# perf 0.6.2: several commands in one round trip (one Redis pipeline). Each op is a tuple:
#   ("get", key)                      -> value or None
#   ("set", key, value, ttl)          -> None   (ttl None = no expiry)
#   ("incr", key, ttl)                -> new value; the TTL is set only on the first INCR
#   ("hset", key, field, value, ttl)  -> None   (the TTL is refreshed, like hset())
# Results come back in order. Not a transaction: same semantics as the single calls.


async def _batch_one_by_one(store, ops: list[tuple]) -> list:
    out = []
    for op in ops:
        kind = op[0]
        if kind == "get":
            out.append(await store.get(op[1]))
        elif kind == "set":
            out.append(await store.set(op[1], op[2], op[3]))
        elif kind == "incr":
            out.append(await store.incr(op[1], op[2]))
        elif kind == "hset":
            out.append(await store.hset(op[1], op[2], op[3], op[4]))
        else:
            raise ValueError(f"unknown store op {kind!r}")
    return out


class MemoryStore:
    """Single-process fallback. Not shared between workers: dev and tests only."""

    def __init__(self):
        self._data: dict[str, object] = {}
        self._exp: dict[str, float] = {}

    def _alive(self, key: str) -> bool:
        exp = self._exp.get(key)
        if exp is not None and exp <= time.monotonic():
            self._data.pop(key, None)
            self._exp.pop(key, None)
            return False
        return key in self._data

    def _expire(self, key: str, ttl: int | None) -> None:
        if ttl:
            self._exp[key] = time.monotonic() + ttl
        else:
            self._exp.pop(key, None)

    async def get(self, key):
        if not self._alive(key):
            return None
        v = self._data[key]
        return v if isinstance(v, str) else None

    async def set(self, key, value, ttl=None):
        self._data[key] = value
        self._expire(key, ttl)

    async def delete(self, *keys):
        for k in keys:
            self._data.pop(k, None)
            self._exp.pop(k, None)

    async def incrby(self, key, amount, ttl):
        n = (int(self._data[key]) if self._alive(key) else 0) + amount
        fresh = not self._alive(key)
        self._data[key] = str(n)
        if fresh:
            self._expire(key, ttl)
        return n

    async def setnx(self, key, value, ttl):
        if self._alive(key):
            return False
        self._data[key] = value
        self._expire(key, ttl)
        return True

    async def getdel(self, key):
        v = await self.get(key)
        self._data.pop(key, None)
        self._exp.pop(key, None)
        return v

    async def hdel(self, key, field):
        if self._alive(key) and isinstance(self._data.get(key), dict):
            self._data[key].pop(field, None)  # type: ignore[union-attr]

    async def incr(self, key, ttl):
        n = (int(self._data[key]) if self._alive(key) else 0) + 1
        fresh = not self._alive(key)
        self._data[key] = str(n)
        if fresh:
            self._expire(key, ttl)
        return n

    async def ttl(self, key):
        if not self._alive(key):
            return -2
        exp = self._exp.get(key)
        if exp is None:
            return -1
        return max(0, int(round(exp - time.monotonic())))

    def _hash(self, key) -> dict:
        if not self._alive(key) or not isinstance(self._data.get(key), dict):
            self._data[key] = {}
        return self._data[key]  # type: ignore[return-value]

    async def hset(self, key, field, value, ttl):
        self._hash(key)[field] = value
        self._expire(key, ttl)

    async def hgetall(self, key):
        if not self._alive(key) or not isinstance(self._data.get(key), dict):
            return {}
        return dict(self._data[key])  # type: ignore[arg-type]

    async def hincrby(self, key, field, amount, ttl):
        h = self._hash(key)
        n = int(h.get(field, 0)) + amount
        h[field] = str(n)
        self._expire(key, ttl)
        return n

    async def live_snapshot(self, exam_id):
        return (await self.hgetall(f"hb:{exam_id}"), await self.hgetall(f"evc:{exam_id}"),
                await self.hgetall(f"evl:{exam_id}"))

    async def mget(self, *keys):
        return [await self.get(k) for k in keys]

    async def batch(self, ops):
        return await _batch_one_by_one(self, ops)

    async def close(self):
        pass


class RedisStore:
    def __init__(self, url: str, max_connections: int = 200, wait_s: float = 30):
        import redis.asyncio as aioredis
        # perf 0.6.2 (5,000-student test): the plain pool raised "Too many connections" (a
        # 500) the moment a worker had more requests in flight than connections; a
        # blocking pool makes them wait their turn (up to wait_s) instead.
        pool = aioredis.BlockingConnectionPool.from_url(
            url, decode_responses=True, max_connections=max_connections, timeout=wait_s,
            socket_timeout=30)
        self.r = aioredis.Redis(connection_pool=pool)

    async def get(self, key):
        return await self.r.get(key)

    async def set(self, key, value, ttl=None):
        await self.r.set(key, value, ex=ttl)

    async def delete(self, *keys):
        if keys:
            await self.r.delete(*keys)

    async def setnx(self, key, value, ttl):
        return bool(await self.r.set(key, value, ex=ttl, nx=True))

    async def getdel(self, key):
        return await self.r.getdel(key)          # atomic (Redis >= 6.2)

    async def incrby(self, key, amount, ttl):
        pipe = self.r.pipeline()
        pipe.incrby(key, amount)
        pipe.expire(key, ttl, nx=True)
        n, _ = await pipe.execute()
        return int(n)

    async def hdel(self, key, field):
        await self.r.hdel(key, field)

    async def incr(self, key, ttl):
        pipe = self.r.pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl, nx=True)
        n, _ = await pipe.execute()
        return int(n)

    async def ttl(self, key):
        return int(await self.r.ttl(key))

    async def hset(self, key, field, value, ttl):
        pipe = self.r.pipeline()
        pipe.hset(key, field, value)
        pipe.expire(key, ttl)
        await pipe.execute()

    async def hgetall(self, key):
        return await self.r.hgetall(key)

    async def hincrby(self, key, field, amount, ttl):
        pipe = self.r.pipeline()
        pipe.hincrby(key, field, amount)
        pipe.expire(key, ttl)
        n, _ = await pipe.execute()
        return int(n or 0)

    async def live_snapshot(self, exam_id):
        pipe = self.r.pipeline()
        pipe.hgetall(f"hb:{exam_id}")
        pipe.hgetall(f"evc:{exam_id}")
        pipe.hgetall(f"evl:{exam_id}")
        hb, evc, evl = await pipe.execute()
        return hb, evc, evl

    async def mget(self, *keys):
        if not keys:
            return []
        return list(await self.r.mget(keys))

    async def batch(self, ops):
        pipe = self.r.pipeline(transaction=False)
        picks = []                      # index of each op's own result in the pipeline
        for op in ops:
            kind = op[0]
            if kind == "get":
                pipe.get(op[1])
                picks.append((len(pipe), "get"))
            elif kind == "set":
                pipe.set(op[1], op[2], ex=op[3])
                picks.append((len(pipe), "none"))
            elif kind == "incr":
                pipe.incr(op[1])
                picks.append((len(pipe), "int"))
                pipe.expire(op[1], op[2], nx=True)
            elif kind == "hset":
                pipe.hset(op[1], op[2], op[3])
                picks.append((len(pipe), "none"))
                pipe.expire(op[1], op[4])
            else:
                raise ValueError(f"unknown store op {kind!r}")
        res = await pipe.execute()
        out = []
        for n, how in picks:
            v = res[n - 1]
            out.append(None if how == "none" else int(v) if how == "int" else v)
        return out

    async def close(self):
        await self.r.aclose(close_connection_pool=True)


def make_store(redis_url: str | None) -> Store:
    if not redis_url:
        return MemoryStore()
    # Redis connections per worker process (REDIS_MAX_CONNECTIONS); more requests wait.
    try:
        n = int(os.environ.get("REDIS_MAX_CONNECTIONS", "200"))
    except ValueError:
        n = 200
    return RedisStore(redis_url, max(1, n))


_store: Store | None = None


def set_store(s: Store | None) -> None:
    global _store
    _store = s


def get_store() -> Store:
    if _store is None:
        raise RuntimeError("store not initialised")
    return _store
