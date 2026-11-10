import argparse
import asyncio
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from dishka import AsyncContainer
from dishka.integrations.fastapi import setup_dishka
from fastapi import FastAPI
from prometheus_client import CollectorRegistry, GCCollector, PlatformCollector, ProcessCollector
from prometheus_fastapi_instrumentator import Instrumentator

from src.adapters.operations.worker import ManagedWorker
from src.application.auth_identity.errors import AuthError, AuthUnavailableError, ForbiddenError, UserAlreadyExistsError
from src.web_api.configuration import Configuration, load_configuration
from src.web_api.http.errors import (
    auth_error_handler,
    auth_unavailable_handler,
    conflict_error_handler,
    forbidden_error_handler,
)
from src.web_api.ioc.make import make_ioc
from src.web_api.routes import (
    auth,
    case_management,
    competencies,
    development,
    healthcheck,
    learning,
    my_skills,
    priority,
    profile,
    readiness,
    review_quality,
    review_results,
    reviews,
    wallet,
)
from src.web_api.telemetry import Telemetry, configure_telemetry


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    telemetry: Telemetry | None = app.state.telemetry
    if telemetry is not None:
        telemetry.start_logging()
    try:
        worker = await app.state.dishka_container.get(ManagedWorker)
        async with worker:
            yield
    finally:
        try:
            await app.state.dishka_container.close()
        finally:
            if telemetry is not None:
                await asyncio.to_thread(telemetry.shutdown)


def make_asgi_application(ioc: AsyncContainer) -> FastAPI:
    app = FastAPI(
        lifespan=lifespan,
        title="Template API",
        description="FastAPI backend template",
        version="1.0.0",
    )
    app.include_router(healthcheck.router)
    app.include_router(readiness.router)
    app.include_router(auth.router)
    app.include_router(competencies.router)
    app.include_router(my_skills.router)
    app.include_router(wallet.router)
    app.include_router(case_management.router)
    app.include_router(learning.router)
    app.include_router(reviews.router)
    app.include_router(review_results.router)
    app.include_router(review_quality.router)
    app.include_router(priority.router)
    app.include_router(profile.router)
    app.include_router(development.router)
    app.add_exception_handler(AuthError, auth_error_handler)
    app.add_exception_handler(ForbiddenError, forbidden_error_handler)
    app.add_exception_handler(UserAlreadyExistsError, conflict_error_handler)
    app.add_exception_handler(AuthUnavailableError, auth_unavailable_handler)
    registry = CollectorRegistry()
    GCCollector(registry=registry)
    PlatformCollector(registry=registry)
    ProcessCollector(registry=registry)
    Instrumentator(registry=registry).instrument(app).expose(app)
    setup_dishka(container=ioc, app=app)
    app.state.telemetry = configure_telemetry(app)
    return app


def make_server(configuration: Configuration) -> uvicorn.Server:
    app = make_asgi_application(make_ioc(configuration))
    return uvicorn.Server(
        uvicorn.Config(
            app=app,
            host=configuration.server.host,
            port=configuration.server.port,
            access_log=configuration.server.access_log,
        ),
    )


async def run_server(configuration_path: Path) -> None:
    if os.getenv("LOCAL_DEMO_SEED") == "true":
        from src.web_api.local_demo import seed  # noqa: PLC0415

        await seed(configuration_path)
    server = make_server(load_configuration(configuration_path))
    await server.serve()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("configuration", nargs="?", default=None)
    args = parser.parse_args()
    configuration_path = args.configuration or os.getenv("CONFIGURATION_PATH")
    if configuration_path is None:
        parser.error("pass a configuration path or set CONFIGURATION_PATH")
    asyncio.run(run_server(Path(configuration_path)))


if __name__ == "__main__":
    main()
