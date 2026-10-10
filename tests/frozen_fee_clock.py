"""Opt-in pytest plugin: run the suite with the fee/evaluation clock frozen.

Not collected as a test (no ``test_`` prefix). Used for the date-independence
sweep recorded in docs/TESTING.md::

    PYTHONPATH=. FROZEN_NOW=2027-10-09T12:00:00 .venv/bin/pytest -o addopts="" -p tests.frozen_fee_clock

Every module that decides fee age or the evaluator's decision time must be
frozen *together*. Freezing only the fee modules leaves
``arbitrage/two_leg.py`` on the real clock; a schedule dated "45 days ago" by
the frozen clock is then in the real future, its age is negative, and it is
wrongly treated as fresh (Arb.15 reproduced this as one failure).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

_ENV = "FROZEN_NOW"


def _frozen_instant() -> datetime:
    raw = os.environ[_ENV]
    return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)


def pytest_configure(config):  # pragma: no cover - exercised by the sweep only
    if _ENV not in os.environ:
        return
    import prediction_arb.arbitrage.two_leg as two_leg
    import prediction_arb.fees.calculator as calculator
    import prediction_arb.fees.freshness as freshness

    def _now() -> datetime:
        return _frozen_instant()

    # Modules import ``utcnow`` by value, so each reference is patched.
    calculator.utcnow = _now
    freshness.utcnow = _now
    two_leg.utcnow = _now
