"""Engine and session factory."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from ledger.config import Settings


def build_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,  # transparently replace connections the database has dropped
    )


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    # expire_on_commit=False lets handlers read objects after commit without a new query.
    return sessionmaker(bind=engine, expire_on_commit=False)
