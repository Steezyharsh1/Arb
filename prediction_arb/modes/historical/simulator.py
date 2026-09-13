"""Historical execution simulation: re-export of the dedicated
HistoricalExecutionEngine. Distinct class, simulation only (§44)."""
from ...execution.simulated import HistoricalExecutionEngine

__all__ = ["HistoricalExecutionEngine"]
