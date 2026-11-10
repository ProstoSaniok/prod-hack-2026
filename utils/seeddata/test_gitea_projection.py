"""Seed-only Gitea 28 comments; ordinary backend adapter stays untouched."""

import httpx
import pytest

from utils.seeddata.server import SeedReviewProjectionProvider
from utils.seeddata.gitea_projection import SeedGiteaProjectionStorage
from src.adapters.operations.worker import ManagedWorker
from src.adapters.gitea.review_projection import GiteaReviewProjectionStorage
from src.application.operations.recovery import DependencyError
from src.application.operations.work import FailureCode
from src.web_api.ioc.make import make_ioc
from src.web_api.configuration import (
    Configuration,
    ServerConfiguration,
    DatabaseConfiguration,
    AccessTokenConfiguration,
)
from src.application.common.containers import SecretString
from cryptography.fernet import Fernet
from tests.support.review_projection import MockGitea, projection, transport


class SeedMockGitea(MockGitea):
    def comment(self, body):
        return {
            **super().comment(body),
            "issue_url": "",
            "pull_request_url": f"http://gitea.test/service/{self.item.repository_name}/pulls/{self.item.pull_number}",
        }

    def comment_response(self, request):
        if request.method == "POST":
            return super().comment_response(request)
        return httpx.Response(200, json=self.comments)

    def __call__(self, request):
        if request.url.path == self.path + "/issues/comments/77":
            self.requests.append(request)
            return httpx.Response(
                200, json=next(c for c in self.comments if c["id"] == 77)
            )
        return super().__call__(request)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem",
    [
        "none",
        "wrong_pull",
        "wrong_repository",
        "query",
        "credentials",
        "empty",
        "duplicate",
        "oversized",
        "foreign_author",
    ],
)
async def test_seed_projection_checks_gitea28_receipts(problem):
    item = projection()
    mock = SeedMockGitea(item)
    comment = mock.comment(item.body)
    url = comment["pull_request_url"]
    if problem == "wrong_pull":
        comment["pull_request_url"] = url + "9"
    elif problem == "wrong_repository":
        comment["pull_request_url"] = url.replace(item.repository_name, "another")
    elif problem == "query":
        comment["pull_request_url"] = url + "?target=other"
    elif problem == "credentials":
        comment["pull_request_url"] = url.replace("gitea.test", "user@gitea.test")
    elif problem == "empty":
        comment["pull_request_url"] = ""
    elif problem == "foreign_author":
        comment["user"] = {"id": 2, "login": "other"}
    mock.comments = [{**mock.comment("Unrelated"), "id": i} for i in range(1, 61)] + [
        comment
    ]
    if problem == "duplicate":
        mock.comments.append({**comment, "id": 78})
    elif problem == "oversized":
        mock.comments = [{**mock.comment("Unrelated"), "id": i} for i in range(1, 1002)]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(mock), trust_env=False
    ) as client:
        storage = SeedGiteaProjectionStorage(transport(client))
        if problem == "none":
            assert await storage.lookup(item) == 77
        else:
            with pytest.raises(DependencyError) as caught:
                await storage.lookup(item)
            assert caught.value.code == (
                FailureCode.UNAVAILABLE
                if problem == "oversized"
                else FailureCode.REJECTED
            )
    assert all(r.method == "GET" and not r.url.query for r in mock.requests)


@pytest.mark.asyncio
async def test_seed_projection_lost_post_response_does_not_duplicate():
    item = projection()
    mock = SeedMockGitea(item)
    mock.lost_response = True
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(mock), trust_env=False
    ) as client:
        storage = SeedGiteaProjectionStorage(transport(client))
        assert await storage.publish(item) == 77
        assert await storage.publish(item) == 77
    assert sum(r.method == "POST" for r in mock.requests) == 1


@pytest.mark.asyncio
async def test_seed_worker_provider_is_scoped_to_seed_server():
    config = Configuration(
        server=ServerConfiguration(),
        database=DatabaseConfiguration(
            url=SecretString("postgresql+psycopg://unused:unused@127.0.0.1:1/unused")
        ),
        access_token=AccessTokenConfiguration(
            crypto_key=SecretString(Fernet.generate_key().decode())
        ),
    )
    ordinary = make_ioc(config)
    seed = make_ioc(config, SeedReviewProjectionProvider())
    ordinary_worker = await ordinary.get(ManagedWorker)
    seed_worker = await seed.get(ManagedWorker)
    assert type(ordinary_worker.runner.storage) is GiteaReviewProjectionStorage
    assert type(seed_worker.runner.storage) is SeedGiteaProjectionStorage
    await ordinary.close()
    await seed.close()
