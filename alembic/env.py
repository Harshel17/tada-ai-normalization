from alembic import context
from sqlalchemy import create_engine, pool

from normalization.persistence.tables import Base
from normalization.settings import get_settings

config = context.config
target_metadata = Base.metadata


def database_url() -> str:
    # `alembic -x url=...` (used by the tests) overrides DATABASE_URL.
    return context.get_x_argument(as_dictionary=True).get("url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
