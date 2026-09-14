from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine(url: str):
    if url.startswith("sqlite:///"):
        db_path = Path(url.removeprefix("sqlite:///"))
        db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
        future=True,
    )
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return engine


engine = _make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def init_db(bind=None) -> None:
    from . import models  # noqa: F401
    from .migrations import run_migrations

    target = bind or engine
    # Additive migrations run first so that create_all never sees a table it
    # would otherwise consider up to date while a column is still missing.
    run_migrations(target)
    Base.metadata.create_all(bind=target)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

