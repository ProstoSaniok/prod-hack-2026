from collections.abc import AsyncIterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


@pytest.fixture
async def wallet_engine(core_engine: Engine) -> AsyncIterator[AsyncEngine]:
    # Parent fixture applies real T02 migrations in a new task-owned schema.
    engine = create_async_engine(core_engine.url, connect_args={"connect_timeout": 3})
    try:
        yield engine
    finally:
        await engine.dispose()
