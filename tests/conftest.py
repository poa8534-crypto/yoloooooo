from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite:///./data/test_app.db")

from app import models  # noqa: F401
from app.db import Base


def _memory_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db(tmp_path: Path):
    factory = sessionmaker(bind=_memory_engine(), expire_on_commit=False)
    with factory() as session:
        yield session


@pytest.fixture
def session_factory():
    """A session factory over a fresh in-memory ledger."""
    return sessionmaker(bind=_memory_engine(), expire_on_commit=False)
