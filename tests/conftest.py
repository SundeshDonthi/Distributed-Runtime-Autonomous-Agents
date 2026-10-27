from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from runtime.state.orm import Base


@pytest.fixture()
def db_session():
    """An isolated in-memory SQLite DB per test, mirroring the Postgres schema
    closely enough (JSON columns, string FKs) to exercise the repository layer
    without requiring a live Postgres instance for unit tests.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
