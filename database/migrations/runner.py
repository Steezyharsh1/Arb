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


MIGRATIONS: list[Migration] = [
    (1, "create initial schema", _m001_create_all),
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
