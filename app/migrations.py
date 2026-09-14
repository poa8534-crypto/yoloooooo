"""Forward-only, idempotent schema migrations.

The ledger is append-only and already holds captured evidence, so migrations
here may only add tables and nullable columns. Nothing drops, rewrites or
back-fills an existing row: the stored evidence is exactly what was captured.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, inspect, text


@dataclass(frozen=True)
class AddColumn:
    table: str
    column: str
    ddl: str


# Additive column changes, applied only when the column is absent.
COLUMN_MIGRATIONS: tuple[AddColumn, ...] = (
    AddColumn("observations", "association_id", "VARCHAR"),
    AddColumn("association_reviews", "sequence", "INTEGER"),
    AddColumn("association_overrides", "sequence", "INTEGER"),
    AddColumn("match_subjects", "supersedes_subject_id", "VARCHAR"),
)

INDEX_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("ix_observations_association_id", "observations", "association_id"),
    ("ix_association_reviews_sequence", "association_reviews", "sequence"),
    ("ix_association_overrides_sequence", "association_overrides", "sequence"),
    ("ix_match_subjects_supersedes", "match_subjects", "supersedes_subject_id"),
)


def append_only_tables() -> tuple[str, ...]:
    from .models import APPEND_ONLY

    return tuple(model.__tablename__ for model in APPEND_ONLY)


def install_append_only_triggers(engine: Engine) -> list[str]:
    """Enforce append-only in the database, not just in the ORM.

    The ORM event hooks only fire for attribute-level writes. A bulk
    `UPDATE`/`DELETE`, a raw SQL statement, or anything opening the file with
    another SQLite client walks straight past them. These triggers are what
    actually makes the ledger append-only.

    A future migration that genuinely needs to rewrite rows has to drop the
    triggers, do the work, and reinstall them — deliberately awkward.
    """
    if engine.dialect.name != "sqlite":
        # Other backends need their own rules; refusing silently would be worse
        # than saying so.
        raise RuntimeError(
            f"append-only enforcement is implemented for SQLite only, not {engine.dialect.name}"
        )
    tables = set(inspect(engine).get_table_names())
    installed: list[str] = []
    with engine.begin() as connection:
        for table in append_only_tables():
            if table not in tables:
                continue
            for operation in ("UPDATE", "DELETE"):
                name = f"trg_{table}_no_{operation.lower()}"
                connection.execute(text(
                    f"CREATE TRIGGER IF NOT EXISTS {name} "
                    f"BEFORE {operation} ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
                ))
                installed.append(name)
    return installed


def drop_append_only_triggers(engine: Engine) -> list[str]:
    """Remove the append-only triggers so a migration can rewrite rows."""
    if engine.dialect.name != "sqlite":
        return []
    dropped: list[str] = []
    with engine.begin() as connection:
        for table in append_only_tables():
            for operation in ("update", "delete"):
                name = f"trg_{table}_no_{operation}"
                connection.execute(text(f"DROP TRIGGER IF EXISTS {name}"))
                dropped.append(name)
    return dropped


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
