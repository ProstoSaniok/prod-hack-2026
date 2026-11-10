import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from src.adapters.data_gateways.learning_views import row_mapping
from src.adapters.data_gateways.tables import meta_data
from src.adapters.operations.review_requests import ReviewRequests
from src.adapters.operations.sql_records import value
from src.adapters.wallet.persistence import SqlAlchemyWalletTransaction
from src.application.development.entity import SkillGrade
from src.application.operations.idempotency import CachedResponse
from src.application.repository_storage.git import GitStorage
from src.application.review.entity import AssignmentState, JobState
from src.application.wallet.entity import ReservationState, SourceKind, WalletChange
from tests.support.learning import MemoryGit, edited_solution, learning_client, participant, submission_project
from tests.support.review_completion import complete, final_draft, save
from tests.support.review_quality import (
    another_submission,
    enable,
    expert,
    expire_request,
    pending_review,
    provisional,
)
from tests.support.reviews import QUEUE, Scenario, claim, expire, reviewer, seed_rights, submitted

__all__ = ["learning_client"]
WALLET = "/api/v1/me/wallet"
PRIORITIES = "/api/v1/me/priority-requests"


@pytest.fixture
def learning_storage() -> GitStorage:
    return MemoryGit()


async def reserve(client: AsyncClient, scenario: Scenario, key: str = "reserve") -> Response:
    return await client.post(
        scenario.submission + "/priority-requests", headers={**scenario.author, "Idempotency-Key": key}
    )


async def cancel(client: AsyncClient, request: Response, author: dict[str, str], key: str = "cancel") -> Response:
    return await client.delete(request.headers["location"], headers={**author, "Idempotency-Key": key})


async def wallet(client: AsyncClient, owner: dict[str, str], settled: int, reserved: int) -> None:
    response = await client.get(WALLET, headers=owner)
    assert response.status_code == 200, response.text
    assert tuple(response.json()[field] for field in ("settled", "reserved", "available")) == (
        settled,
        reserved,
        settled - reserved,
    )


async def priority_state(client: AsyncClient, request: Response, owner: dict[str, str], status: str) -> None:
    response = await client.get(request.headers["location"], headers=owner)
    assert response.status_code == 200, response.text
    assert response.json()["id"] == request.json()["id"]
    assert response.json()["status"] == status
    assert response.headers["cache-control"] == "no-store"
    listing = await client.get(PRIORITIES, headers=owner)
    assert listing.status_code == 200, listing.text
    assert response.json() in listing.json()


async def facts(engine: AsyncEngine) -> dict[str, list[dict[str, object]]]:
    """Compare durable state, not just counts, across read/replay/failure paths."""
    result = {}
    async with engine.connect() as connection:
        for name in (
            "wallets",
            "wallet_ledger",
            "wallet_reservations",
            "priority_requests",
            "priority_scheduler",
            "review_jobs",
            "review_assignments",
            "assignment_competencies",
            "review_drafts",
            "request_scopes",
            "request_generations",
        ):
            table = meta_data.tables[name]
            rows = (await connection.execute(select(table).order_by(*table.primary_key.columns))).mappings()
            result[name] = [row_mapping(row) for row in rows]
    return result


async def independent_submission(client: AsyncClient, engine: AsyncEngine, source: Scenario, prefix: str) -> Scenario:
    """Distinct case and author, but identical skills/performer snapshots for fair FIFO comparisons."""
    project, author, author_id, skills = await submission_project(
        client, engine, skill_ids=source.skills, email_prefix=prefix
    )
    head = await edited_solution(client, project, author)
    response = await client.post(
        project + "/submissions", headers={**author, "Idempotency-Key": "submit"}, json={"expected_commit_sha": head}
    )
    assert response.status_code == 201, response.text
    return Scenario(project, author, author_id, skills, response.headers["location"])


