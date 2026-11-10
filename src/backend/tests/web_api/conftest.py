from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.web_api.configuration import load_configuration
from src.web_api.entry_point import make_asgi_application
from src.web_api.ioc.make import make_ioc


@pytest.fixture
async def backend() -> AsyncIterator[FastAPI]:
    configuration = load_configuration(Path(__file__).parents[2] / "config.example.toml")
    app = make_asgi_application(make_ioc(configuration))
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def http_client(backend: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=backend), base_url="http://testserver") as client:
        yield client
