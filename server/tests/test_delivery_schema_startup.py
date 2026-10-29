"""The outbox unique key (attempt_id, revision) is installed by the startup step, not on a request.
The Postgres test runs when TEST_POSTGRES_URL points at a server where a scratch database may be
created (postgresql+asyncpg://user:pw@host:port/postgres); it is skipped otherwise."""
import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.db import Database
from app.integrations import outbox

PG_URL = os.environ.get("TEST_POSTGRES_URL")

OLD_TABLE = """
CREATE TABLE external_deliveries (
    id BIGSERIAL PRIMARY KEY, source VARCHAR(32) NOT NULL,
    attempt_id VARCHAR(32) NOT NULL UNIQUE, exam_id VARCHAR(32) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending', tries INTEGER NOT NULL DEFAULT 0,
    next_at TIMESTAMP NOT NULL, last_error VARCHAR(500), receipt VARCHAR(200), payload JSON, uploads JSON,
    created_at TIMESTAMP NOT NULL, sent_at TIMESTAMP, locked_until TIMESTAMP, claimed_by VARCHAR(64),
    last_status INTEGER, auth_failed_at TIMESTAMP);
CREATE INDEX ix_external_deliveries_due ON external_deliveries (source, status, next_at);
CREATE INDEX ix_external_deliveries_exam_id ON external_deliveries (exam_id);
INSERT INTO external_deliveries (source, attempt_id, exam_id, status, next_at, created_at, receipt)
    VALUES ('dkuoj', 'a1', 'e1', 'sent', now(), now(), 'R1'), ('dkuoj', 'a2', 'e1', 'pending', now(), now(), NULL);
"""

INSERT = ("INSERT INTO external_deliveries (source, attempt_id, revision, exam_id, status, tries, next_at, created_at) "
          "VALUES ('dkuoj', :a, :r, 'e1', 'pending', 0, now(), now())")


class FakeConn:
    def __init__(self, dialect, unique):
        self.dialect = type("D", (), {"name": dialect})()
        self.unique = unique
        self.sql = []

    def execute(self, stmt, *a, **k):
        self.sql.append(str(stmt))


def test_postgres_statements_add_the_new_key_first_then_drop_the_old_one_by_name(monkeypatch):
    conn = FakeConn("postgresql", None)
    monkeypatch.setattr(outbox, "inspect", lambda c: type("I", (), {"get_table_names": lambda s: ["external_deliveries"]})())
    monkeypatch.setattr(outbox, "_unique_sets", lambda c, t: {"external_deliveries_attempt_id_key": ["attempt_id"]})
    outbox._upgrade_unique(conn)
    assert conn.sql == [
        "SET LOCAL lock_timeout = '15s'",
        "ALTER TABLE external_deliveries ADD CONSTRAINT uq_delivery_attempt_revision UNIQUE (attempt_id, revision)",
        'ALTER TABLE external_deliveries DROP CONSTRAINT IF EXISTS "external_deliveries_attempt_id_key"',
        'DROP INDEX IF EXISTS "external_deliveries_attempt_id_key"',
    ]


def test_postgres_statements_do_nothing_when_the_table_is_current(monkeypatch):
    conn = FakeConn("postgresql", None)
    monkeypatch.setattr(outbox, "inspect", lambda c: type("I", (), {"get_table_names": lambda s: ["external_deliveries"]})())
    monkeypatch.setattr(outbox, "_unique_sets", lambda c, t: {outbox.UQ: ["attempt_id", "revision"]})
    outbox._upgrade_unique(conn)
    assert conn.sql == []


def test_the_lazy_guard_changes_nothing():
    asyncio.run(outbox.ensure_revision_schema(None))


@pytest.mark.skipif(not PG_URL, reason="TEST_POSTGRES_URL not set")
def test_old_outbox_table_is_upgraded_at_startup_on_postgres():
    async def go():
        name = "fp_scratch_" + uuid.uuid4().hex[:10]
        admin = create_async_engine(PG_URL, isolation_level="AUTOCOMMIT")
        async with admin.connect() as c:
            await c.execute(text(f"CREATE DATABASE {name}"))
        url = PG_URL.rsplit("/", 1)[0] + "/" + name
        try:
            eng = create_async_engine(url)
            async with eng.begin() as c:
                for stmt in [s for s in OLD_TABLE.split(";\n") if s.strip()]:
                    await c.execute(text(stmt))
                old = (await c.execute(text(
                    "SELECT conname FROM pg_constraint WHERE conrelid = 'external_deliveries'::regclass AND contype = 'u'"
                ))).scalars().all()
            await eng.dispose()
            assert old == ["external_deliveries_attempt_id_key"]

            dbs = [Database(Settings(database_url=url)) for _ in range(3)]
            await asyncio.gather(*(d.create_all() for d in dbs))
            await dbs[0].create_all()
            for d in dbs:
                await d.dispose()

            eng = create_async_engine(url)
            async with eng.begin() as c:
                rows = (await c.execute(text("SELECT attempt_id, revision, receipt FROM external_deliveries "
                                             "ORDER BY attempt_id"))).all()
                assert [tuple(r) for r in rows] == [("a1", 1, "R1"), ("a2", 1, None)]
                cons = (await c.execute(text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = 'external_deliveries'::regclass AND contype = 'u'"))).all()
                assert [tuple(r) for r in cons] == [(outbox.UQ, "UNIQUE (attempt_id, revision)")]
                await c.execute(text(INSERT), {"a": "a1", "r": 2})
            for a, r in (("a1", 2), ("a1", 1)):
                with pytest.raises(Exception, match="uq_delivery_attempt_revision"):
                    async with eng.begin() as c:
                        await c.execute(text(INSERT), {"a": a, "r": r})
            await eng.dispose()
        finally:
            async with admin.connect() as c:
                await c.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
            await admin.dispose()

    asyncio.run(go())
