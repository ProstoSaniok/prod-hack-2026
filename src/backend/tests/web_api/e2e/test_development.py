import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.adapters.data_gateways.development_requests import DevelopmentRequests
from src.adapters.data_gateways.grade_advancement import SqlGradeAdvancement
from src.adapters.data_gateways.learning_views import row_mapping
from src.adapters.data_gateways.review_access import ReviewAccess
from src.adapters.data_gateways.tables import meta_data
from src.application.development.entity import SkillGrade, SkillGrantState
from src.application.operations.idempotency import CachedResponse
from src.application.repository_storage.git import GitStorage
from src.application.user.entity import UserId
from src.application.wallet.service import WalletCollaborator
from tests.support.learning import MemoryGit, edited_solution, learning_client, participant, submission_project
from tests.support.review_completion import complete, final_draft, save
from tests.support.review_quality import (
    assess,
    enable,
    expert,
    expire_request,
    pending_review,
    quality_effects,
    revoke,
)
from tests.support.reviews import Scenario, claim, submitted

__all__ = ["learning_client"]
BASE = "/api/v1/reviewer-applications"


@pytest.fixture
def learning_storage() -> GitStorage:
    return MemoryGit()


@dataclass(frozen=True)
class Candidate:
    own: Scenario
    expert: dict[str, str]
    expert_id: UUID


async def new_submission(
    client: AsyncClient,
    engine: AsyncEngine,
    case: Candidate,
    name: str,
    *,
    own: bool = False,
) -> Scenario:
    project, author, author_id, skills = await submission_project(
        client,
        engine,
        author_headers=case.own.author if own else None,
        skill_ids=case.own.skills,
        email_prefix=name,
    )
    sha = await edited_solution(client, project, author)
    response = await client.post(
        project + "/submissions", headers={**author, "Idempotency-Key": "submit"}, json={"expected_commit_sha": sha}
    )
    assert response.status_code == 201, response.text
    return Scenario(project, author, author_id, skills, response.headers["location"])


async def skills(engine: AsyncEngine, user: UUID, name: str = "user_competencies") -> list[dict[str, object]]:
    table = meta_data.tables[name]
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                select(table).where(table.c.user_id == user).order_by(table.c.competency_id, table.c.created_at)
            )
        ).mappings()
        return [row_mapping(row) for row in rows]


async def set_total(engine: AsyncEngine, user: UUID, total: Decimal) -> None:
    table = meta_data.tables["user_competencies"]
    async with engine.begin() as connection:
        await connection.execute(
            update(table)
            .where(table.c.user_id == user)
            .values(
                contribution_sum=total,
                displayed_value=min(total, Decimal(100)).quantize(Decimal("0.01")),
            )
        )


async def successful_candidate(
    client: AsyncClient,
    engine: AsyncEngine,
    *,
    baseline: Decimal | None = None,
    own_reviews: int = 3,
) -> Candidate:
    own = await submitted(client, engine)
    headers, user = await expert(client, engine, own.skills)
    case = Candidate(own, headers, user)
    if baseline is not None:
        # Deliberate corrupt/imported-number fixture, used only for negative
        # boundary coverage. Successful candidate flows start at zero.
        await set_total(engine, own.author_id, baseline)
    for index in range(own_reviews):
        if index:
            await new_submission(client, engine, case, f"own-{index}-", own=True)
        await publish_own(client, case, f"own-{index}")
        if not index:
            assert all(row["grade"] == SkillGrade.PERFORMER for row in await skills(engine, own.author_id))
    return case


async def publish_own(client: AsyncClient, case: Candidate, key: str) -> None:
    assigned = await claim(client, case.expert, key)
    assert assigned.status_code == 201, assigned.text
    await save(client, assigned.headers["location"], case.expert, final_draft())
    result = await complete(client, assigned.headers["location"], case.expert)
    assert result.status_code == 201, result.text


async def confirmed_review(client: AsyncClient, engine: AsyncEngine, case: Candidate, name: str) -> None:
    await new_submission(client, engine, case, name)
    _, pending = await pending_review(client, case.own.author, name)
    assigned = await claim(client, case.expert, name)
    assert assigned.status_code == 201, assigned.text
    result = await assess(client, pending.headers["location"], assigned, case.expert)
    assert result.status_code == 201, result.text


