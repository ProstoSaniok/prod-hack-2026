import asyncio
from decimal import Decimal
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from src.adapters.data_gateways.review_effects import SqlReviewPublicationEffects
from src.adapters.data_gateways.tables import meta_data
from src.application.development.entity import SkillGrade, SkillGrantState
from src.application.review.entity import AssignmentState, JobState, ReviewState
from src.application.review.finalization import ReviewPublication
from tests.support.learning import edited_solution, learning_client, participant
from tests.support.review_completion import complete, final_draft, save
from tests.support.review_quality import (
    QualityScenario,
    another_submission,
    assess,
    enable,
    expire_request,
    learning_storage,
    pending_review,
    quality_effects,
    quality_scenario,
    revoke,
)
from tests.support.reviews import claim, expire, seed_rights

__all__ = ["learning_client"]


async def test_supervised_pending_is_private_and_independent_expert_quality_precedes_ordinary_work(
    learning_client: AsyncClient, auth_engine: AsyncEngine, quality_scenario: QualityScenario
) -> None:
    client, engine, case = learning_client, auth_engine, quality_scenario
    scenario, review_url = case.submission, case.created.headers["location"]
    receipt = case.created.json()
    task = case.primary.json()
    assert set(task["supervised_competency_ids"]) == set(map(str, scenario.skills))
    assert len(task["reviewer_skills"]) == 4
    assert all(
        skill["grant"] == "supervised" and skill["value"] == 40 and skill["grade"] == "review_minus"
        for skill in task["reviewer_skills"]
    )
    assert receipt["published_at"] is None
    assert receipt["content_url"] == review_url
    assert case.created.headers["cache-control"] == "no-store"
    before = await quality_effects(engine)
    assert before["review_assignments"][0]["supervised"] is True
    assert before["progression_contributions"] == before["probation_successes"] == before["quality_assessments"] == []
    assert all(row["karma"] == 0 and row["settled"] == 10 for row in before["wallets"])
    for headers in (scenario.author, case.reviewer):
        assert (await client.get("/api/v1/me/wallet", headers=headers)).json()["settled"] == 10
    assert (await client.get(review_url, headers=scenario.author)).status_code == 404
    feedback = (await client.get(scenario.submission + "/feedback", headers=scenario.author)).json()
    assert feedback["reviews"] == []
    assert feedback["effective_review_id"] is feedback["progression"] is None
    own = await client.get(review_url, headers=case.reviewer)
    assert own.status_code == 200, own.text
    assert own.json()["current_revision"]["feedback"] == final_draft()["feedback"]
    assert (await client.get(review_url, headers=case.expert)).status_code == 404
    assert await quality_effects(engine) == before

    # Even an otherwise qualified author cannot assess their own submission.
    await seed_rights(engine, scenario.author_id, scenario.skills, grade=SkillGrade.EXPERT)
    await enable(client, scenario.author)
    assert (await claim(client, scenario.author)).status_code == 204
    assert (await claim(client, case.reviewer, "not-own-quality")).status_code == 204
    partial = await participant(client, engine, "partial-expert@example.com")
    partial_id = UUID((await client.get("/auth/me", headers=partial)).json()["id"])
    await seed_rights(engine, partial_id, scenario.skills[:-1], grade=SkillGrade.EXPERT)
    await seed_rights(engine, partial_id, scenario.skills[-1:], grade=SkillGrade.REVIEW_PLUS)
    await enable(client, partial)
    assert (await claim(client, partial)).status_code == 204
    ordinary = await another_submission(client, engine, scenario, "ordinary-")
    assigned = await claim(client, case.expert)
    assert assigned.status_code == 201, assigned.text
    assert (assigned.json()["kind"], assigned.json()["review_id"], assigned.json()["revision_id"]) == (
        "quality_check",
        receipt["id"],
        receipt["revision_id"],
    )
    assert assigned.json()["submission_id"] != ordinary.submission.rsplit("/", 1)[1]
    full = await claim(client, case.expert, "shared-capacity")
    assert full.status_code == 409
    assert full.json()["code"] == "CAPACITY_LIMIT"
    source_url = assigned.headers["location"] + "/source-review"
    before = await quality_effects(engine)
    source = await client.get(source_url, headers=case.expert)
    assert source.status_code == 200, source.text
    source_body = source.json()
    workspace = source_body.pop("workspace")
    assert source_body == own.json()
    assert workspace["assignment"]["id"] == assigned.json()["id"]
    assert workspace["submission_id"] == receipt["submission_id"]
    assert len(workspace["competencies"]) == 4
    assert workspace["commit_sha"] != workspace["base_commit_sha"]
    for field, suffix in (("files_url", "/files"), ("diff_url", "/diff")):
        assert workspace[field] == assigned.headers["location"] + suffix
        assert (await client.get(workspace[field], headers=case.expert)).status_code == 200
    assert source.json()["current_revision"]["comments"] == final_draft()["comments"]
    assert source.headers["cache-control"] == "no-store"
    for headers in (scenario.author, case.reviewer, partial):
        assert (await client.get(source_url, headers=headers)).status_code == 404
        assert (await assess(client, review_url, assigned, headers)).status_code == 404
    assert await quality_effects(engine) == before
    confirmed = await assess(client, review_url, assigned, case.expert)
    assert confirmed.status_code == 201, confirmed.text
    metadata = confirmed.json()
    assert set(metadata) == {
        "id",
        "review_id",
        "assignment_id",
        "revision_id",
        "assessor_id",
        "decision",
        "score",
        "created_at",
        "content_url",
    }
    assert (metadata["decision"], metadata["score"], metadata["assessor_id"]) == ("confirmed", 80, str(case.expert_id))
    assert confirmed.headers["location"] == metadata["content_url"]
    assert confirmed.headers["cache-control"] == "no-store"
    published = await client.get(review_url, headers=scenario.author)
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"
    assert published.json()["current_revision"] == own.json()["current_revision"]
    progression = (await client.get(scenario.submission + "/progression", headers=scenario.author)).json(
        parse_float=Decimal
    )
    assert len(progression["contributions"]) == 4
    assert all(
        item["assessor_id"] == str(case.expert_id)
        and item["expertise_value"] == 90
        and item["contribution"] == Decimal("11.8")
        for item in progression["contributions"]
    )
    assert (await client.get("/api/v1/me/wallet", headers=scenario.author)).json()["settled"] == 13
    assert (await client.get("/api/v1/me/wallet", headers=case.reviewer)).json()["settled"] == 15
    assert (await client.get("/api/v1/me/wallet", headers=case.expert)).json()["settled"] == 10
    facts = await quality_effects(engine)
    assert next(row for row in facts["wallets"] if row["owner_id"] == case.reviewer_id)["karma"] == 1
    assert next(row for row in facts["wallets"] if row["owner_id"] == case.expert_id)["karma"] == 0
    assert len(facts["probation_successes"]) == len(facts["assessor_snapshots"]) == 4
    probation = (await client.get("/api/v1/me/probation?limit=2", headers=case.reviewer)).json()
    assert len(probation["items"]) == 2
    assert probation["next_offset"] == 2
    assert all(item["confirmed_reviews"] == 1 and item["remaining"] == 2 for item in probation["items"])
    assert (await client.get("/api/v1/me/probation", headers=case.expert)).json()["items"] == []
    for path in (confirmed.headers["location"], review_url + "/quality-assessments"):
        assert (await client.get(path, headers=scenario.author)).status_code == 404
    assessment = await client.get(confirmed.headers["location"], headers=case.reviewer)
    assert assessment.status_code == 200, assessment.text
    assert assessment.json()["feedback"].startswith("Private expert feedback")
    assert len(assessment.json()["assessor_skills"]) == 4
    for headers in (case.reviewer, case.expert):
        history = await client.get(review_url + "/quality-assessments?limit=1", headers=headers)
        assert history.status_code == 200, history.text
        assert history.json() == {"items": [metadata], "limit": 1, "offset": 0, "next_offset": None}
        assert "feedback" not in history.json()["items"][0]
        assert "Private expert feedback" not in history.text
    assert "Private expert feedback" not in published.text
    assert "PRIVATE EVIDENCE" not in source.text
    assert "PRIVATE SEED" not in source.text
    next_task = await claim(client, case.expert, "ordinary-after-quality")
    assert next_task.status_code == 201, next_task.text
    assert next_task.json()["kind"] == "review"


