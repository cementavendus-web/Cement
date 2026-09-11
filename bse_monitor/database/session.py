"""Engine and session management."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

log = logging.getLogger(__name__)

_engine: Optional[Engine] = None
_Session: Optional[sessionmaker] = None


def build_engine(url: str, echo: bool = False, pool_size: int = 5, max_overflow: int = 10) -> Engine:
    kwargs: dict = {"echo": echo, "future": True, "pool_pre_ping": True}
    if url.startswith("postgresql"):
        kwargs.update(pool_size=pool_size, max_overflow=max_overflow)
    else:
        # SQLite (tests): a shared in-memory DB needs the same connection reused.
        kwargs.pop("pool_pre_ping", None)
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _enable_fk(dbapi_conn, _record):  # pragma: no cover - driver hook
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def init_engine(url: str, **kwargs) -> Engine:
    global _engine, _Session
    _engine = build_engine(url, **kwargs)
    _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Engine not initialised; call init_engine(url) first.")
    return _engine


def create_all(engine: Optional[Engine] = None) -> None:
    Base.metadata.create_all(engine or get_engine())
    log.info("Schema ensured", extra={"tables": len(Base.metadata.tables)})


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on any exception."""
    if _Session is None:
        raise RuntimeError("Session factory not initialised; call init_engine(url) first.")
    session = _Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
