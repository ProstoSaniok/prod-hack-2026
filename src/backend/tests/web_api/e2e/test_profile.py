import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import event, insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from src.adapters.data_gateways.learning_views import row_mapping
from src.adapters.data_gateways.tables import meta_data
from src.application.development.entity import ReadinessDecision, ReviewerApplicationState, SkillGrade, SkillGrantState
from src.application.repository_storage.git import GitStorage
from src.application.user.capability import ProductCapability
from tests.support.cases import account
from tests.support.learning import MemoryGit, learning_client, participant
from tests.support.review_quality import (
    QualityScenario,
    assess,
    quality_effects,
    quality_scenario,
    revoke,
)
from tests.support.reviews import claim, seed_rights

__all__ = ["learning_client", "quality_scenario"]
PROFILE = "/api/v1/me/profile"
HISTORY = "/api/v1/me/history"
POLICY = "/api/v1/development/policy"


@pytest.fixture
def learning_storage() -> GitStorage:
    return MemoryGit()


async def facts(engine: AsyncEngine) -> dict[str, list[dict[str, object]]]:
    result = await quality_effects(engine)
    async with engine.connect() as connection:
        for name in (
            "user_profiles",
            "user_capabilities",
            "request_generations",
            "request_scopes",
            "operations",
            "reviewer_applications",
            "reviewer_application_assessments",
        ):
            table = meta_data.tables[name]
            rows = (await connection.execute(select(table).order_by(*table.primary_key.columns))).mappings()
            result[name] = [row_mapping(row) for row in rows]
    return result