async def test_needs_revision_keeps_history_private_and_permanent_replays_never_publish_a_new_revision(
    learning_client: AsyncClient, auth_engine: AsyncEngine, quality_scenario: QualityScenario
) -> None:
    client, engine, case = learning_client, auth_engine, quality_scenario
    review_url = case.created.headers["location"]
    assigned = await claim(client, case.expert)
    assert assigned.status_code == 201, assigned.text
    before = await quality_effects(engine)
    rejected = await assess(client, review_url, assigned, case.expert, decision="needs_revision", score=60)
    assert rejected.status_code == 201, rejected.text
    facts = await quality_effects(engine)
    for name in (
        "wallets",
        "wallet_ledger",
        "probations",
        "probation_successes",
        "progression_contributions",
        "outbox_messages",
    ):
        assert facts[name] == before[name]
    assert (await client.get(review_url, headers=case.reviewer)).json()["status"] == "needs_revision"
    await expire(engine, case.primary.json()["id"])
    root = case.primary.headers["location"]
    draft = await client.get(root + "/draft", headers=case.reviewer)
    assert draft.status_code == 200, draft.text
    assert draft.json()["version"] == 2
    before_workspace = await quality_effects(engine)
    workspace = await client.get(root + "/workspace", headers=case.reviewer)
    assert workspace.status_code == 200, workspace.text
    context = workspace.json()
    assert context["assignment"]["id"] == case.primary.json()["id"]
    assert context["assignment"]["status"] == "completed"
    assert context["submission_id"] == case.created.json()["submission_id"]
    assert context["draft"] == draft.json()
    assert context["draft"]["version"] == case.created.json()["draft_version"]
    assert context["draft"]["feedback"] == final_draft()["feedback"]
    assert context["commit_sha"] != context["base_commit_sha"]
    assert context["case"]["title"] == "Submission case"
    assert len(context["competencies"]) == 4
    assert context["files_url"] == root + "/files"
    assert context["diff_url"] == root + "/diff"
    assert workspace.headers["cache-control"] == "no-store"
    assert await quality_effects(engine) == before_workspace
    corrected = final_draft(2, score=90, marker="Corrected")
    await save(client, root, case.reviewer, corrected)
    corrected_workspace = await client.get(root + "/workspace", headers=case.reviewer)
    assert corrected_workspace.status_code == 200, corrected_workspace.text
    assert corrected_workspace.json()["draft"]["version"] == 3
    assert corrected_workspace.json()["draft"]["feedback"] == corrected["feedback"]
    revision_body = {"expected_revision_id": case.created.json()["revision_id"], "expected_draft_version": 3}
    revision_headers = {**case.reviewer, "Idempotency-Key": "revise"}
    revised = await client.post(review_url + "/revisions", headers=revision_headers, json=revision_body)
    assert revised.status_code == 201, revised.text
    assert (revised.json()["number"], revised.json()["draft_version"]) == (2, 3)
    assert revised.headers["location"] == revised.json()["content_url"]
    revision_id = revised.json()["id"]
    assert revision_id != case.created.json()["revision_id"]
    old_url = review_url + "/revisions/" + case.created.json()["revision_id"]
    old = await client.get(old_url, headers=case.reviewer)
    assert old.status_code == 200, old.text
    assert old.json()["current_revision"]["feedback"] == final_draft()["feedback"]
    for path in (old_url, revised.headers["location"], review_url):
        assert (await client.get(path, headers=case.submission.author)).status_code == 404
    baseline = await quality_effects(engine)
    stale = await assess(client, review_url, assigned, case.expert, revision_id=revision_id, key="stale")
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == "STALE_REVISION"
    replay = await assess(client, review_url, assigned, case.expert, decision="needs_revision", score=60)
    assert replay.status_code == 200, replay.text
    assert replay.content == rejected.content
    assert await quality_effects(engine) == baseline
    fresh = await claim(client, case.expert, "revised-claim")
    assert fresh.status_code == 201, fresh.text
    assert fresh.json()["revision_id"] == revision_id
    assert fresh.json()["id"] != assigned.json()["id"]
    confirmed = await assess(client, review_url, fresh, case.expert, key="confirm-corrected")
    assert confirmed.status_code == 201, confirmed.text
    published = await client.get(review_url, headers=case.submission.author)
    assert published.status_code == 200, published.text
    assert published.json()["current_revision"]["id"] == revision_id
    assert published.json()["current_revision"]["feedback"] == corrected["feedback"]
    assert (await client.get(old_url, headers=case.submission.author)).status_code == 404
    facts = await quality_effects(engine)
    assert len(facts["review_results"]) == 1
    assert len(facts["review_revisions"]) == 2
    assert len(facts["probation_successes"]) == len(facts["progression_contributions"]) == 4
    assert all(row["review_revision_id"] == UUID(revision_id) for row in facts["probation_successes"])
    assert all(row["state"] == AssignmentState.COMPLETED for row in facts["review_assignments"])
    # Original creation metadata survives publication, new keys and journal expiry.
    await expire_request(engine, case.reviewer_id, root + "/reviews", "complete")
    await expire_request(engine, case.reviewer_id, review_url + "/revisions", "revise")
    for key in ("complete", "new-primary-key"):
        primary_replay = await complete(client, root, case.reviewer, key=key)
        assert primary_replay.status_code == 200, primary_replay.text
        assert primary_replay.content == case.created.content
        assert primary_replay.headers["location"] == review_url
    for key in ("revise", "new-revision-key"):
        revision_replay = await client.post(
            review_url + "/revisions", headers={**case.reviewer, "Idempotency-Key": key}, json=revision_body
        )
        assert revision_replay.status_code == 200, revision_replay.text
        assert revision_replay.content == revised.content
        assert revision_replay.headers["location"] == revised.headers["location"]
    old_assessment_replay = await assess(
        client, review_url, assigned, case.expert, decision="needs_revision", score=60, key="old-after-publication"
    )
    assert old_assessment_replay.status_code == 200, old_assessment_replay.text
    assert old_assessment_replay.content == rejected.content
    assert await quality_effects(engine) == facts


