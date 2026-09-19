from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings
from .ownership import Held


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
    from .migrations import (
        drop_append_only_triggers,
        install_append_only_triggers,
        run_migrations,
    )

    target = bind or engine
    # Triggers guard the append-only tables against every writer, including
    # this one, so they come off for the migration and go straight back on.
    # Additive migrations run first so that create_all never sees a table it
    # would otherwise consider up to date while a column is still missing.
    drop_append_only_triggers(target)
    try:
        run_migrations(target)
        Base.metadata.create_all(bind=target)
        from .migrations import redact_credential_urls
        redact_credential_urls(target)
    finally:
        install_append_only_triggers(target)


def _url(factory):
    bind = getattr(factory, "kw", {}).get("bind")
    return getattr(bind, "url", None)


def _private(url) -> bool:
    """A ledger no other process can reach: an in-memory database."""
    return url is None or (url.get_backend_name() == "sqlite"
                           and url.database in (None, "", ":memory:"))


def lock_home(factory) -> Path:
    """Where the locks for this ledger's records live.

    Beside the database file, so two processes pointed at the same file find
    the same locks however each spelled its path, and a checkout with a
    database of its own -- the test suite's -- never shares them with the
    running service. A private ledger's locks still have to be real files, but
    nothing outside this process will ever look for them. Any other backend
    keeps them per machine under `lock_dir`, keyed by its URL without the
    password.
    """
    url = _url(factory)
    if _private(url):
        return Path(tempfile.gettempdir()) / f"venture-agents-locks-{os.getpid()}"
    if url.get_backend_name() == "sqlite":
        path = Path(url.database).resolve()
        return path.with_name(path.name + ".locks")
    key = hashlib.sha256(url.render_as_string(hide_password=True).encode()).hexdigest()[:16]
    return get_settings().lock_dir / f"ledger-{key}"


def ledger_lock(factory) -> Held | None:
    """The lock a service holds for as long as it is using this ledger, or
    None for a private ledger, which no second service could reach."""
    if _private(_url(factory)):
        return None
    return Held(lock_home(factory) / "service.lock")


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
