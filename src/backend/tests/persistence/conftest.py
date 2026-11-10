import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema


def upgrade(revision: str = "head") -> None:
    command.upgrade(Config("alembic.ini"), revision)


@pytest.fixture
def preceding_engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """Run actual migrations only in a newly allocated schema on the explicit test DB."""
    raw_url = os.environ.get("PRODGUILD_MIGRATION_TEST_DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Set PRODGUILD_MIGRATION_TEST_DATABASE_URL or TEST_DATABASE_URL to an isolated migration test DB")
    url = make_url(raw_url)
    if url.drivername != "postgresql+psycopg":
        raise ValueError("Core migration tests require postgresql+psycopg")
    schema = f"t02_{uuid4().hex}"
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    isolated_url = url.update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(isolated_url)
    monkeypatch.setenv("CONFIGURATION_PATH", "config.example.toml")
    monkeypatch.setenv("DATABASE_URL", isolated_url.render_as_string(hide_password=False))
    try:
        upgrade("3a655675991b")
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


@pytest.fixture
def core_engine(preceding_engine: Engine) -> Engine:
    upgrade()
    return preceding_engine
