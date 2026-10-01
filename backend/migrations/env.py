from alembic import context
from app.models import Base
from app.settings import Settings
from sqlalchemy import create_engine, pool

target_metadata = Base.metadata


def run_online_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        url=Settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()
elif context.config.attributes.get("connection") is not None:
    # Test fixtures provide the same connection whose database identity was verified.
    run_online_migrations(context.config.attributes["connection"])
else:
    engine = create_engine(Settings().database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        run_online_migrations(connection)
