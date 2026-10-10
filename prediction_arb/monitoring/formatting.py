"""Display helpers for money that may be UNAVAILABLE (§24, §28).

An amount the engine could not compute must never be rendered as a number:
``$0.00`` reads as "this cost is free" and a positive net profit reads as
"this trade is verified profitable". Both are false when a mandatory cost
is missing, so every reporting surface renders an unavailable amount as
``n/a``. One shared helper keeps the dashboard, the console summaries and
the JSON reports from drifting apart.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

#: What an unavailable amount renders as, on every reporting surface.
UNAVAILABLE_TEXT = "n/a"


def format_amount(value: Optional[Decimal], symbol: str = "$") -> str:
    """``$12.34`` for a known amount; ``n/a`` for an unavailable one."""
    if value is None:
        return UNAVAILABLE_TEXT
    return f"{symbol}{value:.2f}"


def format_ratio(value: Optional[Decimal], places: int = 4) -> str:
    """``0.0531`` for a known ratio; ``n/a`` for an unavailable one."""
    if value is None:
        return UNAVAILABLE_TEXT
    return f"{value:.{places}f}"
