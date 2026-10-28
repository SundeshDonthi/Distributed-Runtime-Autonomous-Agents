from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import runtime.state.db as db_module
from runtime.state.orm import Base


@pytest.fixture()
def db_session(monkeypatch):
    """An isolated in-memory SQLite DB per test, mirroring the Postgres schema
    closely enough (JSON columns, string FKs) to exercise the repository layer
    without requiring a live Postgres instance for unit tests.

    Also rebinds `runtime.state.db`'s global `engine`/`SessionLocal` to this
    same test engine for the duration of the test. Most code takes a
    `session` parameter and doesn't care, but anything that opens its own
    short-lived session via `runtime.state.db.get_session()` -- e.g. a tool
    function, which by contract takes no session argument -- would otherwise
    silently land in a completely different (real Postgres) database than
    the one this fixture hands to the test.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", SessionLocal)

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