async def graduate(client: AsyncClient, engine: AsyncEngine, case: Candidate) -> None:
    await enable(client, case.own.author)
    # Pin author snapshots while provisional, then publish after graduation to
    # exercise advancement from fresh rights rather than the submission snapshot.
    for index in range(3, 5):
        await new_submission(client, engine, case, f"own-{index}-", own=True)
    for index in range(3):
        await confirmed_review(client, engine, case, f"minus-{index}-")
    current = await skills(engine, case.own.author_id, "skill_grants")
    assert len(current) == 4
    assert all(row["state"] == SkillGrantState.CONFIRMED and row["grade"] == SkillGrade.REVIEW_MINUS for row in current)
    # Three real solutions gave 35.4 XP. Crossing 50 through two more published
    # solutions must advance an already-confirmed minus grant in that same UoW.
    for index in range(3, 5):
        await publish_own(client, case, f"own-{index}")
    current = await skills(engine, case.own.author_id, "skill_grants")
    assert len(current) == 8
    assert all(row["state"] == SkillGrantState.REVOKED for row in current if row["grade"] == SkillGrade.REVIEW_MINUS)
    assert all(row["state"] == SkillGrantState.PROVISIONAL for row in current if row["grade"] == SkillGrade.REVIEW_PLUS)
    for index in range(5, 7):
        await new_submission(client, engine, case, f"own-{index}-", own=True)
    for index in range(3):
        await confirmed_review(client, engine, case, f"plus-{index}-")
    current = await skills(engine, case.own.author_id, "skill_grants")
    assert all(row["state"] == SkillGrantState.CONFIRMED for row in current if row["grade"] == SkillGrade.REVIEW_PLUS)
    assert all(row["contribution_sum"] == Decimal(59) for row in await skills(engine, case.own.author_id))
    for index in range(5, 7):
        await publish_own(client, case, f"own-{index}")
    assert all(row["contribution_sum"] == Decimal("82.6") for row in await skills(engine, case.own.author_id))
    current = await skills(engine, case.own.author_id, "skill_grants")
    assert not any(row["grade"] == SkillGrade.EXPERT for row in current)


@pytest.fixture
async def ready_candidate(learning_client: AsyncClient, auth_engine: AsyncEngine) -> Candidate:
    case = await successful_candidate(learning_client, auth_engine)
    await graduate(learning_client, auth_engine, case)
    return case


async def apply(client: AsyncClient, engine: AsyncEngine, case: Candidate, key: str = "apply") -> Response:
    rows = await skills(engine, case.own.author_id)
    return await client.post(
        BASE,
        headers={**case.own.author, "Idempotency-Key": key},
        json={
            "competency_id": str(rows[0]["competency_id"]),
            "requested_grade": "expert",
            "expected_user_competency_version": rows[0]["version"],
        },
    )


def decision_body(application: Response, *, decision: str = "approved") -> dict[str, object]:
    return {
        "expected_application_status": "pending",
        "expected_application_version": application.json()["version"],
        "expected_user_competency_version": application.json()["user_competency_version"],
        "decision": decision,
        "feedback": "Private readiness evidence: strengths, remaining practice and concrete next steps.",
    }


async def facts(engine: AsyncEngine) -> dict[str, list[dict[str, object]]]:
    result = await quality_effects(engine)
    async with engine.connect() as connection:
        for name in (
            "reviewer_applications",
            "reviewer_application_assessments",
            "request_generations",
            "request_scopes",
        ):
            table = meta_data.tables[name]
            rows = (await connection.execute(select(table).order_by(*table.primary_key.columns))).mappings()
            result[name] = [row_mapping(row) for row in rows]
    return result


async def author_snapshots(engine: AsyncEngine, submission: Scenario) -> list[dict[str, object]]:
    table = meta_data.tables["submission_competencies"]
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                select(table)
                .where(table.c.submission_id == UUID(submission.submission.rsplit("/", 1)[1]))
                .order_by(table.c.competency_id)
            )
        ).mappings()
        return [row_mapping(row) for row in rows]


