"""UTC clock utilities. All internal timestamps are timezone-aware UTC.

Never compare timestamps from different venues without passing them
through :func:`ensure_utc` first.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Coerce a datetime to timezone-aware UTC. Naive datetimes are assumed
    to already be UTC (venue APIs used here document UTC timestamps)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_iso_utc(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    v = value.replace("Z", "+00:00")
    try:
        return ensure_utc(datetime.fromisoformat(v))
    except ValueError:
        return None


def age_ms(ts: Optional[datetime], now: Optional[datetime] = None) -> Optional[float]:
    """Return non-negative age, or ``None`` for missing/future timestamps.

    A future venue timestamp cannot prove freshness: it may indicate clock
    skew, malformed data, or an invalid observation. Callers already fail
    closed on ``None``, so do not let a negative age pass their limits.
    """
    if ts is None:
        return None
    now = now or utcnow()
    elapsed = (now - ensure_utc(ts)).total_seconds() * 1000.0
    return elapsed if elapsed >= 0 else None
