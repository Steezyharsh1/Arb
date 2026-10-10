"""Unwired emergency-response scaffold (§71, §72).

The current manager can persistently engage the kill switch and report a
position snapshot; it does **not** cancel orders or flatten exposure. Do not
use it as a recovery path until adapter-specific cancellation/flattening,
partial failures, and alert/write failures have explicit semantics and tests.
See D24 in ``docs/ROADMAP.md``."""
from __future__ import annotations

from ..core.logging import get_logger
from ..monitoring.alerts import AlertSink
from ..risk import kill_switch
from ..risk.exposure import ExposureTracker
from .executor import ExecutionEngine

log = get_logger("execution.emergency")


class EmergencyManager:
    def __init__(self, engine: ExecutionEngine, exposure: ExposureTracker,
                 alerts: AlertSink):
        self.engine = engine
        self.exposure = exposure
        self.alerts = alerts

    async def engage_kill_switch(self, reason: str) -> dict:
        """Stop new orders, report exposure. Cancellation of resting
        orders is engine/adapter-specific and reported, not assumed."""
        kill_switch.engage(reason)
        unhedged = await self.exposure.total_unhedged_dollars()
        positions = await self.engine.get_positions()
        summary = {
            "kill_switch": "ENGAGED",
            "reason": reason,
            "unhedged_dollars": str(unhedged),
            "open_positions": len(positions),
        }
        await self.alerts.alert(
            "KILL SWITCH ENGAGED",
            f"reason={reason}; unhedged=${unhedged}; "
            f"positions={len(positions)}. System will place no new orders "
            f"until manually re-enabled (--unkill).")
        return summary
