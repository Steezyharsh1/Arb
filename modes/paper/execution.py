"""Paper-mode execution: re-export of the dedicated
PaperExecutionEngine (§44) — a distinct class, NOT LiveExecutionEngine
with a flag. Kept as its own module so the mode package mirrors the
spec layout."""
from ...execution.simulated import PaperExecutionEngine

__all__ = ["PaperExecutionEngine"]
