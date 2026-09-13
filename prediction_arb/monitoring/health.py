"""API health monitoring (§38). Tracks per-venue health signals; when
data becomes unreliable trading is disabled until recovery."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from ..core.clock import utcnow


@dataclass
class VenueHealth:
    venue: str
    connected: bool = False
    last_success: Optional[datetime] = None
    last_error: Optional[str] = None
    consecutive_errors: int = 0
    rest_latency_ms: Optional[float] = None
    rate_limit_hits: int = 0
    rejected_orders: int = 0
    healthy: bool = False


class HealthMonitor:
    def __init__(self, max_consecutive_errors: int = 3,
                 max_silence_seconds: float = 60.0):
        self.max_consecutive_errors = max_consecutive_errors
        self.max_silence_seconds = max_silence_seconds
        self._venues: dict[str, VenueHealth] = {}
        self._lock = asyncio.Lock()

    async def record_success(self, venue: str,
                             latency_ms: Optional[float] = None) -> None:
        async with self._lock:
            h = self._venues.setdefault(venue, VenueHealth(venue=venue))
            h.connected = True
            h.last_success = utcnow()
            h.consecutive_errors = 0
            if latency_ms is not None:
                h.rest_latency_ms = latency_ms
            h.healthy = True

    async def record_error(self, venue: str, error: str,
                           rate_limited: bool = False) -> None:
        async with self._lock:
            h = self._venues.setdefault(venue, VenueHealth(venue=venue))
            h.last_error = error[:500]
            h.consecutive_errors += 1
            if rate_limited:
                h.rate_limit_hits += 1
            if h.consecutive_errors >= self.max_consecutive_errors:
                h.healthy = False

    async def is_healthy(self, venue: str) -> bool:
        async with self._lock:
            h = self._venues.get(venue)
            if h is None or not h.healthy or h.last_success is None:
                return False
            silence = (utcnow() - h.last_success).total_seconds()
            return silence <= self.max_silence_seconds

    async def trading_allowed(self, venues: list[str]) -> tuple[bool, str]:
        """Trading requires ALL involved venues healthy (§38)."""
        for v in venues:
            if not await self.is_healthy(v):
                return False, f"venue {v} unhealthy — TRADING DISABLED"
        return True, "ok"

    async def snapshot(self) -> dict[str, VenueHealth]:
        async with self._lock:
            return dict(self._venues)
