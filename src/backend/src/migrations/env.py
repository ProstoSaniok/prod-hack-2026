import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

from src.adapters.data_gateways.tables import meta_data
from src.migrations.autogenerate import render_snapshot_version
from src.migrations import persistence_autogenerate as _persistence_autogenerate
from src.migrations import learning_autogenerate as _learning_autogenerate
from src.web_api.configuration import load_configuration

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

configuration = load_configuration(Path(os.environ.get("CONFIGURATION_PATH", "config.toml")))
url = configuration.database.url.get_value()


def run_migrations_offline() -> None:
    context.configure(
        url=url,
        target_metadata=meta_data,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=meta_data, render_item=render_snapshot_version)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
