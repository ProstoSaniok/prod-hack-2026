"""Immutable revision staging shared by first completion and private correction."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.learning_views import row_mapping
from src.adapters.data_gateways.review_matching import ReviewerSkill
from src.adapters.data_gateways.tables import meta_data
from src.adapters.operations.sql_records import value
from src.application.catalog.management import CaseConflictError, CaseValidationError
from src.application.development.policy_types import POLICY_VERSION
from src.application.review.entity import JobState, ReviewKind, ReviewVerdict, RevisionContent
from src.application.review.finalization import PublicationSkill, require_final_content


def final_content(draft: dict[str, object]) -> RevisionContent:
    if not isinstance(draft["score"], Decimal) or not isinstance(draft["verdict"], ReviewVerdict):
        raise CaseValidationError("A final score and verdict are required")
    try:
        content = RevisionContent(
            score=draft["score"],
            verdict=draft["verdict"],
            strengths=value(draft, "strengths", str),
            problems=value(draft, "problems", str),
            next_steps=value(draft, "next_steps", str),
        )
    except ValueError:
        raise CaseValidationError("Final feedback is incomplete") from None
    require_final_content(content)
    return content


def publication_skill(requirement: dict[str, object], right: ReviewerSkill) -> PublicationSkill:
    return PublicationSkill(
        competency_id=value(requirement, "competency_id", UUID),
        weight=value(requirement, "weight", Decimal),
        reviewer_value=right.competency.displayed_value,
        reviewer_grade=right.competency.grade,
        grant_id=right.grant.id,
        grant_version=right.grant.version,
        competency_version=right.competency.version,
        grant_state=right.grant.state,
    )


async def save_revision(
    session: AsyncSession,
    *,
    assignment_id: UUID,
    review_id: UUID,
    submission_id: UUID,
    draft_version: int,
    number: int,
    now: datetime,
    skills: tuple[PublicationSkill, ...],
) -> tuple[UUID, RevisionContent]:
    drafts, revisions = meta_data.tables["review_drafts"], meta_data.tables["review_revisions"]
    draft = (
        (await session.execute(select(drafts).where(drafts.c.assignment_id == assignment_id).with_for_update()))
        .mappings()
        .one()
    )
    if draft["version"] != draft_version:
        raise CaseConflictError("STALE_VERSION", "Draft version changed")
    content = final_content(row_mapping(draft))
    revision_id = uuid4()
    await session.execute(
        insert(revisions).values(
            id=revision_id,
            created_at=now,
            review_id=review_id,
            submission_id=submission_id,
            number=number,
            source_draft_version=draft_version,
            policy_version=POLICY_VERSION,
            score=content.score,
            verdict=content.verdict,
            strengths=content.strengths,
            problems=content.problems,
            next_steps=content.next_steps,
        )
    )
    await copy_comments(session, value(draft, "id", UUID), revision_id)
    for skill in skills:
        await session.execute(
            insert(meta_data.tables["reviewer_snapshots"]).values(
                id=uuid4(),
                created_at=now,
                revision_id=revision_id,
                competency_id=skill.competency_id,
                value=skill.reviewer_value,
                grade=skill.reviewer_grade,
                grant_id=skill.grant_id,
                grant_version=skill.grant_version,
                user_competency_version=skill.competency_version,
                grant_state=skill.grant_state,
            )
        )
    return revision_id, content


async def copy_comments(session: AsyncSession, draft_id: UUID, revision_id: UUID) -> None:
    comments = meta_data.tables["review_comments"]
    rows = (
        (
            await session.execute(
                select(comments).where(comments.c.draft_id == draft_id).order_by(comments.c.created_at, comments.c.id)
            )
        )
        .mappings()
        .all()
    )
    if len(rows) > 20:
        raise CaseValidationError("Draft comment bounds exceeded")
    for row in rows:
        body = value(row, "text", str)
        if not body.strip() or len(body) > 1000:
            raise CaseValidationError("Draft comment is invalid")
        # Positions were validated against retained B/C when this exact draft
        # version was saved. Completion/correction does not call Git again.
        await session.execute(
            insert(comments).values(
                id=uuid4(),
                created_at=row["created_at"],
                revision_id=revision_id,
                text=body,
                path=row["path"],
                line=row["line"],
                end_line=row["end_line"],
                side=row["side"],
            )
        )


async def enqueue_quality(session: AsyncSession, submission_id: UUID, revision_id: UUID, now: datetime) -> None:
    await session.execute(
        insert(meta_data.tables["review_jobs"]).values(
            id=uuid4(),
            created_at=now,
            submission_id=submission_id,
            reviewed_revision_id=revision_id,
            kind=ReviewKind.QUALITY_CHECK,
            state=JobState.WAITING,
            policy_version=POLICY_VERSION,
        )
    )
