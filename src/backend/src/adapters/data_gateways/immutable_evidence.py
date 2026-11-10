"""Guard write-once evidence through the ORM; privileged SQL remains outside this guard."""

from functools import partial
from typing import NoReturn

from sqlalchemy import Connection, event
from sqlalchemy.orm import Mapper
from sqlalchemy.orm.attributes import get_history

from src.application.development.entity import SkillGrant, SkillGrantState
from src.application.review.entity import ReviewComment


def reject_evidence_change(mapper: Mapper[object], connection: Connection, target: object) -> NoReturn:
    raise ValueError(f"{type(target).__name__} is write-once evidence")


def protect_evidence(entity: type[object]) -> None:
    event.listen(entity, "before_update", reject_evidence_change)
    event.listen(entity, "before_delete", reject_evidence_change)


def protect_revision_comment(mapper: Mapper[ReviewComment], connection: Connection, target: ReviewComment) -> None:
    if target.revision_id is not None or any(get_history(target, "revision_id").deleted):
        raise ValueError("Revision comments are write-once evidence")


def reject_changed_evidence(
    mapper: Mapper[object], connection: Connection, target: object, *, mutable_fields: frozenset[str]
) -> None:
    for attribute in mapper.column_attrs:
        if attribute.key not in mutable_fields and get_history(target, attribute.key).has_changes():
            raise ValueError(f"{type(target).__name__}.{attribute.key} is write-once evidence")


def protect_evidence_columns(entity: type[object], mutable_fields: frozenset[str]) -> None:
    event.listen(entity, "before_update", partial(reject_changed_evidence, mutable_fields=mutable_fields))
    event.listen(entity, "before_delete", reject_evidence_change)


def protect_grant_transition(mapper: Mapper[SkillGrant], connection: Connection, target: SkillGrant) -> None:
    previous_states = get_history(target, "state").deleted
    if previous_states and previous_states[0] == SkillGrantState.REVOKED and target.state != SkillGrantState.REVOKED:
        raise ValueError("Revoked grants cannot be restored; record a new grant")
    if (
        previous_states
        and previous_states[0] == SkillGrantState.CONFIRMED
        and target.state == SkillGrantState.PROVISIONAL
    ):
        raise ValueError("Confirmed grants cannot return to provisional")
    for timestamp in ("confirmed_at", "revoked_at"):
        if any(value is not None for value in get_history(target, timestamp).deleted):
            raise ValueError("Historical grant timestamps are write-once evidence")
