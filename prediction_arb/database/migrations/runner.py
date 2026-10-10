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


# D17: opportunity money columns that must be able to hold NULL. A NULL
# means "this mandatory cost could not be computed, so this figure is
# UNVERIFIED" — the opposite of the "0" that used to be written there.
_M003_NULLABLE = ("fees", "net_profit", "capital_required")

# The rebuilt table definition. Keep it in sync with
# `models.OpportunityRow`: a fresh database is created by `create_all` from
# the model, an existing one by this migration, and both must agree.
_M003_OPPORTUNITIES_DDL = """
CREATE TABLE opportunities_v3 (
    id INTEGER NOT NULL,
    opportunity_id VARCHAR(64) NOT NULL,
    mode VARCHAR(16) NOT NULL,
    opportunity_type VARCHAR(32) NOT NULL,
    timestamp DATETIME NOT NULL,
    normalized_event TEXT NOT NULL,
    contracts TEXT NOT NULL,
    venues VARCHAR(128) NOT NULL,
    gross_profit VARCHAR(32) NOT NULL,
    fees VARCHAR(32),
    slippage VARCHAR(32) NOT NULL,
    net_profit VARCHAR(32),
    quantity VARCHAR(32) NOT NULL,
    capital_required VARCHAR(32),
    independence VARCHAR(24) NOT NULL,
    status VARCHAR(16) NOT NULL,
    block_reasons TEXT NOT NULL,
    unavailable_costs TEXT,
    first_seen DATETIME,
    last_seen DATETIME,
    PRIMARY KEY (id)
)
"""


def _m003_opportunity_unavailable_costs(engine: Engine) -> None:
    """Let the opportunity cost columns hold NULL, and record which
    mandatory costs were unavailable (D17).

    ``fees``, ``net_profit`` and ``capital_required`` are NULL when a
    mandatory cost could not be computed, so the stored row can never
    claim a verified positive net profit for a BLOCKed opportunity.
    ``unavailable_costs`` names the missing components for research.

    SQLite cannot relax a NOT NULL column in place, so an existing table is
    rebuilt with the documented procedure and its rows copied across.
    Nothing is rewritten: a stored number keeps its value and a NULL is
    never back-filled with zero. Idempotent — a database created fresh by
    migration 001 already has the nullable columns and the extra column, so
    only the `ADD COLUMN` guard and the `PRAGMA` check run.
    """
    with engine.begin() as conn:
        info = conn.execute(
            text("PRAGMA table_info(opportunities)")).fetchall()
    if not info:
        return
    present = {row[1]: row[3] for row in info}   # column name -> notnull flag
    with engine.begin() as conn:
        if "unavailable_costs" not in present:
            conn.execute(text(
                "ALTER TABLE opportunities ADD COLUMN unavailable_costs "
                "TEXT"))
    if not any(present.get(name) for name in _M003_NULLABLE):
        return  # already nullable (fresh database) — nothing to rebuild

    shared = [name for name in present if name != "unavailable_costs"]
    columns = ", ".join(shared)
    with engine.begin() as conn:
        conn.execute(text(_M003_OPPORTUNITIES_DDL))
        conn.execute(text(
            f"INSERT INTO opportunities_v3 ({columns}) "
            f"SELECT {columns} FROM opportunities"))
        conn.execute(text("DROP TABLE opportunities"))
        conn.execute(text(
            "ALTER TABLE opportunities_v3 RENAME TO opportunities"))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_opportunities_opportunity_id "
            "ON opportunities (opportunity_id)"))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_opportunities_mode "
            "ON opportunities (mode)"))


_M004_SNAPSHOT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("received_at", "DATETIME"),
    ("timestamp_source", "VARCHAR(24)"),
    ("outcome_side", "VARCHAR(8)"),
    ("sequence_number", "INTEGER"),
    ("microstructure_type", "VARCHAR(24)"),
    ("contract_observed_at", "DATETIME"),
    ("contract_json", "TEXT"),
)


def _m004_historical_evidence(engine: Engine) -> None:
    """Bind future snapshots to receipt time and contemporaneous terms.

    Existing rows stay NULL: quote time is not receipt time, and today's
    market row is not a historical contract version. Read-only replay does
    not run this migration and can still inventory old stores.
    """
    present = _existing_columns(engine, "orderbook_snapshots")
    if not present:
        Base.metadata.tables["orderbook_snapshots"].create(engine, checkfirst=True)
        present = _existing_columns(engine, "orderbook_snapshots")
    with engine.begin() as conn:
        for name, ddl in _M004_SNAPSHOT_COLUMNS:
            if name not in present:
                conn.execute(text(
                    f"ALTER TABLE orderbook_snapshots ADD COLUMN {name} {ddl}"))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_orderbook_snapshots_received_at "
            "ON orderbook_snapshots (received_at)"))
    if "evaluation_json" not in _existing_columns(engine, "opportunities"):
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE opportunities ADD COLUMN evaluation_json TEXT"))


MIGRATIONS: list[Migration] = [
    (1, "create initial schema", _m001_create_all),
    (2, "markets: full NormalizedContract fields + raw payload (D4)",
     _m002_market_contract_fields),
    (3, "opportunities: nullable cost columns + unavailable_costs (D17)",
     _m003_opportunity_unavailable_costs),
    (4, "historical: snapshot timing/contract evidence + evaluation basis",
     _m004_historical_evidence),
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