async def test_priority_hold_replay_privacy_cancellation_and_fresh_attempt(
    learning_client: AsyncClient, auth_engine: AsyncEngine
) -> None:
    client, engine = learning_client, auth_engine
    first = await submitted(client, engine)
    second = await another_submission(client, engine, first, "second-")
    stranger = await participant(client, engine, "stranger@example.com")
    url = first.submission + "/priority-requests"
    before = await facts(engine)
    available = await client.get(first.submission, headers=first.author)
    assert available.json()["priority"] == {
        "cost": 5,
        "can_purchase": True,
        "can_cancel": False,
        "current_request": None,
    }
    assert (await client.post(url, headers={"Idempotency-Key": "unauthenticated"})).status_code == 401
    assert (await client.post(url, headers=first.author)).status_code == 422
    assert (await client.post(url, headers={**stranger, "Idempotency-Key": "foreign"})).status_code == 404
    for body in ({"cost": 0}, {"cost": 500}, {}):
        rejected = await client.post(url, headers={**first.author, "Idempotency-Key": "invalid"}, json=body)
        assert rejected.status_code == 422, rejected.text
    assert await facts(engine) == before

    responses = await asyncio.wait_for(
        asyncio.gather(*(reserve(client, first, key) for key in ("reserve-a", "reserve-b"))), timeout=10
    )
    assert sorted(response.status_code for response in responses) == [200, 201], [r.text for r in responses]
    held = next(response for response in responses if response.status_code == 201)
    assert responses[0].content == responses[1].content
    assert held.json()["cost"] == 5
    await wallet(client, first.author, 10, 5)
    other = await reserve(client, second)
    assert other.status_code == 201, other.text
    await wallet(client, first.author, 10, 10)
    # Existing active intent is found before checking funds, including an expired key.
    await expire_request(engine, first.author_id, url, "reserve-a")
    for key in ("new-equal", "reserve-a"):
        replay = await reserve(client, first, key)
        assert replay.status_code == 200, replay.text
        assert replay.content == held.content
    assert len((await facts(engine))["wallet_reservations"]) == 2

    before = await facts(engine)
    await priority_state(client, held, first.author, "reserved")
    available = (await client.get(first.submission, headers=first.author)).json()["priority"]
    assert (available["can_purchase"], available["can_cancel"]) == (False, True)
    assert available["current_request"] == (await client.get(held.headers["location"], headers=first.author)).json()
    assert (await client.get(held.headers["location"], headers=stranger)).status_code == 404
    assert (await client.get(PRIORITIES, headers=stranger)).json() == []
    assert (await cancel(client, held, stranger)).status_code == 404
    rejected = await client.request(
        "DELETE", held.headers["location"], headers={**first.author, "Idempotency-Key": "invalid"}, json={"cost": 0}
    )
    assert rejected.status_code == 422, rejected.text
    assert await facts(engine) == before
    for _ in range(2):
        deleted = await cancel(client, held, first.author)
        assert deleted.status_code == 204, deleted.text
        assert deleted.content == b""
    assert (await cancel(client, held, first.author, "new-cancel")).status_code == 409
    await wallet(client, first.author, 10, 5)
    await priority_state(client, held, first.author, "cancelled")
    available = (await client.get(first.submission, headers=first.author)).json()["priority"]
    assert (available["can_purchase"], available["can_cancel"]) == (True, False)
    assert available["current_request"]["status"] == "cancelled"
    assert (await client.get(held.headers["location"], headers=stranger)).status_code == 404
    retained = await reserve(client, first, "reserve-b")
    assert retained.status_code == 200, retained.text
    assert retained.content == held.content
    assert retained.json()["status"] == "reserved"  # Receipt, not a current-state GET.
    fresh = await reserve(client, first, "fresh-attempt")
    assert fresh.status_code == 201, fresh.text
    assert fresh.json()["id"] != held.json()["id"]
    assert fresh.json()["reservation_id"] != held.json()["reservation_id"]
    await wallet(client, first.author, 10, 10)
    await priority_state(client, fresh, first.author, "reserved")
    assert len((await client.get(PRIORITIES, headers=first.author)).json()) == 3


async def test_eligible_claim_settles_once_explicit_return_refunds_but_pending_quality_does_not(
    learning_client: AsyncClient, auth_engine: AsyncEngine
) -> None:
    client, engine = learning_client, auth_engine
    first = await submitted(client, engine)
    second = await another_submission(client, engine, first, "second-")
    held = await reserve(client, first)
    assert held.status_code == 201, held.text
    partial = await reviewer(client, engine, first.skills[:-1], "partial@example.com")
    # An owner with all expert skills is still not eligible for their own work.
    await seed_rights(engine, first.author_id, first.skills, grade=SkillGrade.EXPERT)
    await enable(client, first.author)
    for headers in (partial, first.author):
        assert (await claim(client, headers)).status_code == 204
        await wallet(client, first.author, 10, 5)
        await priority_state(client, held, first.author, "reserved")
    qualified = await reviewer(client, engine, first.skills, "qualified@example.com")
    assigned = await claim(client, qualified)
    assert assigned.status_code == 201, assigned.text
    assert assigned.json()["submission_id"] == first.submission.rsplit("/", 1)[1]
    replay = await claim(client, qualified)
    assert replay.status_code == 200, replay.text
    assert replay.content == assigned.content
    await wallet(client, first.author, 5, 0)
    await priority_state(client, held, first.author, "settled")
    available = (await client.get(first.submission, headers=first.author)).json()["priority"]
    assert (available["can_purchase"], available["can_cancel"]) == (False, False)
    assert available["current_request"]["status"] == "settled"
    assert (await client.get(held.headers["location"], headers=qualified)).status_code == 404
    assert (await cancel(client, held, first.author)).status_code == 409
    assert (await reserve(client, first, "already-assigned")).status_code == 409

    await expire(engine, assigned.json()["id"])
    before = await facts(engine)
    await priority_state(client, held, first.author, "settled")
    assert len((await client.get(QUEUE, headers=qualified)).json()) == 1
    assert (await claim(client, qualified)).status_code == 200
    assert await facts(engine) == before
    for key in ("return", "return", "return-again"):
        response = await client.delete(assigned.headers["location"], headers={**qualified, "Idempotency-Key": key})
        assert response.status_code == 204, response.text
    replacement_headers = await reviewer(client, engine, first.skills, "replacement@example.com")
    replacement = await claim(client, replacement_headers, "explicit-replacement")
    assert replacement.status_code == 201, replacement.text
    assert replacement.json()["submission_id"] == assigned.json()["submission_id"]
    assert replacement.json()["id"] != assigned.json()["id"]
    await wallet(client, first.author, 10, 0)
    await priority_state(client, held, first.author, "refunded")
    assert (await client.get(held.headers["location"], headers=qualified)).status_code == 404
    state = await facts(engine)
    old = next(row for row in state["review_assignments"] if row["id"] == UUID(assigned.json()["id"]))
    assert old["state"] == AssignmentState.CANCELLED
    assert state["wallet_reservations"][0]["state"] == ReservationState.REFUNDED
    refund = next(row for row in state["wallet_ledger"] if row["source_kind"] == SourceKind.REFUND)
    settlement = next(row for row in state["wallet_ledger"] if row["source_kind"] == SourceKind.SETTLE)
    assert refund["related_entry_id"] == settlement["id"]
    assert refund["assignment_id"] == settlement["assignment_id"] == UUID(assigned.json()["id"])

    await save(client, replacement.headers["location"], replacement_headers, final_draft())
    published = await complete(client, replacement.headers["location"], replacement_headers)
    assert published.status_code == 201, published.text
    second_hold = await reserve(client, second)
    assert second_hold.status_code == 201, second_hold.text
    supervised, _ = await provisional(client, engine, first.skills)
    primary, pending = await pending_review(client, supervised)
    assert primary.json()["submission_id"] == second.submission.rsplit("/", 1)[1]
    await wallet(client, first.author, 8, 0)  # 10 + successful-case 3 - next priority 5.
    await expire(engine, primary.json()["id"])
    expert_headers, _ = await expert(client, engine, first.skills)
    quality = await claim(client, expert_headers)
    assert quality.status_code == 201, quality.text
    assert quality.json()["review_id"] == pending.json()["id"]
    assert quality.json()["kind"] == "quality_check"
    returned = await client.delete(
        quality.headers["location"], headers={**expert_headers, "Idempotency-Key": "quality-return"}
    )
    assert returned.status_code == 204, returned.text
    assert (await claim(client, expert_headers, "not-again")).status_code == 204
    await priority_state(client, second_hold, first.author, "settled")
    await wallet(client, first.author, 8, 0)
    after = await facts(engine)
    assert sum(row["source_kind"] == SourceKind.REFUND for row in after["wallet_ledger"]) == 1
    assert sum(row["source_kind"] == SourceKind.SETTLE for row in after["wallet_ledger"]) == 2
    completed = next(row for row in after["review_assignments"] if row["id"] == UUID(primary.json()["id"]))
    assert completed["state"] == AssignmentState.COMPLETED


async def test_concurrent_cross_job_reservations_cannot_overspend_and_claim_cancel_conserve_funds(
    learning_client: AsyncClient, auth_engine: AsyncEngine
) -> None:
    client, engine = learning_client, auth_engine
    first = await submitted(client, engine)
    scenarios = [first, *[await another_submission(client, engine, first, f"case-{i}-") for i in range(2)]]
    responses = await asyncio.wait_for(asyncio.gather(*(reserve(client, case) for case in scenarios)), timeout=10)
    assert sorted(response.status_code for response in responses) == [201, 201, 409], [r.text for r in responses]
    denied = next(response for response in responses if response.status_code == 409)
    assert denied.json()["code"] == "INSUFFICIENT_FUNDS"
    await wallet(client, first.author, 10, 10)
    target = next(response for response in responses if response.status_code == 201)
    qualified = await reviewer(client, engine, first.skills, "race-reviewer@example.com")
    allocated, cancelled = await asyncio.wait_for(
        asyncio.gather(claim(client, qualified), cancel(client, target, first.author)), timeout=10
    )
    assert allocated.status_code == 201, allocated.text
    current = await client.get(target.headers["location"], headers=first.author)
    state = current.json()["status"]
    assert (cancelled.status_code, state) in {(204, "cancelled"), (409, "settled")}
    await wallet(client, first.author, 5, 0 if state == "cancelled" else 5)
    before = await facts(engine)
    assert (await claim(client, qualified)).status_code == 200
    assert await facts(engine) == before
    assert len(before["priority_requests"]) == len(before["wallet_reservations"]) == 2
    assert len(before["review_assignments"]) == len(before["review_drafts"]) == 1
    assert sum(row["source_kind"] == SourceKind.SETTLE for row in before["wallet_ledger"]) == 1
    assert sum(row["source_kind"] == SourceKind.RELEASE for row in before["wallet_ledger"]) == (state == "cancelled")
    assert (
        sum(value(row, "settled_delta", int) for row in before["wallet_ledger"] if row["owner_id"] == first.author_id)
        == 5
    )


async def test_claim_failure_after_settlement_or_receipt_rolls_back_every_effect(
    learning_client: AsyncClient, auth_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine = learning_client, auth_engine
    scenario = await submitted(client, engine)
    held = await reserve(client, scenario)
    assert held.status_code == 201, held.text
    qualified = await reviewer(client, engine, scenario.skills, "rollback-reviewer@example.com")
    original_apply = SqlAlchemyWalletTransaction.apply
    original_capture = ReviewRequests.complete

    async def fail_after_settlement(transaction: SqlAlchemyWalletTransaction, change: WalletChange) -> None:
        await original_apply(transaction, change)
        assert change.entry.source.kind == SourceKind.SETTLE
        assert change.after.settled == 5
        assert change.after.reserved == 0
        raise SQLAlchemyError("injected failure after persisted priority settlement")

    async def fail_after_capture(requests: ReviewRequests, response: CachedResponse) -> None:
        await original_capture(requests, response)
        assert response.status_code == 201
        holds = meta_data.tables["wallet_reservations"]
        assert await requests.session.scalar(select(holds.c.state)) == ReservationState.SETTLED
        raise SQLAlchemyError("injected failure after captured priority claim")

    before = await facts(engine)
    for stage in ("settlement", "capture"):
        with monkeypatch.context() as patch:
            if stage == "settlement":
                patch.setattr(SqlAlchemyWalletTransaction, "apply", fail_after_settlement)
            else:
                patch.setattr(ReviewRequests, "complete", fail_after_capture)
            failed = await claim(client, qualified)
        assert failed.status_code == 503, failed.text
        assert "injected failure" not in failed.text
        assert await facts(engine) == before
        await wallet(client, scenario.author, 10, 5)
        await priority_state(client, held, scenario.author, "reserved")
    responses = await asyncio.wait_for(asyncio.gather(*(claim(client, qualified) for _ in range(2))), timeout=10)
    assert sorted(response.status_code for response in responses) == [200, 201], [r.text for r in responses]
    assert responses[0].content == responses[1].content
    await wallet(client, scenario.author, 5, 0)
    after = await facts(engine)
    assert len(after["review_assignments"]) == len(after["review_drafts"]) == 1
    assert after["review_jobs"][0]["state"] == JobState.CLAIMED
    assert after["priority_scheduler"][0]["consecutive_priority"] == 1
    assert sum(row["source_kind"] == SourceKind.SETTLE for row in after["wallet_ledger"]) == 1


async def test_return_failure_after_refund_or_receipt_rolls_back_all_effects(
    learning_client: AsyncClient, auth_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine = learning_client, auth_engine
    scenario = await submitted(client, engine)
    held = await reserve(client, scenario)
    headers = await reviewer(client, engine, scenario.skills, "return-failure@example.com")
    assigned = await claim(client, headers)
    assert assigned.status_code == 201
    original_apply, original_capture = SqlAlchemyWalletTransaction.apply, ReviewRequests.complete

    async def fail_after_refund(transaction: SqlAlchemyWalletTransaction, change: WalletChange) -> None:
        await original_apply(transaction, change)
        assert change.entry.source.kind == SourceKind.REFUND
        raise SQLAlchemyError("injected failure after refund")

    async def fail_after_capture(requests: ReviewRequests, response: CachedResponse) -> None:
        await original_capture(requests, response)
        assert response.status_code == 204
        raise SQLAlchemyError("injected failure after return capture")

    before = await facts(engine)
    for stage in ("refund", "capture"):
        with monkeypatch.context() as patch:
            if stage == "refund":
                patch.setattr(SqlAlchemyWalletTransaction, "apply", fail_after_refund)
            else:
                patch.setattr(ReviewRequests, "complete", fail_after_capture)
            failed = await client.delete(assigned.headers["location"], headers={**headers, "Idempotency-Key": "return"})
        assert failed.status_code == 503, failed.text
        assert "injected failure" not in failed.text
        assert await facts(engine) == before
    for _ in range(2):
        returned = await client.delete(assigned.headers["location"], headers={**headers, "Idempotency-Key": "return"})
        assert returned.status_code == 204
    await wallet(client, scenario.author, 10, 0)
    await priority_state(client, held, scenario.author, "refunded")
    after = await facts(engine)
    assert sum(row["source_kind"] == SourceKind.REFUND for row in after["wallet_ledger"]) == 1


async def test_persisted_three_to_one_fairness_fifo_and_aged_ordinary_precedes_priority(
    learning_client: AsyncClient, auth_engine: AsyncEngine
) -> None:
    client, engine = learning_client, auth_engine
    first = await submitted(client, engine)
    priority_cases = [first, await another_submission(client, engine, first, "second-")]
    priority_cases.extend([await independent_submission(client, engine, first, f"priority-{i}-") for i in range(2)])
    ordinary = await another_submission(client, engine, first, "ordinary-")
    aged = await another_submission(client, engine, first, "aged-")
    for case in priority_cases:
        response = await reserve(client, case)
        assert response.status_code == 201, response.text
    jobs = meta_data.tables["review_jobs"]
    # Trusted fixture timestamps, not public clock input. Equal distance and time
    # for the first pair exercise the stable job-ID tiebreak, not purchase order.
    now = datetime.now(UTC)
    async with engine.begin() as connection:
        for index, case in enumerate(priority_cases):
            await connection.execute(
                update(jobs)
                .where(jobs.c.submission_id == UUID(case.submission.rsplit("/", 1)[1]))
                .values(created_at=now - timedelta(hours=2) + timedelta(seconds=max(0, index - 1)))
            )
        await connection.execute(
            update(jobs)
            .where(jobs.c.submission_id == UUID(aged.submission.rsplit("/", 1)[1]))
            .values(created_at=now - timedelta(hours=25))
        )
        priority_order = (
            (
                await connection.execute(
                    select(jobs.c.submission_id)
                    .where(
                        jobs.c.submission_id.in_([UUID(case.submission.rsplit("/", 1)[1]) for case in priority_cases])
                    )
                    .order_by(jobs.c.created_at, jobs.c.id)
                )
            )
            .scalars()
            .all()
        )
    headers, _ = await expert(client, engine, first.skills)
    expected = [UUID(aged.submission.rsplit("/", 1)[1]), *priority_order[:3]]
    expected.extend([UUID(ordinary.submission.rsplit("/", 1)[1]), priority_order[3]])
    scheduler = meta_data.tables["priority_scheduler"]
    for index, (submission_id, streak) in enumerate(zip(expected, (0, 1, 2, 3, 0, 1), strict=True)):
        assigned = await claim(client, headers, f"claim-{index}")
        assert assigned.status_code == 201, assigned.text
        assert assigned.json()["submission_id"] == str(submission_id)
        async with engine.connect() as connection:
            assert await connection.scalar(select(scheduler.c.consecutive_priority)) == streak
        # Free capacity through the real publication path, not direct state edits.
        await save(client, assigned.headers["location"], headers, final_draft())
        published = await complete(client, assigned.headers["location"], headers)
        assert published.status_code == 201, published.text
    assert (await claim(client, headers, "empty")).status_code == 204
    after = await facts(engine)
    assert len(after["review_assignments"]) == 6
    assert all(row["state"] == AssignmentState.COMPLETED for row in after["review_assignments"])
    assert all(row["state"] == ReservationState.SETTLED for row in after["wallet_reservations"])
    assert sum(row["source_kind"] == SourceKind.SETTLE for row in after["wallet_ledger"]) == 4