async def assert_new_author_submission(
    client: AsyncClient, engine: AsyncEngine, case: Candidate, name: str
) -> tuple[Scenario, list[dict[str, object]]]:
    before_skills = await skills(engine, case.own.author_id)
    before_grants = await skills(engine, case.own.author_id, "skill_grants")
    live_grants = {row["competency_id"]: row for row in before_grants if row["state"] == SkillGrantState.CONFIRMED}
    wallet = await client.get("/api/v1/me/wallet", headers=case.own.author)
    assert wallet.status_code == 200, wallet.text
    # Creates a fresh case/project and asserts the actual submission POST is 201.
    submission = await new_submission(client, engine, case, name, own=True)
    snapshots = await author_snapshots(engine, submission)
    assert len(snapshots) == len(before_skills) == len(live_grants) == 4
    for row, skill in zip(snapshots, before_skills, strict=True):
        grant = live_grants[skill["competency_id"]]
        expected = {
            "author_id": case.own.author_id,
            "competency_id": skill["competency_id"],
            "author_grade": skill["grade"],
            "author_user_competency_version": skill["version"],
            "author_grant_id": grant["id"],
            "author_grant_version": grant["version"],
            "author_grant_grade": grant["grade"],
            "author_grant_state": SkillGrantState.CONFIRMED,
            "author_grant_evidence_kind": grant["evidence_kind"],
            "author_grant_confirmed_at": grant["confirmed_at"],
            "author_confirmed_by_user_id": grant["confirmed_by_user_id"],
            "author_confirmed_by_grant_id": grant["confirmed_by_grant_id"],
            "author_confirmed_by_grant_version": grant["confirmed_by_grant_version"],
            "author_readiness_assessment_id": grant["readiness_assessment_id"],
            "author_seed_reference": None,
        }
        assert {field: row[field] for field in expected} == expected
    assert await skills(engine, case.own.author_id) == before_skills
    assert await skills(engine, case.own.author_id, "skill_grants") == before_grants
    before_reads = await facts(engine)
    for path in (submission.submission, submission.submission + "/snapshot", "/api/v1/me/wallet/transactions"):
        read = await client.get(path, headers=case.own.author)
        assert read.status_code == 200, read.text
        assert read.headers["cache-control"] == "no-store"
    assert (await client.get("/api/v1/me/wallet", headers=case.own.author)).json() == wallet.json()
    assert await facts(engine) == before_reads
    assert await author_snapshots(engine, submission) == snapshots
    return submission, snapshots


async def test_exact_uncapped_threshold_and_numeric_values_never_replace_durable_gates(
    learning_client: AsyncClient,
    auth_engine: AsyncEngine,
) -> None:
    client, engine = learning_client, auth_engine
    case = await successful_candidate(
        client, engine, baseline=Decimal("1.399999999999999999999999999999999999"), own_reviews=2
    )
    rows = await skills(engine, case.own.author_id)
    assert all(row["displayed_value"] == 25 and row["grade"] == SkillGrade.PERFORMER for row in rows)
    assert await skills(engine, case.own.author_id, "skill_grants") == []
    async with AsyncSession(engine) as session:
        await ReviewAccess(session).actor(UserId(case.own.author_id), lock=True)
        with localcontext() as context:
            context.prec = 2
            for row in rows:
                await SqlGradeAdvancement(session).advance(
                    case.own.author_id, UUID(str(row["competency_id"])), datetime.now(UTC)
                )
        await session.commit()
    assert await skills(engine, case.own.author_id, "skill_grants") == []
    for total in (Decimal(25), Decimal(50), Decimal(75), Decimal(100)):
        await set_total(engine, case.own.author_id, total)
        async with AsyncSession(engine) as session:
            await ReviewAccess(session).actor(UserId(case.own.author_id), lock=True)
            for row in rows:
                await SqlGradeAdvancement(session).advance(
                    case.own.author_id, UUID(str(row["competency_id"])), datetime.now(UTC)
                )
            await session.commit()
        assert all(row["grade"] == SkillGrade.REVIEW_MINUS for row in await skills(engine, case.own.author_id))
        denied = await apply(client, engine, case, str(total))
        assert denied.status_code == 409, denied.text
        assert denied.json()["code"] == "GRADE_REQUIREMENTS_NOT_MET"


