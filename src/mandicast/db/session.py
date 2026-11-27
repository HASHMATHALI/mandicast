"""Engine and session factory. The URL comes from settings (SecretStr) and is never logged."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from mandicast.config import get_settings

_engines: dict[str, Engine] = {}


def get_engine(url: str | None = None) -> Engine:
    settings = get_settings()
    url = url or settings.database_url.get_secret_value()
    if url not in _engines:
        _engines[url] = create_engine(url, pool_pre_ping=True, hide_parameters=True,
                                      connect_args={"connect_timeout": settings.db_connect_timeout_s})
    return _engines[url]


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """One transaction: commit on success, roll back on any error."""
    factory = sessionmaker(bind=engine or get_engine(), expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
