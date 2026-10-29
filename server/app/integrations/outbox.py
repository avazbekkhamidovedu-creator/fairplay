"""Generic outbox helpers (rows of integrations.models.ExternalDelivery)."""
from __future__ import annotations

from sqlalchemy import func, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import IntegrityEvent
from ..timeutil import iso
from .models import ExternalDelivery

REOPENED = "attempt_reopened"
UQ = "uq_delivery_attempt_revision"


async def enqueue(session: AsyncSession, source: str, aid: str, exam_id: str, now) -> None:
    """Add the outbox row inside the caller's transaction (no commit). A row that is there
    already is left alone, so the hand-in itself can never fail on it. The revision is 1 plus
    the times the attempt was reopened, so a repeated call for one hand-in stays one row."""
    reopened = (await session.execute(select(func.count()).select_from(IntegrityEvent).where(
        IntegrityEvent.attempt_id == aid, IntegrityEvent.type == REOPENED))).scalar_one()
    values = {"source": source, "attempt_id": aid, "revision": 1 + int(reopened or 0), "exam_id": exam_id,
              "status": "pending", "tries": 0, "next_at": now, "created_at": now}
    dialect = session.bind.dialect.name if session.bind is not None else "sqlite"
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    await session.execute(insert(ExternalDelivery).values(**values)
                          .on_conflict_do_nothing(index_elements=["attempt_id", "revision"]))


async def freeze_unsent(session: AsyncSession, a) -> None:
    """The attempt is about to be reopened: a hand-in not built yet keeps what was handed in
    (no commit)."""
    rows = (await session.execute(select(ExternalDelivery).where(
        ExternalDelivery.attempt_id == a.id, ExternalDelivery.payload.is_(None),
        ExternalDelivery.snapshot.is_(None)))).scalars().all()
    for d in rows:
        d.snapshot = {"answers": dict(a.answers or {}),
                      "started_at": a.started_at.isoformat(), "deadline_at": a.deadline_at.isoformat(),
                      "handed_in_at": (a.submitted_at or a.updated_at).isoformat(),
                      "evidence": "sealed" if a.sealed_proof else "server"}


def delivery_out(d: ExternalDelivery) -> dict:
    return {"revision": int(d.revision or 1), "status": d.status, "tries": int(d.tries or 0), "last_error": d.last_error,
            "last_status": d.last_status, "receipt": d.receipt, "next_at": iso(d.next_at) if d.status in ("pending", "failed") else None,
            "sent_at": iso(d.sent_at)}


async def delivery_status(session: AsyncSession, exam_id: str) -> dict[str, dict]:
    rows = (await session.execute(select(ExternalDelivery).where(
        ExternalDelivery.exam_id == exam_id).order_by(ExternalDelivery.revision))).scalars().all()
    return {d.attempt_id: delivery_out(d) for d in rows}


def _unique_sets(conn, table: str) -> dict[str, list[str]]:
    """name -> columns of every UNIQUE constraint / unique index of the table (SQLite names
    its inline ones sqlite_autoindex_*, which the inspector does not list)."""
    if conn.dialect.name == "sqlite":
        out = {}
        for row in conn.execute(text(f"PRAGMA index_list({table})")).all():
            if row[2]:
                out[row[1]] = [c[2] for c in conn.execute(text(f"PRAGMA index_info({row[1]})")).all()]
        return out
    insp = inspect(conn)
    out = {u["name"]: list(u["column_names"]) for u in insp.get_unique_constraints(table)}
    out.update({i["name"]: list(i["column_names"]) for i in insp.get_indexes(table) if i.get("unique")})
    return out


def _upgrade_unique(conn) -> None:
    """Older databases have attempt_id UNIQUE on its own; a second revision needs
    (attempt_id, revision) instead. create_all never alters a table, so do it here."""
    table = ExternalDelivery.__table__
    if table.name not in inspect(conn).get_table_names():
        return
    have = _unique_sets(conn, table.name)
    if UQ in have:
        return
    old = [n for n, cols in have.items() if cols == ["attempt_id"]]
    if conn.dialect.name == "postgresql":
        conn.execute(text("SET LOCAL lock_timeout = '15s'"))
        conn.execute(text(f"ALTER TABLE {table.name} ADD CONSTRAINT {UQ} UNIQUE (attempt_id, revision)"))
        for n in old:
            conn.execute(text(f'ALTER TABLE {table.name} DROP CONSTRAINT IF EXISTS "{n}"'))
            conn.execute(text(f'DROP INDEX IF EXISTS "{n}"'))
        return
    if not old:
        conn.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {UQ} ON {table.name} (attempt_id, revision)"))
        return
    have_cols = {c["name"] for c in inspect(conn).get_columns(table.name)}
    cols = ", ".join(c.name for c in table.columns if c.name in have_cols)
    old_name = f"_{table.name}_old"
    conn.execute(text(f"ALTER TABLE {table.name} RENAME TO {old_name}"))
    for ix in inspect(conn).get_indexes(old_name):
        conn.execute(text(f'DROP INDEX IF EXISTS "{ix["name"]}"'))
    table.create(conn)
    conn.execute(text(f"INSERT INTO {table.name} ({cols}) SELECT {cols} FROM {old_name}"))
    conn.execute(text(f"DROP TABLE {old_name}"))


async def ensure_revision_schema(session: AsyncSession) -> None:
    """Kept for callers: the table is upgraded at startup (Database.create_all), never during a request."""