async def test_three_per_grade_independent_readiness_private_reads_and_permanent_replays(
    learning_client: AsyncClient,
    auth_engine: AsyncEngine,
    ready_candidate: Candidate,
) -> None:
    client, engine, case = learning_client, auth_engine, ready_candidate
    plus_submission, plus_snapshots = await assert_new_author_submission(client, engine, case, "graduated-plus-")
    assert all(row["author_grant_grade"] == SkillGrade.REVIEW_PLUS for row in plus_snapshots)
    assert all(row["author_readiness_assessment_id"] is None for row in plus_snapshots)
    before = await facts(engine)
    assert (await client.get("/api/v1/me/reviewer-applications", headers=case.own.author)).json()["items"] == []
    assert (
        await client.get("/api/v1/me/reviewer-applications?offset=99999999999999999999999", headers=case.own.author)
    ).json()["items"] == []
    assert await facts(engine) == before
    await set_total(engine, case.own.author_id, Decimal("74.999999999999999999999999999999999"))
    denied = await apply(client, engine, case, "below-75")
    assert denied.status_code == 409, denied.text
    assert denied.json()["code"] == "GRADE_REQUIREMENTS_NOT_MET"
    await set_total(engine, case.own.author_id, Decimal("82.6"))
    created = await apply(client, engine, case)
    assert created.status_code == 201, created.text
    url = created.headers["location"]
    assert "evidence" not in created.json()
    assert len(created.content) < 65_536
    assert created.headers["cache-control"] == "no-store"
    assert (await apply(client, engine, case)).json() == created.json()
    await expire_request(engine, case.own.author_id, BASE, "apply")
    for key in ("apply", "other-key"):
        replay = await apply(client, engine, case, key)
        assert replay.status_code == 200, replay.text
        assert replay.json() == created.json()
    stranger = await participant(client, engine, "stranger@example.com")
    assert (await client.get(url, headers=stranger)).status_code == 404
    assert (
        await client.post(
            url + "/assessments", headers={**case.own.author, "Idempotency-Key": "self"}, json=decision_body(created)
        )
    ).status_code == 404
    read = await client.get(url, headers=case.expert)
    assert read.status_code == 200, read.text
    assert len(read.json()["evidence"]["passed_probation_ids"]) == 2
    assert len(read.json()["evidence"]["good_review_ids"]) == 6
    before = await facts(engine)
    assert (await client.get(url, headers=case.own.author)).status_code == 200
    assert await facts(engine) == before
    body = decision_body(created)
    assessed = await client.post(url + "/assessments", headers={**case.expert, "Idempotency-Key": "assess"}, json=body)
    assert assessed.status_code == 201, assessed.text
    assert "feedback" not in assessed.json()
    assert len(assessed.content) < 65_536
    assert (await client.get(assessed.headers["location"], headers=case.own.author)).json()["feedback"] == body[
        "feedback"
    ]
    assert (await client.get(assessed.headers["location"], headers=stranger)).status_code == 404
    grants = await skills(engine, case.own.author_id, "skill_grants")
    expert_grants = [row for row in grants if row["grade"] == SkillGrade.EXPERT]
    assert len(expert_grants) == 1
    assert expert_grants[0]["readiness_assessment_id"] == UUID(assessed.json()["id"])
    assert sum(row["state"] == SkillGrantState.CONFIRMED for row in grants) == 4
    _, expert_snapshots = await assert_new_author_submission(client, engine, case, "approved-expert-")
    expert_snapshot = next(row for row in expert_snapshots if row["author_grant_grade"] == SkillGrade.EXPERT)
    assert expert_snapshot["author_readiness_assessment_id"] == UUID(assessed.json()["id"])
    assert await author_snapshots(engine, plus_submission) == plus_snapshots
    await expire_request(engine, case.expert_id, url + "/assessments", "assess")
    for key in ("assess", "new-assessment-key"):
        replay = await client.post(url + "/assessments", headers={**case.expert, "Idempotency-Key": key}, json=body)
        assert replay.status_code == 200, replay.text
        assert replay.json() == assessed.json()
    assert await skills(engine, case.own.author_id, "skill_grants") == grants
    await revoke(engine, case.expert_id)
    before = await facts(engine)
    assert (await client.get(url, headers=case.expert)).status_code == 404
    assert (
        await client.post(url + "/assessments", headers={**case.expert, "Idempotency-Key": "assess"}, json=body)
    ).status_code == 404
    assert await facts(engine) == before


