"""Lightweight forward-only migration runner (§40, §82).

SQLite + a `schema_migrations` table. Each migration is (version,
callable). v1 ships migration 001 (create all tables). Alembic can be
swapped in later; this runner keeps v1 dependency-light while still
giving versioned, ordered, recorded migrations.
"""
from __future__ import annotations

from typing import Callable

from sqlalchemy import Engine, text

from ..models import Base

Migration = tuple[int, str, Callable[[Engine], None]]


def _m001_create_all(engine: Engine) -> None:
    Base.metadata.create_all(engine)


# Phase 4 / D4: the columns the recorded store was missing. Every one is
# nullable — rows recorded before this migration legitimately do not have
# the data, and reconstruction must report "unknown" rather than invent it.
_M002_MARKET_COLUMNS: tuple[tuple[str, str], ...] = (
    ("clearing_entity", "VARCHAR(64)"),
    ("normalized_event", "TEXT"),
    ("outcome", "VARCHAR(16)"),
    ("normalized_outcome", "VARCHAR(16)"),
    ("outcome_count", "INTEGER"),
    ("all_outcomes_json", "TEXT"),
    ("settlement_time", "DATETIME"),
    ("timezone", "VARCHAR(32)"),
    ("cutoff_time", "DATETIME"),
    ("resolution_source", "TEXT"),
    ("resolution_method", "TEXT"),
    ("threshold", "VARCHAR(48)"),
    ("threshold_operator", "VARCHAR(8)"),
    ("units", "VARCHAR(32)"),
    ("payout", "VARCHAR(32)"),
    ("currency", "VARCHAR(8)"),
    ("early_resolution_rules", "TEXT"),
    ("cancellation_rules", "TEXT"),
    ("contract_version", "VARCHAR(32)"),
    ("tick_size", "VARCHAR(32)"),
    ("min_order_size", "VARCHAR(32)"),
    ("max_order_size", "VARCHAR(32)"),
    ("jurisdiction_requirements", "VARCHAR(64)"),
    ("microstructure_json", "TEXT"),
    ("order_types_json", "TEXT"),
    ("raw_json", "TEXT"),
)


def _existing_columns(engine: Engine, table: str) -> set[str]:
    with engine.begin() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def _m002_market_contract_fields(engine: Engine) -> None:
    """Add the full-NormalizedContract columns to `markets` (D4).

    Idempotent by construction: a database created fresh by migration 001
    already has these columns (``create_all`` uses the current model), so
    each column is only added when it is actually absent. Old rows keep
    NULLs — they are never back-filled with guessed values.
    """
    present = _existing_columns(engine, "markets")
    with engine.begin() as conn:
        for name, ddl in _M002_MARKET_COLUMNS:
            if name in present:
                continue
            conn.execute(
                text(f"ALTER TABLE markets ADD COLUMN {name} {ddl}"))
        # Replay groups by settlement window, so it needs an index.
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_markets_settlement_time "
            "ON markets (settlement_time)"))


MIGRATIONS: list[Migration] = [
    (1, "create initial schema", _m001_create_all),
    (2, "markets: full NormalizedContract fields + raw payload (D4)",
     _m002_market_contract_fields),
]


def run_migrations(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, description TEXT, "
            "applied_at TEXT)"))
        applied = {row[0] for row in
                   conn.execute(text("SELECT version FROM schema_migrations"))}
    for version, description, fn in sorted(MIGRATIONS):
        if version in applied:
            continue
        fn(engine)
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO schema_migrations (version, description, "
                     "applied_at) VALUES (:v, :d, datetime('now'))"),
                {"v": version, "d": description})