async def test_quality_publication_failure_rolls_back_all_effects_and_concurrent_retry_is_once_only(
    learning_client: AsyncClient,
    auth_engine: AsyncEngine,
    quality_scenario: QualityScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, case = learning_client, auth_engine, quality_scenario
    review_url = case.created.headers["location"]
    assigned = await claim(client, case.expert)
    assert assigned.status_code == 201, assigned.text
    baseline = await quality_effects(engine)
    original = SqlReviewPublicationEffects.apply

    async def fail_after_effects(store: SqlReviewPublicationEffects, publication: ReviewPublication) -> None:
        await original(store, publication)
        for name, added in (
            ("quality_assessments", 1),
            ("probation_successes", 4),
            ("wallet_ledger", 2),
            ("outbox_messages", 1),
        ):
            assert (
                await store.session.scalar(select(func.count()).select_from(meta_data.tables[name]))
                == len(baseline[name]) + added
            )
        raise SQLAlchemyError("injected failure after real quality publication effects")

    with monkeypatch.context() as patch:
        patch.setattr(SqlReviewPublicationEffects, "apply", fail_after_effects)
        failed = await assess(client, review_url, assigned, case.expert)
    assert failed.status_code == 503, failed.text
    assert "injected failure" not in failed.text
    assert await quality_effects(engine) == baseline
    responses = await asyncio.wait_for(
        asyncio.gather(*(assess(client, review_url, assigned, case.expert, key=key) for key in ("race-a", "race-b"))),
        timeout=10,
    )
    assert sorted(response.status_code for response in responses) == [200, 201], [
        response.text for response in responses
    ]
    assert responses[0].content == responses[1].content
    assert responses[0].headers["location"] == responses[1].headers["location"]
    facts = await quality_effects(engine)
    assert len(facts["quality_assessments"]) == 1
    assert (
        len(facts["assessor_snapshots"])
        == len(facts["probation_successes"])
        == len(facts["progression_contributions"])
        == 4
    )
    assert len(facts["wallet_ledger"]) == len(baseline["wallet_ledger"]) + 2
    assert len(facts["outbox_messages"]) == len(baseline["outbox_messages"]) + 1
    await revoke(engine, case.expert_id)
    baseline = await quality_effects(engine)
    replay = await assess(client, review_url, assigned, case.expert, key="race-a")
    assert replay.status_code == 404, replay.text
    assert (await client.get(responses[0].headers["location"], headers=case.expert)).status_code == 404
    assert (await client.get(review_url + "/quality-assessments", headers=case.expert)).status_code == 404
    assert (await client.get(review_url, headers=case.submission.author)).status_code == 200
    assert await quality_effects(engine) == baseline


async def test_quality_has_no_deadline_and_current_rights_gate_reads_and_replays(
    learning_client: AsyncClient, auth_engine: AsyncEngine, quality_scenario: QualityScenario
) -> None:
    client, engine, case = learning_client, auth_engine, quality_scenario
    review_url = case.created.headers["location"]
    assigned = await claim(client, case.expert)
    assert assigned.status_code == 201, assigned.text
    await expire(engine, assigned.json()["id"])
    baseline = await quality_effects(engine)
    assert (await claim(client, case.expert)).status_code == 200
    assert (await client.get(assigned.headers["location"] + "/source-review", headers=case.expert)).status_code == 200
    assert await quality_effects(engine) == baseline
    replacement = assigned
    await revoke(engine, case.expert_id)
    baseline = await quality_effects(engine)
    assert (await claim(client, case.expert)).status_code == 403
    assert (
        await client.get(replacement.headers["location"] + "/source-review", headers=case.expert)
    ).status_code == 404
    assert (await client.get(review_url, headers=case.expert)).status_code == 404
    assert (await assess(client, review_url, replacement, case.expert)).status_code == 404
    assert await quality_effects(engine) == baseline
    # Restore the same expert epoch; the lease is still valid, but the original
    # reviewer's fresh authority must also survive until the assessment commits.
    grants = meta_data.tables["skill_grants"]
    async with engine.begin() as connection:
        await connection.execute(
            update(grants)
            .where(grants.c.user_id == case.expert_id)
            .values(state=SkillGrantState.CONFIRMED, revoked_at=None, version=grants.c.version + 1)
        )
    await revoke(engine, case.reviewer_id)
    baseline = await quality_effects(engine)
    denied = await assess(client, review_url, replacement, case.expert, key="revoked-reviewer")
    assert denied.status_code == 403, denied.text
    assert await quality_effects(engine) == baseline


async def test_three_distinct_reviews_graduate_each_skill_but_a_prebound_fourth_still_needs_quality(
    learning_client: AsyncClient, auth_engine: AsyncEngine, quality_scenario: QualityScenario
) -> None:
    client, engine, case = learning_client, auth_engine, quality_scenario
    pending = [case.created]
    for number in range(2, 5):
        await another_submission(client, engine, case.submission, f"case-{number}-")
        _, created = await pending_review(client, case.reviewer, f"primary-{number}")
        pending.append(created)
    assert len({response.json()["id"] for response in pending}) == 4
    baseline = await quality_effects(engine)
    assert len({row["case_id"] for row in baseline["projects"]}) == 4
    assert all(row["state"] == ReviewState.PENDING for row in baseline["review_results"])
    assert all(row["state"] == AssignmentState.COMPLETED for row in baseline["review_assignments"])
    assert all(row["supervised"] for row in baseline["review_assignments"])
    assert baseline["probation_successes"] == []
    for number in range(1, 4):
        assigned = await claim(client, case.expert, f"quality-{number}")
        assert assigned.status_code == 201, assigned.text
        confirmed = await assess(client, f"/api/v1/reviews/{assigned.json()['review_id']}", assigned, case.expert)
        assert confirmed.status_code == 201, confirmed.text
        page = (await client.get("/api/v1/me/probation", headers=case.reviewer)).json()
        assert len(page["items"]) == 4
        assert all(item["confirmed_reviews"] == number and item["remaining"] == 3 - number for item in page["items"])
        assert all((item["completed_at"] is not None) == (number == 3) for item in page["items"])
    facts = await quality_effects(engine)
    grants = [row for row in facts["skill_grants"] if row["user_id"] == case.reviewer_id]
    assert len(grants) == 4
    assert all(row["state"] == SkillGrantState.CONFIRMED and row["grade"] == SkillGrade.REVIEW_MINUS for row in grants)
    assert all(row["confirmed_by_user_id"] == case.expert_id for row in grants)
    reviewer_skills = [row for row in facts["user_competencies"] if row["user_id"] == case.reviewer_id]
    assert len(reviewer_skills) == 4
    assert all(row["displayed_value"] == 40 and row["grade"] == SkillGrade.REVIEW_MINUS for row in reviewer_skills)
    assert len(facts["probation_successes"]) == 12
    assert len({row["review_id"] for row in facts["probation_successes"]}) == 3
    fourth = pending[-1]
    assert (await client.get(fourth.headers["location"], headers=case.reviewer)).json()[
        "status"
    ] == "pending_quality_check"
    assert (await client.get(fourth.headers["location"], headers=case.submission.author)).status_code == 404
    assert (await client.get("/api/v1/me/wallet", headers=case.reviewer)).json()["settled"] == 25
    assert (await client.get("/api/v1/me/wallet", headers=case.submission.author)).json()["settled"] == 19
    assigned = await claim(client, case.expert, "quality-fourth")
    assert assigned.status_code == 201, assigned.text
    assert assigned.json()["review_id"] == fourth.json()["id"]
    confirmed = await assess(client, fourth.headers["location"], assigned, case.expert)
    assert confirmed.status_code == 201, confirmed.text
    after = await quality_effects(engine)
    assert after["probations"] == facts["probations"]
    assert after["probation_successes"] == facts["probation_successes"]
    assert len(after["quality_assessments"]) == 4
    assert (await client.get("/api/v1/me/wallet", headers=case.reviewer)).json()["settled"] == 30
    assert (await client.get("/api/v1/me/wallet", headers=case.submission.author)).json()["settled"] == 22
    assert (await client.get("/api/v1/me/wallet", headers=case.expert)).json()["settled"] == 10
    # The newly confirmed reviewer must still be able to submit their own work.
    # Graduation adds expert confirmation provenance to a PROGRESSION grant.
    original = (await client.get(case.submission.project, headers=case.submission.author)).json()
    own = await client.post(
        "/api/v1/me/projects",
        headers={**case.reviewer, "Idempotency-Key": "graduate-start"},
        json={"case_id": original["case_id"]},
    )
    assert own.status_code == 201, own.text
    own_root = own.headers["location"]
    head = await edited_solution(client, own_root, case.reviewer, "Graduate's solution\n")
    submitted = await client.post(
        own_root + "/submissions",
        headers={**case.reviewer, "Idempotency-Key": "graduate-submit"},
        json={"expected_commit_sha": head},
    )
    assert submitted.status_code == 201, submitted.text


__all__ += ["learning_storage", "quality_scenario"]


async def test_quality_return_preserves_pinned_revision_and_allows_another_expert(
    learning_client: AsyncClient, auth_engine: AsyncEngine, quality_scenario: QualityScenario
) -> None:
    client, engine, case = learning_client, auth_engine, quality_scenario
    assigned = await claim(client, case.expert)
    assert assigned.status_code == 201
    await expire(engine, assigned.json()["id"])
    await revoke(engine, case.expert_id)
    assert (
        await client.delete(assigned.headers["location"], headers={**case.expert, "Idempotency-Key": "return"})
    ).status_code == 204
    other = await participant(client, engine, "other-expert@example.com")
    other_id = UUID((await client.get("/auth/me", headers=other)).json()["id"])
    await seed_rights(engine, other_id, case.submission.skills, grade=SkillGrade.EXPERT)
    await enable(client, other)
    replacement = await claim(client, other)
    assert replacement.status_code == 201
    assert replacement.json()["revision_id"] == assigned.json()["revision_id"]
    await expire(engine, replacement.json()["id"])
    assert (await assess(client, case.created.headers["location"], replacement, other)).status_code == 201


async def test_quality_return_and_assessment_race_has_one_terminal_outcome(
    learning_client: AsyncClient, auth_engine: AsyncEngine, quality_scenario: QualityScenario
) -> None:
    client, engine, case = learning_client, auth_engine, quality_scenario
    assigned = await claim(client, case.expert)
    assert assigned.status_code == 201
    returned, assessed = await asyncio.gather(
        client.delete(assigned.headers["location"], headers={**case.expert, "Idempotency-Key": "return"}),
        assess(client, case.created.headers["location"], assigned, case.expert),
    )
    assert (returned.status_code, assessed.status_code) in {(204, 409), (409, 201)}
    facts = await quality_effects(engine)
    assignment = next(row for row in facts["review_assignments"] if row["id"] == UUID(assigned.json()["id"]))
    cancelled = assignment["state"] == AssignmentState.CANCELLED
    assert assignment["state"] in {AssignmentState.CANCELLED, AssignmentState.COMPLETED}
    job = next(row for row in facts["review_jobs"] if row["id"] == assignment["job_id"])
    assert job["state"] == (JobState.WAITING if cancelled else JobState.COMPLETED)
    assert len(facts["quality_assessments"]) == (0 if cancelled else 1)
    assert facts["review_results"][0]["state"] == (ReviewState.PENDING if cancelled else ReviewState.PUBLISHED)
