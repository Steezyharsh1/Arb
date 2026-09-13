"""Backtest fill simulator — thin alias layer over the deterministic
HistoricalExecutionEngine so backtests and historical replay share one
fill model (§57)."""
from ..execution.simulated import HistoricalExecutionEngine as BacktestFillSimulator

__all__ = ["BacktestFillSimulator"]
