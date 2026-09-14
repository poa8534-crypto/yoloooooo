"""Migrating an existing ledger in place, without losing captured evidence."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from app.config import ROOT
from app.db import Base, init_db
from app.migrations import COLUMN_MIGRATIONS, pending, run_migrations

# The schema as it stood before the association engine: observations had no
# association column and none of the association tables existed.
LEGACY_SCHEMA = """
CREATE TABLE source_artifacts (
    id VARCHAR NOT NULL PRIMARY KEY, url TEXT, publisher_owner VARCHAR(255),
    retrieval_method VARCHAR(80), captured_at DATETIME, sha256 VARCHAR(64),
    content_type VARCHAR(120), raw_path TEXT, source_tier VARCHAR(32),
    is_discovery_only BOOLEAN
);
CREATE TABLE research_runs (
    id VARCHAR NOT NULL PRIMARY KEY, niche VARCHAR(240), status VARCHAR(32),
    message TEXT, created_at DATETIME, completed_at DATETIME
);
CREATE TABLE candidates (
    id VARCHAR NOT NULL PRIMARY KEY, run_id VARCHAR, external_kind VARCHAR(32),
    external_id VARCHAR(80), display_name_observation_id VARCHAR, created_at DATETIME
);
CREATE TABLE observations (
    id VARCHAR NOT NULL PRIMARY KEY, artifact_id VARCHAR, candidate_id VARCHAR,
    metric VARCHAR(100), value_json JSON, unit VARCHAR(40),
    extraction_method VARCHAR(80), pointer TEXT, observed_at DATETIME
);
"""


@pytest.fixture
def legacy_db(tmp_path: Path) -> Path:
    path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    with engine.begin() as connection:
        for statement in filter(None, (s.strip() for s in LEGACY_SCHEMA.split(";"))):
            connection.execute(text(statement))
        connection.execute(text(
            "INSERT INTO source_artifacts VALUES "
            "('a1','https://games.roblox.com/v1/games','roblox.com','roblox_games_api',"
            "'2026-09-01 00:00:00','"
            + "f" * 64
            + "','application/json','/tmp/a1.json','primary',0)"
        ))
        connection.execute(text(
            "INSERT INTO observations VALUES "
            "('o1','a1','c1','roblox_playing',42,'players','json_pointer','/data/0/playing',"
            "'2026-09-01 00:00:00')"
        ))
    engine.dispose()
    return path


def test_pending_reports_the_missing_column_before_migrating(legacy_db: Path):
    engine = create_engine(f"sqlite:///{legacy_db.as_posix()}")
    outstanding = pending(engine)
    assert "observations.association_id" in outstanding
    assert "source_artifacts.raw_size" in outstanding
    # Only columns on tables the legacy schema actually has.
    assert all(item.split(".")[0] in {"observations", "source_artifacts"} for item in outstanding)
    engine.dispose()


def test_migration_adds_the_column_and_keeps_every_captured_row(legacy_db: Path):
    engine = create_engine(f"sqlite:///{legacy_db.as_posix()}")
    applied = run_migrations(engine)
    assert "observations.association_id" in applied

    columns = {column["name"] for column in inspect(engine).get_columns("observations")}
    assert "association_id" in columns
    with engine.begin() as connection:
        row = connection.execute(text(
            "SELECT id, metric, value_json, association_id FROM observations"
        )).one()
    # The captured value survives untouched; the new column is simply empty.
    assert row[0] == "o1"
    assert row[1] == "roblox_playing"
    assert int(row[2]) == 42
    assert row[3] is None
    engine.dispose()


def test_migration_is_idempotent(legacy_db: Path):
    engine = create_engine(f"sqlite:///{legacy_db.as_posix()}")
    run_migrations(engine)
    assert run_migrations(engine) == []
    assert pending(engine) == []
    engine.dispose()


def test_init_db_upgrades_a_legacy_ledger_and_creates_the_new_tables(legacy_db: Path):
    engine = create_engine(f"sqlite:///{legacy_db.as_posix()}")
    init_db(bind=engine)
    tables = set(inspect(engine).get_table_names())
    for table in (
        "match_subjects", "match_candidates", "association_records",
        "association_reviews", "association_overrides", "matcher_versions",
    ):
        assert table in tables
    with engine.begin() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM observations")).scalar() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM source_artifacts")).scalar() == 1
    engine.dispose()


def test_a_fresh_database_needs_no_migration(tmp_path: Path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}")
    Base.metadata.create_all(engine)
    assert pending(engine) == []
    engine.dispose()


def test_every_declared_migration_is_additive():
    # Guard the ledger's append-only promise: a migration may add, never drop
    # or rewrite.
    for migration in COLUMN_MIGRATIONS:
        assert migration.ddl.strip().upper().startswith(("VARCHAR", "TEXT", "INTEGER", "FLOAT"))
        assert "NOT NULL" not in migration.ddl.upper()


@pytest.mark.skipif(
    not (ROOT / "data" / "venture_agents.db").exists(),
    reason="no local ledger present",
)
def test_migration_runs_against_a_copy_of_the_real_ledger(tmp_path: Path):
    """Exercise the real database without touching it: work on a copy."""
    copy = tmp_path / "venture_agents.db"
    shutil.copy2(ROOT / "data" / "venture_agents.db", copy)
    engine = create_engine(f"sqlite:///{copy.as_posix()}")
    before = {}
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table in inspector.get_table_names():
            before[table] = connection.execute(
                text(f"SELECT COUNT(*) FROM {table}")  # table names come from the schema itself
            ).scalar()

    init_db(bind=engine)

    after_inspector = inspect(engine)
    assert "association_id" in {
        column["name"] for column in after_inspector.get_columns("observations")
    }
    with engine.begin() as connection:
        for table, count in before.items():
            assert connection.execute(
                text(f"SELECT COUNT(*) FROM {table}")
            ).scalar() == count
    assert "association_records" in set(after_inspector.get_table_names())
    engine.dispose()
