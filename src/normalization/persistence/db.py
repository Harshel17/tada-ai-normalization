from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..settings import get_settings


def make_engine(url: str | None = None) -> Engine:
    return create_engine(url or get_settings().database_url, pool_pre_ping=True)


@contextmanager
def transaction(engine: Engine) -> Iterator[Session]:
    """One session, one transaction: commit if the block succeeds, roll back if it raises."""
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session, session.begin():
        yield session
