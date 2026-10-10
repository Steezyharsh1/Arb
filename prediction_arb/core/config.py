"""Configuration loading + mode-consistency validation (§53, §56).

Rules enforced here:
  * mode value in the mode-specific file must equal the requested mode
  * execution_engine must match the mode (paper.yaml + LiveExecutionEngine
    fails startup — spec test case 10)
  * allow_live_orders may be true ONLY in live mode
  * env files are mode-specific; live credentials are never loaded outside
    live mode (§48)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

from .paths import project_root
from .types import Mode, ModeIsolationError

# Project root (the directory holding config/, data/, logs/, reports/) — see
# core/paths.py for resolution order. Both names are module-level so tests can
# monkeypatch them; every function below reads them at call time.
ROOT = project_root()
CONFIG_DIR = ROOT / "config"

_EXPECTED_ENGINE = {
    Mode.HISTORICAL: "HistoricalFillSimulator",
    Mode.PAPER: "PaperExecutionEngine",
    Mode.LIVE: "LiveExecutionEngine",
    Mode.COLLECT: None,  # collect mode has no execution engine at all
}

_EXPECTED_DB = {
    Mode.HISTORICAL: "prediction_arb_historical",
    Mode.PAPER: "prediction_arb_paper",
    Mode.LIVE: "prediction_arb_live",
    Mode.COLLECT: "prediction_arb_paper",  # collect stores market data only
}

_LIVE_CRED_KEYS = ("LIVE",)  # env keys containing these markers are live-only


@dataclass
class AppConfig:
    mode: Mode
    settings: dict[str, Any]
    venues: dict[str, Any]
    fees: dict[str, Any]
    risk_limits: dict[str, Any]
    mode_config: dict[str, Any]
    env: dict[str, str] = field(default_factory=dict)

    @property
    def execution_engine_name(self) -> str | None:
        return self.mode_config.get("execution_engine")

    @property
    def database_name(self) -> str:
        return self.mode_config.get("database", _EXPECTED_DB[self.mode])

    @property
    def database_file(self) -> Path:
        """Where the store is, WITHOUT creating its directory (§103).

        Read-only reports use this so that asking for a report can never
        create ``data/`` as a side effect.
        """
        d = ROOT / "data" / (self.mode.value if self.mode != Mode.COLLECT else "paper")
        return d / f"{self.database_name}.sqlite3"

    @property
    def database_path(self) -> Path:
        path = self.database_file
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def recorded_store_path(self) -> Path:
        """Where collect/paper recorded market data lives (D3).

        Collect and paper share one market-data lineage, so this is the
        collect database file — the store historical replay must be able
        to read. Derived from the same constants collect mode uses to
        write, so the two cannot drift apart. Read-only from here on:
        historical mode writes its own database and never this one.
        """
        return (ROOT / "data" / "paper"
                / f"{_EXPECTED_DB[Mode.COLLECT]}.sqlite3")

    @property
    def allow_live_orders(self) -> bool:
        return bool(self.mode_config.get("allow_live_orders", False))


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config(mode_arg: str | None = None) -> AppConfig:
    settings = _load_yaml(CONFIG_DIR / "settings.yaml")
    venues = _load_yaml(CONFIG_DIR / "venues.yaml")
    fees = _load_yaml(CONFIG_DIR / "fees.yaml")
    risk_limits = _load_yaml(CONFIG_DIR / "risk_limits.yaml")

    mode_str = (mode_arg or settings.get("mode") or "paper").lower()
    try:
        mode = Mode(mode_str)
    except ValueError:
        raise ModeIsolationError(f"Unknown mode: {mode_str!r}")

    if mode == Mode.COLLECT:
        mode_config: dict[str, Any] = {
            "mode": "collect",
            "execution_engine": None,
            "database": _EXPECTED_DB[Mode.COLLECT],
            "allow_live_orders": False,
            "env_file": ".env.paper",
        }
    else:
        mode_config = _load_yaml(CONFIG_DIR / f"{mode.value}.yaml")
        if not mode_config:
            raise ModeIsolationError(
                f"Missing mode config file config/{mode.value}.yaml")

    validate_mode_consistency(mode, mode_config)
    env = load_mode_env(mode, mode_config)
    return AppConfig(mode=mode, settings=settings, venues=venues, fees=fees,
                     risk_limits=risk_limits, mode_config=mode_config, env=env)


def validate_mode_consistency(mode: Mode, mode_config: dict[str, Any]) -> None:
    declared = str(mode_config.get("mode", "")).lower()
    if declared != mode.value:
        raise ModeIsolationError(
            f"Mode config declares mode={declared!r} but application mode is "
            f"{mode.value!r}. Refusing to start.")

    expected_engine = _EXPECTED_ENGINE[mode]
    engine = mode_config.get("execution_engine")
    if engine != expected_engine:
        raise ModeIsolationError(
            f"Mode {mode.value!r} requires execution_engine="
            f"{expected_engine!r} but config declares {engine!r}. "
            f"Refusing to start (spec §53, test case 10).")

    allow_live = bool(mode_config.get("allow_live_orders", False))
    if allow_live and mode != Mode.LIVE:
        raise ModeIsolationError(
            f"allow_live_orders=true is illegal in mode {mode.value!r}.")
    if mode == Mode.LIVE and not allow_live:
        raise ModeIsolationError(
            "live.yaml must explicitly set allow_live_orders: true.")

    db = str(mode_config.get("database", ""))
    if db != _EXPECTED_DB[mode]:
        raise ModeIsolationError(
            f"Mode {mode.value!r} must use database {_EXPECTED_DB[mode]!r}, "
            f"got {db!r}.")


def load_mode_env(mode: Mode, mode_config: dict[str, Any]) -> dict[str, str]:
    """Load ONLY the env file for this mode. Additionally, refuse to
    proceed if a non-live env file contains live-credential markers."""
    env_file = mode_config.get("env_file") or f".env.{mode.value}"
    path = ROOT / env_file
    if not path.exists():
        return {}
    values = {k: v for k, v in dotenv_values(path).items() if v is not None}
    if mode != Mode.LIVE:
        leaked = [k for k in values if any(m in k.upper() for m in _LIVE_CRED_KEYS)]
        if leaked:
            raise ModeIsolationError(
                f"Env file {env_file} contains live-credential keys "
                f"{leaked} but mode is {mode.value!r}. Refusing to start.")
    return values