async def test_readiness_cas_rejection_new_application_atomic_rollback_and_concurrent_approval(
    learning_client: AsyncClient,
    auth_engine: AsyncEngine,
    ready_candidate: Candidate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, case = learning_client, auth_engine, ready_candidate
    created = await apply(client, engine, case)
    assert created.status_code == 201, created.text
    url = created.headers["location"] + "/assessments"
    body = decision_body(created, decision="needs_development")
    for changed in (
        {"expected_application_version": 2},
        {"expected_user_competency_version": 1},
    ):
        stale = await client.post(url, headers={**case.expert, "Idempotency-Key": "stale"}, json={**body, **changed})
        assert stale.status_code == 409, stale.text
        assert stale.json()["code"] == "STALE_VERSION"
    for invalid_change in (
        {"expected_application_status": "approved"},
        {"expected_application_version": True},
        {"feedback": "   "},
        {"evidence": {"good_reviews": 100}},
    ):
        invalid = await client.post(
            url, headers={**case.expert, "Idempotency-Key": "invalid"}, json={**body, **invalid_change}
        )
        assert invalid.status_code == 422, invalid.text
    rejected = await client.post(url, headers={**case.expert, "Idempotency-Key": "reject"}, json=body)
    assert rejected.status_code == 201, rejected.text
    assert not any(
        row["grade"] == SkillGrade.EXPERT for row in await skills(engine, case.own.author_id, "skill_grants")
    )
    created = await apply(client, engine, case, "reapply")
    assert created.status_code == 201, created.text
    url = created.headers["location"] + "/assessments"
    body = decision_body(created)
    before = await facts(engine)

    async def fail_capture(self: DevelopmentRequests, response: CachedResponse) -> None:
        grants = meta_data.tables["skill_grants"]
        assert (
            await self.session.scalar(
                select(grants.c.id).where(
                    grants.c.user_id == case.own.author_id,
                    grants.c.grade == SkillGrade.EXPERT,
                    grants.c.readiness_assessment_id == UUID(json.loads(response.body)["id"]),
                )
            )
            is not None
        )
        raise SQLAlchemyError("Injected post-grant capture failure")

    with monkeypatch.context() as patch:
        patch.setattr(DevelopmentRequests, "complete", fail_capture)
        failed = await client.post(url, headers={**case.expert, "Idempotency-Key": "approve"}, json=body)
    assert failed.status_code == 503, failed.text
    assert await facts(engine) == before
    other_expert, _ = await expert(client, engine, case.own.skills, "other-expert@example.com")
    results = await asyncio.wait_for(
        asyncio.gather(
            *(
                client.post(url, headers={**headers, "Idempotency-Key": key}, json=body)
                for headers, key in ((case.expert, "approve"), (other_expert, "racing-approve"))
            )
        ),
        timeout=10,
    )
    assert sorted(result.status_code for result in results) == [201, 409], [result.text for result in results]
    history = (await client.get("/api/v1/me/reviewer-applications", headers=case.own.author)).json()["items"]
    assert [row["status"] for row in history] == ["approved", "needs_development"]
    assert len((await facts(engine))["reviewer_application_assessments"]) == 2
    # A different skill remains independently eligible. Once its live grant is
    # revoked, neither cached nor expired-key/pending-business replay is allowed.
    other = (await skills(engine, case.own.author_id))[1]
    application_body = {
        "competency_id": str(other["competency_id"]),
        "requested_grade": "expert",
        "expected_user_competency_version": other["version"],
    }
    pending = await client.post(
        BASE, headers={**case.own.author, "Idempotency-Key": "other-skill"}, json=application_body
    )
    assert pending.status_code == 201, pending.text
    await revoke(engine, case.own.author_id)
    before = await facts(engine)
    denied = await client.post(
        BASE, headers={**case.own.author, "Idempotency-Key": "other-skill"}, json=application_body
    )
    assert denied.status_code == 409, denied.text
    assert denied.json()["code"] == "GRADE_REQUIREMENTS_NOT_MET"
    assert await facts(engine) == before
    await expire_request(engine, case.own.author_id, BASE, "other-skill")
    before = await facts(engine)
    for key in ("other-skill", "revoked-other-key"):
        denied = await client.post(BASE, headers={**case.own.author, "Idempotency-Key": key}, json=application_body)
        assert denied.status_code == 409, denied.text
    denied = await client.post(
        pending.headers["location"] + "/assessments",
        headers={**case.expert, "Idempotency-Key": "revoked-candidate"},
        json=decision_body(pending),
    )
    assert denied.status_code == 409, denied.text
    assert await facts(engine) == before


async def test_readiness_discovery_eligibility_and_published_portfolio_are_private_read_only(
    learning_client: AsyncClient,
    auth_engine: AsyncEngine,
    ready_candidate: Candidate,
) -> None:
    client, engine, case = learning_client, auth_engine, ready_candidate
    initial = (await client.get("/api/v1/me/profile", headers=case.own.author)).json()
    assert all(
        skill["expert_application"] == {"can_apply": True, "pending_application_id": None}
        for skill in initial["competencies"]
    )
    created = []
    for index, skill in enumerate(initial["competencies"][:2]):
        response = await client.post(
            BASE,
            headers={**case.own.author, "Idempotency-Key": f"portfolio-{index}"},
            json={
                "competency_id": skill["competency_id"],
                "requested_grade": "expert",
                "expected_user_competency_version": skill["version"],
            },
        )
        assert response.status_code == 201, response.text
        created.append(response)
    stranger = await participant(client, engine, "portfolio-stranger@example.com")
    unrelated, _ = await expert(
        client, engine, [UUID(initial["competencies"][2]["competency_id"])], "portfolio-other-expert@example.com"
    )
    independent, _ = await expert(
        client, engine, [UUID(created[0].json()["competency_id"])], "portfolio-independent@example.com"
    )
    before = await facts(engine)
    assert (await client.get(BASE)).status_code == 401
    assert (await client.get(BASE, headers=stranger)).status_code == 403
    assert (await client.get(BASE, headers=unrelated)).json()["items"] == []
    page = await client.get(BASE + "?limit=1", headers=case.expert)
    assert page.status_code == 200, page.text
    assert page.headers["cache-control"] == "no-store"
    assert page.json()["items"][0]["id"] == created[0].json()["id"]
    assert page.json()["next_offset"] == 1
    second = await client.get(BASE + "?limit=1&offset=1", headers=case.expert)
    assert second.json()["items"][0]["id"] == created[1].json()["id"]
    assert second.json()["next_offset"] is None
    assert (await client.get(BASE + "?offset=9999999999999999999999", headers=case.expert)).json()["items"] == []
    assert (await client.get(BASE + "?limit=101", headers=case.expert)).status_code == 422
    profile = (await client.get("/api/v1/me/profile", headers=case.own.author)).json()
    for item in created:
        skill = next(
            skill for skill in profile["competencies"] if skill["competency_id"] == item.json()["competency_id"]
        )
        assert skill["expert_application"] == {"can_apply": False, "pending_application_id": item.json()["id"]}
    url = created[0].headers["location"]
    detail = await client.get(url, headers=case.expert)
    assert detail.status_code == 200, detail.text
    assert detail.json()["can_assess"] is True
    assert (await client.get(url, headers=case.own.author)).json()["can_assess"] is False
    summary = detail.json()["evidence_summary"]
    assert len(summary["successful_cases"]) == len(summary["probations"]) == 2
    assert len(summary["reviews"]) == 6
    assert all(period["confirmed_reviews"] == 3 for period in summary["probations"])
    for item in summary["reviews"]:
        work = await client.get(item["content_url"], headers=case.expert)
        assert work.status_code == 200, work.text
        assert work.json()["revision_id"] == item["revision_id"]
        assert set(work.json()["quality"]) == {"score", "confirmed_at"}
        assert set(work.json()["feedback"]) == {"strengths", "problems", "next_steps"}
        assert work.json()["case"]["description"]
        assert "draft" not in work.text
        assert "repository" not in work.text
        assert "Private expert feedback" not in work.text
        assert (await client.get(item["content_url"], headers=independent)).status_code == 200
        assert (await client.get(f"/api/v1/reviews/{item['review_id']}", headers=independent)).status_code == 404
        assert (await client.get(item["content_url"], headers=case.own.author)).status_code == 200
        assert (await client.get(item["content_url"], headers=stranger)).status_code == 404
        assert (await client.get(item["content_url"], headers=unrelated)).status_code == 404
    assert (await client.get(url + f"/evidence/reviews/{uuid4()}", headers=case.expert)).status_code == 404
    mode = (await client.get("/api/v1/me/review-mode", headers=case.expert)).json()
    assert mode["can_assess_readiness"] is True
    assert await facts(engine) == before
    await set_total(engine, case.expert_id, Decimal("74.9999999999999999999999"))
    assert (await client.get("/api/v1/me/review-mode", headers=case.expert)).json()["can_assess_readiness"] is False
    assert (await client.get(BASE, headers=case.expert)).status_code == 403
    assert (await client.get(summary["reviews"][0]["content_url"], headers=case.expert)).status_code == 404


async def test_old_minus_correction_after_plus_promotion_keeps_original_probation_binding(
    learning_client: AsyncClient,
    auth_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine = learning_client, auth_engine
    # Here the XP gate is satisfied before probation: the third confirmation
    # must confirm minus and enter provisional plus atomically before wallets.
    case = await successful_candidate(client, engine, own_reviews=5)
    await enable(client, case.own.author)
    await new_submission(client, engine, case, "correction-")
    original, pending = await pending_review(client, case.own.author, "old")
    assigned = await claim(client, case.expert, "old-quality")
    rejected = await assess(
        client, pending.headers["location"], assigned, case.expert, decision="needs_revision", score=60
    )
    assert rejected.status_code == 201, rejected.text
    for index in range(2):
        await confirmed_review(client, engine, case, f"minus-{index}-")
    await new_submission(client, engine, case, "minus-third-")
    _, third = await pending_review(client, case.own.author, "third")
    third_quality = await claim(client, case.expert, "third")
    before = await facts(engine)
    with monkeypatch.context() as patch:
        patch.setattr(WalletCollaborator, "reward_review", AsyncMock(side_effect=SQLAlchemyError("wallet failure")))
        failed = await assess(client, third.headers["location"], third_quality, case.expert)
    assert failed.status_code == 503, failed.text
    assert await facts(engine) == before
    confirmed = await assess(client, third.headers["location"], third_quality, case.expert)
    assert confirmed.status_code == 201, confirmed.text
    assert all(row["grade"] == SkillGrade.REVIEW_PLUS for row in await skills(engine, case.own.author_id))
    before = await facts(engine)
    await save(client, original.headers["location"], case.own.author, final_draft(version=2, marker="Corrected"))
    revised = await client.post(
        pending.headers["location"] + "/revisions",
        headers={**case.own.author, "Idempotency-Key": "revise"},
        json={"expected_revision_id": pending.json()["revision_id"], "expected_draft_version": 3},
    )
    assert revised.status_code == 201, revised.text
    assigned = await claim(client, case.expert, "correction-quality")
    confirmed = await assess(client, pending.headers["location"], assigned, case.expert, key="corrected")
    assert confirmed.status_code == 201, confirmed.text
    after = await facts(engine)
    assert before["probations"] == after["probations"]
    assert before["probation_successes"] == after["probation_successes"]
    assert (await client.get(pending.headers["location"], headers=case.own.author)).json()["status"] == "published"


async def test_active_confirmed_minus_assignment_survives_verified_plus_promotion_only(
    learning_client: AsyncClient, auth_engine: AsyncEngine
) -> None:
    client, engine = learning_client, auth_engine
    case = await successful_candidate(client, engine)
    await enable(client, case.own.author)
    for index in range(3):
        await confirmed_review(client, engine, case, f"graduate-minus-{index}-")
    old_grants = await skills(engine, case.own.author_id, "skill_grants")
    assert all(
        row["grade"] == SkillGrade.REVIEW_MINUS and row["state"] == SkillGrantState.CONFIRMED for row in old_grants
    )
    subject = await new_submission(client, engine, case, "carry-over-")
    assigned = await claim(client, case.own.author, "carry-over")
    assert assigned.status_code == 201, assigned.text
    assert assigned.json()["supervised_competency_ids"] == []
    root = assigned.headers["location"]
    await save(client, root, case.own.author, final_draft())
    original = [
        row
        for row in (await facts(engine))["assignment_competencies"]
        if row["assignment_id"] == UUID(assigned.json()["id"])
    ]
    assert len(original) == 4
    assert all(row["value"] == Decimal("35.4") for row in original)
    for index in range(3, 5):
        await new_submission(client, engine, case, f"own-{index}-", own=True)
        await publish_own(client, case, f"own-{index}")
    current = await skills(engine, case.own.author_id, "skill_grants")
    plus = [row for row in current if row["grade"] == SkillGrade.REVIEW_PLUS]
    assert len(plus) == 4
    assert all(row["state"] == SkillGrantState.PROVISIONAL for row in plus)
    assert all(row["state"] == SkillGrantState.REVOKED for row in current if row["grade"] == SkillGrade.REVIEW_MINUS)
    grants = meta_data.tables["skill_grants"]
    live_ids = [row["id"] for row in plus]
    async with engine.begin() as connection:
        await connection.execute(
            update(grants)
            .where(grants.c.id.in_(live_ids))
            .values(state=SkillGrantState.REVOKED, revoked_at=datetime.now(UTC), version=grants.c.version + 1)
        )
    before = await facts(engine)
    denied = await complete(client, root, case.own.author)
    assert denied.status_code == 403, denied.text
    assert await facts(engine) == before
    # Restore only the test-injected revocation, retaining the actual promotion's
    # immutable provenance. Revoked old minus grants are never restored.
    async with engine.begin() as connection:
        await connection.execute(
            update(grants)
            .where(grants.c.id.in_(live_ids))
            .values(state=SkillGrantState.PROVISIONAL, revoked_at=None, version=grants.c.version + 1)
        )
        altered = json.loads(str(plus[0]["evidence"]))
        altered["previous_grant_id"] = str(case.expert_id)
        await connection.execute(
            update(grants).where(grants.c.id == plus[0]["id"]).values(evidence=json.dumps(altered))
        )
    before = await facts(engine)
    denied = await complete(client, root, case.own.author)
    assert denied.status_code == 409, denied.text
    assert denied.json()["code"] == "SUPERVISION_REQUIRED"
    assert await facts(engine) == before
    async with engine.begin() as connection:
        await connection.execute(
            update(grants).where(grants.c.id == plus[0]["id"]).values(evidence=plus[0]["evidence"])
        )
    before = await facts(engine)
    wallet_before = (await client.get("/api/v1/me/wallet", headers=case.own.author)).json()
    published = await complete(client, root, case.own.author)
    assert published.status_code == 201, published.text
    assert published.json()["status"] == "published"
    assert (await client.get(published.headers["location"], headers=subject.author)).status_code == 200
    after = await facts(engine)
    assert after["probations"] == before["probations"]
    assert after["probation_successes"] == before["probation_successes"]
    assert after["quality_assessments"] == before["quality_assessments"]
    snapshots = [
        row for row in after["reviewer_snapshots"] if row["revision_id"] == UUID(published.json()["revision_id"])
    ]
    fields = ("value", "grade", "grant_id", "grant_version", "user_competency_version", "grant_state")
    assert {row["competency_id"]: tuple(row[field] for field in fields) for row in snapshots} == {
        row["competency_id"]: tuple(row[field] for field in fields) for row in original
    }
    contributions = [
        row for row in after["progression_contributions"] if row["review_id"] == UUID(published.json()["id"])
    ]
    assert len(contributions) == 4
    assert all(
        row["expertise_value"] == Decimal("35.4") and row["contribution"] == Decimal("10.708") for row in contributions
    )
    wallet_after = (await client.get("/api/v1/me/wallet", headers=case.own.author)).json()
    assert wallet_after["settled"] == wallet_before["settled"] + 5
    await expire_request(engine, case.own.author_id, root + "/reviews", "complete")
    for key in ("complete", "new-key"):
        replay = await complete(client, root, case.own.author, key=key)
        assert replay.status_code == 200, replay.text
        assert replay.json() == published.json()
    assert (await facts(engine))["wallet_ledger"] == after["wallet_ledger"]
    await new_submission(client, engine, case, "new-plus-")
    next_task, pending = await pending_review(client, case.own.author, "new-plus")
    assert set(next_task.json()["supervised_competency_ids"]) == set(map(str, case.own.skills))
    assert all(row["grade"] == SkillGrade.REVIEW_PLUS for row in next_task.json()["reviewer_skills"])
    assert pending.json()["status"] == "pending_quality_check"
    assert (await client.get("/api/v1/me/wallet", headers=case.own.author)).json() == wallet_after
    assert (await facts(engine))["probation_successes"] == after["probation_successes"]
