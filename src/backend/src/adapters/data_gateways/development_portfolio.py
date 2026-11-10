"""Application-scoped published evidence; never draft or private quality content."""

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.learning_views import encode
from src.adapters.data_gateways.review_results import bounded
from src.adapters.data_gateways.tables import meta_data
from src.application.catalog.management import CaseNotFoundError
from src.application.review.entity import QualityDecision, ReviewState


def identities(evidence: Mapping[str, object], name: str, maximum: int) -> tuple[UUID, ...]:
    values = evidence.get(name)
    if not isinstance(values, list) or len(values) > maximum or any(not isinstance(item, str) for item in values):
        raise CaseNotFoundError
    try:
        return tuple(UUID(item) for item in values)
    except ValueError:
        raise CaseNotFoundError from None


class DevelopmentPortfolio:
    def __init__(
        self, session: AsyncSession, application: Mapping[str, object], evidence: Mapping[str, object]
    ) -> None:
        self.session, self.application, self.evidence = session, application, evidence
        self.case_ids = identities(evidence, "successful_case_ids", 2)
        self.review_ids = identities(evidence, "good_review_ids", 6)
        self.probation_ids = identities(evidence, "passed_probation_ids", 2)

    def review_query(self) -> Select[tuple[object, ...]]:
        successes, probations, reviews, revisions, assessments, submissions, contents = (
            meta_data.tables[name]
            for name in (
                "probation_successes",
                "probations",
                "review_results",
                "review_revisions",
                "quality_assessments",
                "submissions",
                "case_content_versions",
            )
        )
        return (
            select(
                reviews.c.id.label("review_id"),
                revisions.c.id.label("revision_id"),
                reviews.c.published_at,
                contents.c.title.label("case_title"),
                contents.c.description,
                contents.c.expected_result,
                revisions.c.score,
                revisions.c.verdict,
                revisions.c.strengths,
                revisions.c.problems,
                revisions.c.next_steps,
                assessments.c.quality_score,
                assessments.c.created_at.label("confirmed_at"),
                probations.c.id.label("probation_id"),
            )
            .select_from(successes)
            .join(probations, probations.c.id == successes.c.probation_id)
            .join(reviews, reviews.c.id == successes.c.review_id)
            .join(revisions, revisions.c.id == successes.c.review_revision_id)
            .join(assessments, assessments.c.id == successes.c.quality_assessment_id)
            .join(submissions, submissions.c.id == reviews.c.submission_id)
            .join(contents, contents.c.id == submissions.c.content_version_id)
            .where(
                successes.c.reviewer_id == self.application["user_id"],
                successes.c.competency_id == self.application["competency_id"],
                successes.c.deleted_at.is_(None),
                probations.c.id.in_(self.probation_ids),
                probations.c.user_id == self.application["user_id"],
                probations.c.competency_id == self.application["competency_id"],
                probations.c.deleted_at.is_(None),
                probations.c.completed_at.is_not(None),
                reviews.c.id.in_(self.review_ids),
                reviews.c.reviewer_id == self.application["user_id"],
                reviews.c.state == ReviewState.PUBLISHED,
                reviews.c.published_revision_id == revisions.c.id,
                reviews.c.deleted_at.is_(None),
                revisions.c.review_id == reviews.c.id,
                revisions.c.submission_id == submissions.c.id,
                revisions.c.deleted_at.is_(None),
                assessments.c.revision_id == revisions.c.id,
                assessments.c.decision == QualityDecision.CONFIRMED,
                assessments.c.quality_score >= 70,
                assessments.c.deleted_at.is_(None),
                submissions.c.deleted_at.is_(None),
            )
            .order_by(reviews.c.published_at, reviews.c.id)
        )

    async def summary(self) -> dict[str, object]:
        contributions, reviews, submissions, contents, probations = (
            meta_data.tables[name]
            for name in (
                "progression_contributions",
                "review_results",
                "submissions",
                "case_content_versions",
                "probations",
            )
        )
        cases = (
            (
                await self.session.execute(
                    select(contributions.c.case_id, contributions.c.score, contents.c.title)
                    .join(reviews, reviews.c.id == contributions.c.review_id)
                    .join(submissions, submissions.c.id == reviews.c.submission_id)
                    .join(contents, contents.c.id == submissions.c.content_version_id)
                    .where(
                        contributions.c.participant_id == self.application["user_id"],
                        contributions.c.competency_id == self.application["competency_id"],
                        contributions.c.case_id.in_(self.case_ids),
                        contributions.c.effective.is_(True),
                        contributions.c.deleted_at.is_(None),
                        contributions.c.score >= 70,
                        reviews.c.state == ReviewState.PUBLISHED,
                        reviews.c.deleted_at.is_(None),
                        reviews.c.published_revision_id == contributions.c.review_revision_id,
                        submissions.c.author_id == self.application["user_id"],
                        submissions.c.deleted_at.is_(None),
                    )
                    .order_by(contributions.c.case_id)
                    .limit(2)
                )
            )
            .mappings()
            .all()
        )
        works = (await self.session.execute(self.review_query().limit(6))).mappings().all()
        periods = (
            await self.session.execute(
                select(probations)
                .where(
                    probations.c.id.in_(self.probation_ids),
                    probations.c.user_id == self.application["user_id"],
                    probations.c.competency_id == self.application["competency_id"],
                    probations.c.deleted_at.is_(None),
                    probations.c.completed_at.is_not(None),
                )
                .order_by(probations.c.created_at, probations.c.id)
                .limit(2)
            )
        ).mappings()
        return {
            "value": self.evidence["value"],
            "policy_version": self.evidence["policy_version"],
            "successful_cases": [dict(row) for row in cases],
            "probations": [
                {
                    "id": row["id"],
                    "grade": row["grade"],
                    "completed_at": row["completed_at"],
                    "confirmed_reviews": len({
                        work["review_id"] for work in works if work["probation_id"] == row["id"]
                    }),
                }
                for row in periods
            ],
            "reviews": [
                {
                    name: row[name]
                    for name in ("review_id", "revision_id", "case_title", "published_at", "quality_score")
                }
                | {
                    "content_url": f"/api/v1/reviewer-applications/{self.application['id']}"
                    f"/evidence/reviews/{row['review_id']}"
                }
                for row in works
            ],
        }

    async def review(self, review_id: UUID) -> bytes:
        if review_id not in self.review_ids:
            raise CaseNotFoundError
        reviews = meta_data.tables["review_results"]
        row = (
            (await self.session.execute(self.review_query().where(reviews.c.id == review_id))).mappings().one_or_none()
        )
        if row is None:
            raise CaseNotFoundError
        comments = meta_data.tables["review_comments"]
        items = (
            (
                await self.session.execute(
                    select(comments)
                    .where(
                        comments.c.revision_id == row["revision_id"],
                        comments.c.deleted_at.is_(None),
                    )
                    .order_by(comments.c.created_at, comments.c.id)
                    .limit(21)
                )
            )
            .mappings()
            .all()
        )
        if len(items) > 20:
            raise CaseNotFoundError
        return bounded(
            encode({
                "review_id": row["review_id"],
                "revision_id": row["revision_id"],
                "published_at": row["published_at"],
                "case": {
                    "title": row["case_title"],
                    "description": row["description"],
                    "expected_result": row["expected_result"],
                },
                "score": row["score"],
                "verdict": row["verdict"],
                "feedback": {name: row[name] for name in ("strengths", "problems", "next_steps")},
                "comments": [
                    {
                        "body": item["text"],
                        "path": item["path"],
                        "line": item["line"],
                        "side": item["side"],
                        **({"end_line": item["end_line"]} if item["end_line"] is not None else {}),
                    }
                    for item in items
                ],
                "quality": {"score": row["quality_score"], "confirmed_at": row["confirmed_at"]},
            })
        )