async def test_empty_profile_is_private_authenticated_and_read_only(
    learning_client: AsyncClient, auth_engine: AsyncEngine
) -> None:
    client, engine = learning_client, auth_engine
    headers = await participant(client, engine, "private-profile@example.com")
    other = await participant(client, engine, "other-profile@example.com")
    _, fresh = await account(client, engine, "fresh-profile@example.com", organizer=False)
    owner = (await client.get("/auth/me", headers=headers)).json()["id"]
    before = await facts(engine)
    fresh_profile = await client.get(PROFILE, headers=fresh)
    assert fresh_profile.status_code == 200
    assert fresh_profile.json()["capabilities"] == ["participant"]
    assert fresh_profile.json()["prodits"] == 10
    for path in (PROFILE, HISTORY, POLICY):
        denied = await client.get(path)
        assert denied.status_code == 401, denied.text
        assert denied.headers["www-authenticate"] == "Bearer"
        assert denied.headers["cache-control"] == "no-store"
    response = await client.get(PROFILE, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == owner
    assert (body["role"], body["capabilities"], body["display_name"]) == ("user", ["participant"], "Участник")
    assert body["grade"] == "performer"
    assert body["competencies"] == body["probation"] == []
    assert body["wants_to_review"] is False
    assert body["wallet"] == {
        "settled": 10,
        "reserved": 0,
        "available": 10,
        "unit": "PRODиты",
        "transactions_url": "/api/v1/me/wallet/transactions",
    }
    assert (body["karma"], body["prodits"]) == (0, 10)
    assert "@" not in response.text
    assert "password" not in response.text
    assert "access_token" not in response.text
    foreign = (await client.get(PROFILE, headers=other)).json()
    assert foreign["id"] != owner
    assert (await client.get(PROFILE + "/" + owner, headers=other)).status_code == 404
    history = (await client.get(HISTORY, headers=headers)).json()
    assert len(history) == 1
    assert history[0]["type"] == "wallet_transaction"
    assert (await client.get(HISTORY, headers=headers, params={"offset": 2**80})).json() == []
    assert (await client.get(HISTORY, headers=headers, params={"limit": 0})).status_code == 422
    assert (await client.get(HISTORY, headers=headers, params={"limit": 101})).status_code == 422
    assert (await client.get(HISTORY, headers=headers, params={"offset": -1})).status_code == 422
    policy = await client.get(POLICY, headers=headers)
    assert policy.status_code == 200, policy.text
    assert policy.json()["version"] == "prodguild-development-v1"
    assert policy.json()["progression_version"] == "p02-v1"
    assert policy.json()["formula"] == "score * weight * (1 + 0.2 * E / 100)"
    assert policy.json()["probation_required_reviews"] == 3
    assert policy.json()["priority_cost"] == 5
    assert [gate["minimum_value"] for gate in policy.json()["grade_gates"]] == [0, 25, 50, 75]
    assert await facts(engine) == before
    capabilities = meta_data.tables["user_capabilities"]
    async with engine.begin() as connection:
        await connection.execute(
            update(capabilities).where(capabilities.c.user_id == UUID(owner)).values(revoked_at=datetime.now(UTC))
        )
    before = await facts(engine)
    revoked_profile = await client.get(PROFILE, headers=headers)
    assert revoked_profile.status_code == 200
    assert revoked_profile.json()["capabilities"] == []
    assert (await client.get(HISTORY, headers=headers)).status_code == 200
    assert (await client.get(POLICY, headers=headers)).status_code == 200
    assert await facts(engine) == before
    # Trusted legacy fixture: valid persisted user/token/capability, no wallet.
    # Retarget this test's token rather than signing in (which initializes credit).
    legacy = uuid4()
    async with engine.begin() as connection:
        await connection.execute(insert(meta_data.tables["users"]).values(id=legacy, created_at=datetime.now(UTC)))
        await connection.execute(
            insert(capabilities).values(
                id=uuid4(),
                created_at=datetime.now(UTC),
                user_id=legacy,
                capability=ProductCapability.PARTICIPANT,
                version=1,
            )
        )
        tokens = meta_data.tables["access_token"]
        await connection.execute(update(tokens).where(tokens.c.user_id == UUID(owner)).values(user_id=legacy))
    before = await facts(engine)
    assert (await client.get(PROFILE, headers=headers)).status_code == 404
    assert (await client.get(HISTORY, headers=headers)).json() == []
    assert (await client.get("/api/v1/me/wallet", headers=headers)).status_code == 404
    assert await facts(engine) == before


async def test_pending_quality_is_not_author_history_and_publication_exposes_real_effects(
    learning_client: AsyncClient, auth_engine: AsyncEngine, quality_scenario: QualityScenario
) -> None:
    client, engine, case = learning_client, auth_engine, quality_scenario
    before = await facts(engine)
    author_history = await client.get(HISTORY, headers=case.submission.author)
    assert author_history.status_code == 200, author_history.text
    assert {item["type"] for item in author_history.json()} == {
        "wallet_transaction",
        "case_started",
        "solution_submitted",
    }
    assert case.created.json()["id"] not in author_history.text
    own_history = (await client.get(HISTORY, headers=case.reviewer)).json()
    assert sum(item["type"] == "review_completed" for item in own_history) == 1
    response = await client.get(PROFILE, headers=case.reviewer)
    assert response.status_code == 200, response.text
    own = response.json(parse_float=Decimal)
    assert own["grade"] == "review_minus"
    assert len(own["competencies"]) == len(own["probation"]) == 4
    assert all(skill["grant"] == "supervised" and skill["value"] == 40 for skill in own["competencies"])
    assert all(skill["evidence"]["grant_version"] == 1 for skill in own["competencies"])
    assert all(item["remaining"] == 3 and not item["passed"] for item in own["probation"])
    assert all(
        skill["next_grade"]
        == {
            "grade": "review_plus",
            "xp_remaining": 10,
            "successful_cases": 0,
            "previous_probation_passed": False,
        }
        for skill in own["competencies"]
    )
    assert "PRIVATE EVIDENCE" not in response.text
    assert "PRIVATE SEED" not in response.text
    assert await facts(engine) == before
    assigned = await claim(client, case.expert)
    confirmed = await assess(client, case.created.headers["location"], assigned, case.expert)
    assert confirmed.status_code == 201, confirmed.text
    before = await facts(engine)
    author = await client.get(PROFILE, headers=case.submission.author)
    assert author.status_code == 200, author.text
    author_body = author.json(parse_float=Decimal)
    assert all(skill["contribution_sum"] == Decimal("11.8") for skill in author_body["competencies"])
    assert all(
        skill["next_grade"]
        == {
            "grade": "review_minus",
            "xp_remaining": Decimal("13.2"),
            "successful_cases": 1,
            "previous_probation_passed": True,
        }
        for skill in author_body["competencies"]
    )
    assert all(skill["grant"] == "none" and not skill["review_allowed"] for skill in author_body["competencies"])
    assert (author_body["prodits"], author_body["wallet"]["settled"], author_body["karma"]) == (13, 13, 0)
    reviewer = (await client.get(PROFILE, headers=case.reviewer)).json()
    assert (reviewer["prodits"], reviewer["karma"]) == (15, 1)
    assert all(item["confirmed_reviews"] == 1 and item["remaining"] == 2 for item in reviewer["probation"])
    author_events = await client.get(HISTORY, headers=case.submission.author)
    assert sum(item["type"] == "feedback_published" for item in author_events.json()) == 1
    assert "quality_assessed" not in author_events.text
    assert "Private expert feedback" not in author_events.text
    expert_events = (await client.get(HISTORY, headers=case.expert)).json()
    assert sum(item["type"] == "quality_assessed" for item in expert_events) == 1
    assert "Private expert feedback" not in str(expert_events)
    all_events = (await client.get(HISTORY, headers=case.reviewer, params={"limit": 100})).json()
    assert len({item["id"] for item in all_events}) == len(all_events)
    ledger = (await client.get("/api/v1/me/wallet/transactions", headers=case.reviewer)).json()
    assert {item["resource_id"] for item in all_events if item["type"] == "wallet_transaction"} == {
        item["id"] for item in ledger
    }
    # Shared publication timestamps exercise the event-type/source-ID tie-break.
    keys = [(item["created_at"], item["type"], item["id"]) for item in all_events]
    assert keys == sorted(keys, key=lambda item: (-datetime.fromisoformat(item[0]).timestamp(), item[1], item[2]))
    paged = []
    for offset in range(0, len(all_events) + 1, 2):
        page = await client.get(HISTORY, headers=case.reviewer, params={"limit": 2, "offset": offset})
        assert page.status_code == 200, page.text
        paged.extend(page.json())
    assert paged == all_events
    assert (await client.get(HISTORY, headers=case.reviewer, params={"offset": 100})).json() == []
    assert await facts(engine) == before


async def test_profile_reuses_per_skill_authority_and_owner_fence_without_locking_other_users(
    learning_client: AsyncClient, auth_engine: AsyncEngine
) -> None:
    client, engine = learning_client, auth_engine
    headers = await participant(client, engine, "mixed-profile@example.com")
    other = await participant(client, engine, "unrelated@example.com")
    owner = UUID((await client.get("/auth/me", headers=headers)).json()["id"])
    ids = [uuid4(), uuid4(), uuid4()]
    async with engine.begin() as connection:
        await connection.execute(
            insert(meta_data.tables["competencies"]),
            [
                {"id": skill, "created_at": datetime.now(UTC), "name": str(skill), "description": "Skill"}
                for skill in ids
            ],
        )
    await seed_rights(engine, owner, ids[:1], grade=SkillGrade.EXPERT)
    await seed_rights(engine, owner, ids[1:2], state=SkillGrantState.PROVISIONAL)
    # A high numeric value with no grant and a provisional grant without probation
    # both remain unauthorized; expert status never propagates to unrelated skills.
    skills = meta_data.tables["user_competencies"]
    exact = Decimal("123.123456789012345678901234567890123456789")
    async with engine.begin() as connection:
        await connection.execute(update(skills).where(skills.c.user_id == owner).values(contribution_sum=exact))
        await connection.execute(
            insert(skills).values(
                id=uuid4(),
                created_at=datetime.now(UTC),
                user_id=owner,
                competency_id=ids[2],
                contribution_sum=100,
                displayed_value=100,
                grade=SkillGrade.EXPERT,
                version=1,
            )
        )
    response = await client.get(PROFILE, headers=headers)
    assert response.status_code == 200, response.text
    values = {UUID(item["competency_id"]): item for item in response.json(parse_float=Decimal)["competencies"]}
    assert values[ids[0]]["contribution_sum"] == exact
    assert values[ids[0]]["next_grade"] is None
    assert (values[ids[0]]["grant"], values[ids[0]]["expert_confirmed"]) == ("confirmed", True)
    assert all(values[skill]["grant"] == "none" and not values[skill]["review_allowed"] for skill in ids[1:])
    expert_id = UUID((await client.get("/auth/me", headers=other)).json()["id"])
    await seed_rights(engine, expert_id, ids[1:2], grade=SkillGrade.EXPERT)
    applications, assessments, grants = (
        meta_data.tables[name] for name in ("reviewer_applications", "reviewer_application_assessments", "skill_grants")
    )
    application_id = uuid4()
    async with engine.begin() as connection:
        expert_grant = await connection.scalar(select(grants.c.id).where(grants.c.user_id == expert_id))
        await connection.execute(
            insert(applications).values(
                id=application_id,
                created_at=datetime.now(UTC),
                user_id=owner,
                competency_id=ids[1],
                requested_grade=SkillGrade.REVIEW_PLUS,
                user_competency_version=1,
                evidence="PRIVATE APPLICATION",
                state=ReviewerApplicationState.CONFIRMED,
                version=1,
            )
        )
        await connection.execute(
            insert(assessments).values(
                id=uuid4(),
                created_at=datetime.now(UTC),
                application_id=application_id,
                application_version=1,
                applicant_id=owner,
                competency_id=ids[1],
                requested_grade=SkillGrade.REVIEW_PLUS,
                expert_user_id=expert_id,
                expert_grant_id=expert_grant,
                expert_grant_version=1,
                expert_competency_version=1,
                expert_value=90,
                decision=ReadinessDecision.CONFIRMED,
                feedback="PRIVATE READINESS FEEDBACK",
                expert_grade=SkillGrade.EXPERT,
                expert_grant_state=SkillGrantState.CONFIRMED,
            )
        )
    owner_events = await client.get(HISTORY, headers=headers)
    assert {item["type"] for item in owner_events.json()} == {
        "wallet_transaction",
        "readiness_requested",
        "readiness_decided",
    }
    expert_events = await client.get(HISTORY, headers=other)
    assert {item["type"] for item in expert_events.json()} == {"wallet_transaction", "readiness_assessed"}
    assert "PRIVATE" not in owner_events.text
    assert "PRIVATE" not in expert_events.text
    before = await facts(engine)
    # Hold the publisher's owner fence: profile must wait BEFORE any effects read,
    # while a different owner's profile can still complete.
    users = meta_data.tables["users"]
    async with engine.connect() as connection, connection.begin():
        await connection.execute(select(users.c.id).where(users.c.id == owner).with_for_update(key_share=True))
        pending = asyncio.create_task(client.get(PROFILE, headers=headers))
        try:
            await asyncio.sleep(0.05)
            assert not pending.done()
            foreign = await asyncio.wait_for(client.get(PROFILE, headers=other), timeout=3)
            assert foreign.status_code == 200, foreign.text
        finally:
            await connection.rollback()
            unblocked = await asyncio.wait_for(pending, timeout=3)
        assert unblocked.status_code == 200, unblocked.text
    assert await facts(engine) == before
    await revoke(engine, owner)
    response = await client.get(PROFILE, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["grade"] == "performer"
    assert all(item["grant"] == "none" and item["evidence"] is None for item in response.json()["competencies"])
    # One SQL UNION page, not one source query per item or an in-memory fetch-all.
    statements: list[str] = []

    def capture(*args: object) -> None:
        statements.append(str(args[2]))

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        page = await client.get(HISTORY, headers=headers, params={"limit": 1})
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
    assert page.status_code == 200, page.text
    union_queries = [statement for statement in statements if "UNION ALL" in statement]
    assert len(union_queries) == 1
    assert "LIMIT" in union_queries[0]
    assert "OFFSET" in union_queries[0]
