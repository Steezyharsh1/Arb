"""Emergency procedures (§71, §72): flatten-and-stop behavior driven by
the kill switch or unrecoverable exposure."""
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
