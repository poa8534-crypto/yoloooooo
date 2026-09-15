"""The ledger reset is destructive, so what it does and does not touch is pinned.

The dangerous failure is not deleting too little. It is leaving the append-only
triggers off afterwards, or wiping the quota reservations that mirror real spend
against a provider's allowance.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.migrations import append_only_tables
from scripts.reset_ledger import TABLES, reset


@pytest.fixture
def populated(session_factory, settings):
    """A ledger holding one run's worth of everything, plus quota state."""
    from app.models import AuditRecord, Proposal, SystemState
    from tests.test_deep_research import VALID, seed

    seed(session_factory)
    with session_factory() as db:
        from app.models import Candidate
        from sqlalchemy import select

        candidate_id = db.scalar(select(Candidate.id))
        db.add(Proposal(candidate_id=candidate_id, agent="meta_hunter", payload=VALID, model_name="fake"))
        db.add(AuditRecord(candidate_id=candidate_id, proposal_id=None, payload={
            "candidate_id": candidate_id, "proposal": None, "gates": [], "risks": ["r"],
            "evidence_state": "blocked", "decision": "collection_only", "note": "n",
        }))
        db.add(SystemState(key=f"quota:youtube:{datetime.now(UTC).date()}",
                           value_json={"reserved": 3621, "allowance": 10000, "remote_remaining": "unknown"}))
        db.add(SystemState(key="last_snapshot", value_json={"completed_at": "2026-09-15T00:00:00+00:00"}))
        db.commit()
    return session_factory.kw["bind"]


def _count(engine, table: str) -> int:
    with engine.begin() as connection:
        return connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0


def test_the_reset_empties_every_ledger_table(populated, settings):
    engine = populated
    assert _count(engine, "observations") > 0, "the fixture seeded nothing"
    reset(drop_artifacts=False, target=engine, artifact_dir=settings.artifact_dir)
    with engine.begin() as connection:
        # security_redactions is created on demand by the credential migration,
        # so it is legitimately absent from a fresh ledger.
        existing = {row[0] for row in connection.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        )}
    checked = [table for table in TABLES if table in existing]
    assert len(checked) >= len(TABLES) - 1, sorted(set(TABLES) - existing)
    for table in checked:
        assert _count(engine, table) == 0, f"{table} still holds rows after a full reset"


def test_the_reset_keeps_quota_reservations(populated, settings):
    """Zeroing these would let the next run spend past an allowance the
    provider is still counting."""
    engine = populated
    reset(drop_artifacts=False, target=engine, artifact_dir=settings.artifact_dir)
    with engine.begin() as connection:
        keys = [row[0] for row in connection.execute(text("SELECT key FROM system_state"))]
    assert any(key.startswith("quota:") for key in keys), "real API spend was erased"
    assert "last_snapshot" not in keys, "stale scheduler state survived the reset"


def test_the_ledger_is_append_only_again_afterwards(populated, session_factory, settings):
    """The reset has to drop the triggers to do its work. Leaving them off
    would silently turn the ledger into an ordinary mutable table."""
    from tests.test_deep_research import seed

    engine = populated
    reset(drop_artifacts=False, target=engine, artifact_dir=settings.artifact_dir)
    seed(session_factory)

    with engine.begin() as connection:
        names = {row[0] for row in connection.execute(
            text("SELECT name FROM sqlite_master WHERE type='trigger'")
        )}
    tables = set(append_only_tables())
    assert tables, "no append-only tables are declared"
    for table in tables:
        assert f"trg_{table}_no_update" in names, f"{table} lost its update guard"
        assert f"trg_{table}_no_delete" in names, f"{table} lost its delete guard"

    with pytest.raises(Exception):
        with engine.begin() as connection:
            connection.execute(text("UPDATE observations SET value_json = '0'"))


def test_the_reset_removes_captured_payload_files(populated, settings):
    """A hash in the ledger with no bytes behind it is worse than neither."""
    artifacts = settings.artifact_dir
    assert list(artifacts.iterdir()), "the fixture captured no payload files"
    reset(drop_artifacts=True, target=populated, artifact_dir=artifacts)
    assert list(artifacts.iterdir()) == []


def test_payload_files_can_be_kept(populated, settings):
    """Positive control: the caller chooses, and wiping is not the only path."""
    before = {path.name for path in settings.artifact_dir.iterdir()}
    assert before
    reset(drop_artifacts=False, target=populated, artifact_dir=settings.artifact_dir)
    assert {path.name for path in settings.artifact_dir.iterdir()} == before


def test_the_reset_covers_every_declared_table():
    """A table added later must not be silently skipped by the wipe.

    This nearly happened: two tables appeared in the models while the reset
    script listed the ones that existed when it was written, so a "full reset"
    would have quietly left them populated.
    """
    import app.models  # noqa: F401  (registers every table on the metadata)
    from app.db import Base

    declared = {table.name for table in Base.metadata.sorted_tables}
    # system_state is handled separately: quota rows survive on purpose.
    declared.discard("system_state")
    missing = declared - set(TABLES)
    assert not missing, f"the reset would leave these tables populated: {sorted(missing)}"


def test_a_caller_supplied_ledger_may_not_wipe_the_live_payload_directory(populated, settings):
    """A throwaway engine with no artifact_dir used to fall back to the real
    one, so an unguarded path could delete payloads belonging to a different
    database entirely. It did exactly that during a mutation run.
    """
    with pytest.raises(ValueError, match="artifact_dir"):
        reset(drop_artifacts=True, target=populated)
    with pytest.raises(ValueError, match="artifact_dir"):
        reset(drop_artifacts=False, target=populated)
    assert list(settings.artifact_dir.iterdir()), "the guard fired but files were still removed"
