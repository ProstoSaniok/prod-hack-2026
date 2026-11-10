import json

import httpx
import pytest

from src.adapters.gitea.config import GiteaConfiguration
from src.adapters.gitea.git import GiteaGitStorage
from src.adapters.gitea.transport import GiteaTransport
from src.application.common.containers import SecretString
from src.application.repository_storage.git import GitPullRequest, GitStorage, StorageConflictError

_PATH = "/api/v1/repos/service/prodguild-test"
_BASE, _HEAD = "prodguild/bases/one", "prodguild/submissions/one"
_BASE_SHA, _HEAD_SHA = "a" * 40, "c" * 40
_REPOSITORY = {
    "id": 12,
    "owner": {"id": 7, "login": "service"},
    "name": "prodguild-test",
    "description": "prodguild:project:one",
    "private": True,
    "fork": False,
    "empty": False,
}


def _ref(branch: str, sha: str, repository_id: int = 12) -> dict[str, object]:
    return {"ref": branch, "sha": sha, "repo_id": repository_id, "repo": {**_REPOSITORY, "id": repository_id}}


class _Pulls:
    def __init__(self) -> None:
        self.pull: dict[str, object] | None = None
        self.posts = 0

    def respond(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "token private-token"
        assert request.method == "GET" or (request.method == "POST" and request.url.path == _PATH + "/pulls")
        if request.method == "POST":
            self.posts += 1
            payload = json.loads(request.content)
            assert payload["base"] == _BASE
            assert payload["head"] == _HEAD  # Never an owner:fork branch.
            assert payload["body"] == "prodguild:submission:one"
            self.pull = {
                "id": 42,
                "number": 3,
                "user": {"id": 7, "login": "service"},
                "title": payload["title"],
                "body": payload["body"],
                "state": "open",
                "merged": False,
                "base": _ref(_BASE, _BASE_SHA),
                "head": _ref(_HEAD, _HEAD_SHA),
            }
            raise httpx.ReadError("private-token response lost", request=request)
        if request.url.path == "/api/v1/user":
            return httpx.Response(200, json={"id": 7, "login": "service"})
        if request.url.path == _PATH:
            return httpx.Response(200, json=_REPOSITORY)
        for branch, sha in ((_BASE, _BASE_SHA), (_HEAD, _HEAD_SHA)):
            if request.url.path == _PATH + "/branches/" + branch:
                return httpx.Response(
                    200,
                    json={
                        "name": branch,
                        "commit": {"id": sha},
                        "protected": True,
                        "effective_branch_protection_name": branch,
                    },
                )
            if request.url.path == _PATH + "/branch_protections/" + branch:
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
                    "merge_whitelist_usernames": None,
                    "merge_whitelist_teams": [],
                    "bypass_allowlist_usernames": [],
                    "bypass_allowlist_teams": [],
                    "unprotected_file_patterns": "",
                })
                return httpx.Response(200, json=rule)
        if request.url.path == _PATH + "/pulls":
            assert request.url.params["state"] == "all"
            page = int(request.url.params["page"])
            unrelated = {
                "id": 99,
                "number": 99,
                "user": {"id": 7, "login": "service"},
                "title": "unrelated",
                "body": None,
                "state": "closed",
                "merged": False,
                "base": _ref("other-base", _BASE_SHA),
                "head": _ref("other-head", _HEAD_SHA),
            }
            return httpx.Response(
                200, json=[unrelated] if page == 1 else [self.pull] if page == 2 and self.pull else []
            )
        if request.url.path == _PATH + "/pulls/3":
            return httpx.Response(200, json=self.pull)
        pytest.fail(f"Unexpected request: {request.method} {request.url.path}")


async def test_pull_request_response_loss_replay_and_foreign_head_fail_closed() -> None:
    fixture = _Pulls()
    configuration = GiteaConfiguration(
        internal_api_url=SecretString("http://private-gitea.invalid"), api_token=SecretString("private-token")
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.respond)) as client:
        storage: GitStorage = GiteaGitStorage(GiteaTransport(client, configuration))

        async def ensure() -> GitPullRequest:
            return await storage.ensure_pull_request(
                "prodguild-test",
                base_branch=_BASE,
                base_sha=_BASE_SHA,
                head_branch=_HEAD,
                head_sha=_HEAD_SHA,
                marker="prodguild:submission:one",
            )

        first = await ensure()
        assert first == GitPullRequest(12, 7, 3, 42, _BASE_SHA, _HEAD_SHA)
        assert await ensure() == first
        assert fixture.pull is not None
        original = fixture.pull
        for changes in (
            {"head": _ref(_HEAD, _HEAD_SHA, 999)},
            {"head": _ref(_HEAD, "d" * 40)},
            {"state": "closed"},
        ):
            fixture.pull = {**original, **changes}
            with pytest.raises(StorageConflictError) as caught:
                await ensure()
            assert caught.value.__context__ is None
            assert "private-token" not in str(caught.value)
    assert fixture.posts == 1
