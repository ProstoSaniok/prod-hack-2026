import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import unquote
from uuid import uuid4

import httpx
import pytest

from src.adapters.gitea.config import GiteaConfiguration
from src.adapters.gitea.transport import GiteaTransport
from src.application.common.containers import SecretString
from src.application.operations.entity import OutboxMessage, ProjectionSpec
from src.application.operations.recovery import DependencyError, RunResult, result_for
from src.application.operations.work import FailureCode, RetryPolicy, Work, WorkState
from src.application.review.entity import DiffSide, ReviewVerdict, RevisionContent
from src.application.review.projection import (
    TOPIC,
    ProjectionComment,
    PublishedReviewProjection,
)


def projection() -> PublishedReviewProjection:
    project, submission = uuid4(), uuid4()
    return PublishedReviewProjection(
        review_id=uuid4(),
        revision_id=uuid4(),
        revision_number=1,
        repository_name=f"prodguild-project-{project.hex}",
        repository_id=12,
        service_owner_id=1,
        pull_number=3,
        pull_id=41,
        pull_marker=f"prodguild-submission:{submission}",
        base_branch=f"prodguild/bases/{project}",
        base_sha="b" * 40,
        head_branch=f"prodguild/submissions/{submission}",
        head_sha="c" * 40,
        content=RevisionContent(
            score=Decimal("80.25"),
            verdict=ReviewVerdict.APPROVED,
            strengths="Сильные стороны",
            problems="Проблемы",
            next_steps="Дальнейшие шаги",
        ),
        comments=(
            ProjectionComment(text="General feedback"),
            ProjectionComment(text="Fix old line", path="obsolete.txt", side=DiffSide.OLD, line=2),
            ProjectionComment(text="Fix new line", path="solution.txt", side=DiffSide.NEW, line=3),
        ),
    )


class MemoryProjectionStore:
    def __init__(self, item: PublishedReviewProjection | None) -> None:
        self.projection = item
        self.now = datetime.now(UTC)
        self.message = OutboxMessage(
            id=uuid4(),
            spec=ProjectionSpec(
                topic=TOPIC,
                source_key=str(item.revision_id) if item else str(uuid4()),
                subject_id=item.review_id if item else uuid4(),
                revision=1,
            ),
            work=Work(created_at=self.now, available_at=self.now),
        )
        self.post_started = False
        self.receipt: int | None = None
        self.fail_finalize = False

    async def claim(self, *, owner: str, policy: RetryPolicy) -> OutboxMessage | None:
        if not self.message.work.is_due(self.now):
            return None
        work = self.message.work.claim(now=self.now, owner=owner, token=uuid4(), policy=policy)
        self.message = replace(self.message, work=work)
        return self.message if work.state == WorkState.RUNNING else None

    async def load(self, message: OutboxMessage) -> PublishedReviewProjection | None:
        return self.projection

    async def reserve_post(self, message: OutboxMessage, projection: PublishedReviewProjection) -> bool:
        if self.post_started:
            return False
        self.post_started = True
        return True

    async def finish(
        self,
        message: OutboxMessage,
        *,
        projection: PublishedReviewProjection | None,
        external_id: int | None,
        error: FailureCode | None,
        policy: RetryPolicy,
    ) -> RunResult:
        if self.fail_finalize:
            self.fail_finalize = False
            raise RuntimeError("private DB error must not escape")
        lease, expected = self.message.work.lease, message.work.lease
        if lease is None or expected is None or lease.token != expected.token or lease.until <= self.now:
            return RunResult.LEASE_LOST
        work = (
            self.message.work.succeed(token=lease.token, now=self.now)
            if error is None
            else self.message.work.fail(token=lease.token, now=self.now, code=error, policy=policy)
        )
        self.message = replace(self.message, work=work)
        if error is None:
            self.receipt = external_id
        return result_for(work)


class MemoryProjectionStorage:
    def __init__(self) -> None:
        self.comments: dict[str, int] = {}
        self.lookups = 0
        self.posts = 0
        self.failure: FailureCode | None = None

    async def lookup(self, projection: PublishedReviewProjection) -> int | None:
        self.lookups += 1
        if self.failure:
            raise DependencyError(self.failure)
        return self.comments.get(projection.body)

    async def publish(self, projection: PublishedReviewProjection) -> int:
        self.posts += 1
        self.comments[projection.body] = 77
        return 77


