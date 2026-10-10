"""Historical replay and library backtests share ONE dated research model.

The older undated HistoricalExecutionEngine is retained for direct simulator
unit tests; it is not the historical evaluation path (§102).
"""
from ...backtesting.simulator import HistoricalFillSimulator

__all__ = ["HistoricalFillSimulator"]
