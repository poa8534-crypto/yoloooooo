"""Forward-only, idempotent schema migrations.

The ledger is append-only and already holds captured evidence, so migrations
here may only add tables and nullable columns. Nothing drops, rewrites or
back-fills an existing row: the stored evidence is exactly what was captured.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, inspect, text

SCHEMA_VERSION = "2026-09-14-association-engine"


@dataclass(frozen=True)
class AddColumn:
    table: str
    column: str
    ddl: str


# Additive column changes, applied only when the column is absent.
COLUMN_MIGRATIONS: tuple[AddColumn, ...] = (
    AddColumn("observations", "association_id", "VARCHAR"),
)

INDEX_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("ix_observations_association_id", "observations", "association_id"),
)


def pending(engine: Engine) -> list[str]:
    """Names of migrations that have not been applied yet."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    outstanding: list[str] = []
    for migration in COLUMN_MIGRATIONS:
        if migration.table not in tables:
            continue
        columns = {column["name"] for column in inspector.get_columns(migration.table)}
        if migration.column not in columns:
            outstanding.append(f"{migration.table}.{migration.column}")
    return outstanding


def run_migrations(engine: Engine) -> list[str]:
    """Apply every outstanding additive migration. Safe to call repeatedly."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    applied: list[str] = []
    with engine.begin() as connection:
        for migration in COLUMN_MIGRATIONS:
            if migration.table not in tables:
                # Table does not exist yet; create_all builds it with the
                # column already present.
                continue
            columns = {column["name"] for column in inspector.get_columns(migration.table)}
            if migration.column in columns:
                continue
            connection.execute(text(
                f"ALTER TABLE {migration.table} ADD COLUMN {migration.column} {migration.ddl}"
            ))
            applied.append(f"{migration.table}.{migration.column}")
        for index_name, table, column in INDEX_MIGRATIONS:
            if table not in tables and table not in inspect(engine).get_table_names():
                continue
            connection.execute(text(
                f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({column})"
            ))
    return applied