class MockGitea:
    """No live services/provisioning; all transport requests are MockTransport."""

    def __init__(self, item: PublishedReviewProjection) -> None:
        self.item = item
        self.requests: list[httpx.Request] = []
        self.comments: list[dict[str, object]] = []
        self.lost_response = False
        self.public = False
        self.foreign_author = False
        self.wrong_sha = False
        self.wrong_pr = False
        self.wrong_comment_issue = False
        self.path = f"/api/v1/repos/service/{item.repository_name}"

    @staticmethod
    def owner() -> dict[str, object]:
        return {"id": 1, "login": "service"}

    def repository(self) -> dict[str, object]:
        return {
            "id": self.item.repository_id,
            "owner": self.owner(),
            "name": self.item.repository_name,
            "description": "private project",
            "private": not self.public,
            "fork": False,
            "empty": False,
        }

    def comment(self, body: str) -> dict[str, object]:
        return {
            "id": 77,
            "body": body,
            "user": {"id": 2, "login": "foreign"} if self.foreign_author else self.owner(),
            "issue_url": "http://gitea.test"
            + self.path
            + f"/issues/{99 if self.wrong_comment_issue else self.item.pull_number}",
        }

    @staticmethod
    def protection(branch: str) -> dict[str, object]:
        rule: dict[str, object] = dict.fromkeys(
            (
                "enable_push",
                "enable_push_whitelist",
                "push_whitelist_deploy_keys",
                "enable_force_push",
                "enable_force_push_allowlist",
                "force_push_allowlist_deploy_keys",
                "enable_bypass_allowlist",
            ),
            False,
        )
        rule.update({
            "rule_name": branch,
            "enable_merge_whitelist": True,
            "block_admin_merge_override": True,
            "merge_whitelist_usernames": [],
            "merge_whitelist_teams": [],
            "bypass_allowlist_usernames": [],
            "bypass_allowlist_teams": [],
            "unprotected_file_patterns": "",
        })
        return rule

    def comment_response(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            payload: object = json.loads(request.content)
            assert isinstance(payload, dict)
            assert isinstance(payload["body"], str)
            self.comments.append(self.comment(payload["body"]))
            if self.lost_response:
                raise httpx.ReadTimeout("private token must not escape", request=request)
            return httpx.Response(201, json=self.comments[-1])
        return httpx.Response(200, json=self.comments if request.url.params.get("page") == "1" else [])

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        item = self.item
        if path == "/api/v1/user":
            return httpx.Response(200, json=self.owner())
        if path == self.path:
            return httpx.Response(200, json=self.repository())
        if "/branches/" in path:
            branch = unquote(path.split("/branches/", 1)[1])
            sha = item.base_sha if branch == item.base_branch else item.head_sha
            return httpx.Response(
                200,
                json={
                    "name": branch,
                    "commit": {"id": sha},
                    "protected": True,
                    "effective_branch_protection_name": branch,
                },
            )
        if "/branch_protections/" in path:
            return httpx.Response(200, json=self.protection(unquote(path.split("/branch_protections/", 1)[1])))
        if path == self.path + f"/pulls/{item.pull_number}":

            def ref(branch: str, sha: str) -> dict[str, object]:
                return {"ref": branch, "sha": sha, "repo_id": item.repository_id, "repo": self.repository()}

            return httpx.Response(
                200,
                json={
                    "id": 999 if self.wrong_pr else item.pull_id,
                    "number": item.pull_number,
                    "user": self.owner(),
                    "title": "prodguild:" + hashlib.sha256(item.pull_marker.encode()).hexdigest(),
                    "body": item.pull_marker,
                    "state": "open",
                    "merged": False,
                    "base": ref(item.base_branch, item.base_sha),
                    "head": ref(item.head_branch, "d" * 40 if self.wrong_sha else item.head_sha),
                },
            )
        if path == self.path + f"/issues/{item.pull_number}/comments":
            return self.comment_response(request)
        if path == self.path + "/issues/comments/77":
            return httpx.Response(200, json=self.comments[0])
        pytest.fail(f"Unexpected request: {request.method} {path}")


def transport(client: httpx.AsyncClient) -> GiteaTransport:
    return GiteaTransport(
        client,
        GiteaConfiguration(
            internal_api_url=SecretString("http://gitea.test"),
            api_token=SecretString("private-token"),
        ),
    )
