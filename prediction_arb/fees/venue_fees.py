"""Convenience loader wiring config/fees.yaml into a FeeCalculator."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..core.paths import config_file
from .calculator import FeeCalculator


def load_fee_calculator(fees_config: dict[str, Any] | None = None,
                        max_schedule_age_days: int = 30) -> FeeCalculator:
    if fees_config is None:
        # Resolved at call time so $PREDICTION_ARB_HOME and test monkeypatching
        # are honoured.
        path: Path = config_file("fees.yaml")
        with open(path, "r", encoding="utf-8") as f:
            fees_config = yaml.safe_load(f) or {}
    return FeeCalculator.from_config(fees_config, max_schedule_age_days)
