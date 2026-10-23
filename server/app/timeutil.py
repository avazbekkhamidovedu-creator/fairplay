"""All timestamps are stored as naive UTC and rendered as ISO-8601 with a trailing Z."""
from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def arrived_at(request) -> datetime:
    """When this request reached the server (before any wait in the admission line,
    app/admission.py), as naive UTC; now if unknown. Deadline checks use this, so a
    request that waited in line at the deadline is not late (0.6.2 review)."""
    now = utcnow()
    try:
        ts = request.scope.get("state", {}).get("fairplay_arrived")
    except AttributeError:
        return now
    if not isinstance(ts, (int, float)):
        return now
    arrived = datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)
    return min(arrived, now)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat(timespec="milliseconds") + "Z"


def iso_from_epoch(ts: float | None) -> str | None:
    if ts is None:
        return None
    return iso(datetime.fromtimestamp(ts, timezone.utc))


def parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt
