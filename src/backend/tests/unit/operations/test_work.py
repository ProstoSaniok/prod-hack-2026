from dataclasses import replace
from datetime import timedelta
from typing import cast
from uuid import uuid4

import pytest

from src.application.operations.entity import OperationSpec, ProjectionSpec
from src.application.operations.recovery import Lookup, LookupState
from src.application.operations.work import FailureCode, LeaseLostError, RetryPolicy, Work, WorkState
from tests.unit.operations.fakes import FakeClock


def test_retry_delays_are_exponential_capped_and_budget_bounded() -> None:
    policy = RetryPolicy(max_attempts=10, max_delay=timedelta(seconds=5))
    assert [policy.delay(n).total_seconds() for n in range(1, 7)] == [1, 2, 4, 5, 5, 5]
    for attempt in (0, 11):
        with pytest.raises(ValueError, match="attempt"):
            policy.delay(attempt)


def test_work_due_fences_expiry_and_failure_transitions() -> None:
    clock, policy = FakeClock(), RetryPolicy(max_attempts=2)
    token = uuid4()
    work = Work(created_at=clock.now(), available_at=clock.now())
    assert work.is_due(clock.now())
    claimed = work.claim(now=clock.now(), owner="t03", token=token, policy=policy)
    assert not claimed.is_due(clock.now())
    with pytest.raises(ValueError, match="not due"):
        claimed.claim(now=clock.now(), owner="another", token=uuid4(), policy=policy)
    with pytest.raises(LeaseLostError):
        claimed.succeed(token=uuid4(), now=clock.now())
    retried = claimed.fail(token=token, now=clock.now(), code=FailureCode.TIMEOUT, policy=policy)
    assert retried.state == WorkState.RETRY_WAIT
    assert not retried.is_due(clock.now())
    clock.advance(timedelta(seconds=1))
    second = retried.claim(now=clock.now(), owner="t03", token=token, policy=policy)
    exhausted = second.fail(token=token, now=clock.now(), code=FailureCode.TIMEOUT, policy=policy)
    assert exhausted.state == WorkState.NEEDS_ATTENTION
    assert not exhausted.is_due(clock.now())
    clock.advance(policy.lease_for)
    with pytest.raises(LeaseLostError):
        second.succeed(token=token, now=clock.now())


def test_operation_and_projection_identifiers_reject_unbounded_or_secret_content() -> None:
    original = OperationSpec(kind="repo.ensure", source_key="project:1", subject_id=uuid4(), payload_hash="a" * 64)
    with pytest.raises(ValueError, match="kind"):
        replace(original, kind="bad value")
    with pytest.raises(ValueError, match="source"):
        replace(original, source_key="bad\nvalue")
    with pytest.raises(ValueError, match="hash"):
        replace(original, payload_hash="not-a-hash")
    with pytest.raises(ValueError, match="revisions"):
        ProjectionSpec(topic="review", source_key="review:1", subject_id=uuid4(), revision=0)
    with pytest.raises(ValueError, match="external ID"):
        Lookup(state=LookupState.FOUND)
    with pytest.raises(ValueError, match="Only"):
        Lookup(state=LookupState.ABSENT, external_id="42")
    with pytest.raises(ValueError, match="stable"):
        Lookup(state=LookupState.FOUND, external_id="https://host/?token=secret")
    with pytest.raises(TypeError, match="Unknown"):
        Lookup(state=cast(LookupState, "unexpected"))
