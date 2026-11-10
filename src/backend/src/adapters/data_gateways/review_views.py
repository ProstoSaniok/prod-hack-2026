from collections.abc import Mapping
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.learning_views import encode
from src.adapters.data_gateways.tables import meta_data
from src.adapters.operations.sql_records import value
from src.application.development.entity import SkillGrantState
from src.application.review.entity import AssignmentState, ReviewKind
from src.application.review.workspace import DraftContent


def number(value: object) -> bytes:
    if value is None:
        return b"null"
    if not isinstance(value, Decimal) or not value.is_finite():
        raise TypeError("Expected finite persisted review number")
    return str(value).encode("ascii")


def draft_input(content: DraftContent) -> bytes:
    fields = {
        "expected_version": content.expected_version,
        "verdict": content.verdict,
        "feedback": {"strengths": content.strengths, "problems": content.problems, "next_steps": content.next_steps},
        "comments": [
            {
                "body": comment.body,
                "path": comment.path,
                "line": comment.line,
                "side": comment.side,
                **({"end_line": comment.end_line} if comment.end_line is not None else {}),
            }
            for comment in content.comments
        ],
    }
    return encode(fields)[:-1] + b',"score":' + number(content.score) + b"}"


class ReviewViews:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def task(self, assignment: Mapping[str, object], *, can_continue: bool = True) -> bytes:
        jobs, drafts, skills = (
            meta_data.tables[name] for name in ("review_jobs", "review_drafts", "assignment_competencies")
        )
        draft_id = await self.session.scalar(select(drafts.c.id).where(drafts.c.assignment_id == assignment["id"]))
        if draft_id is None:
            raise ValueError("Assignment has no reserved draft")
        rows = (
            (
                await self.session.execute(
                    select(skills).where(skills.c.assignment_id == assignment["id"]).order_by(skills.c.competency_id)
                )
            )
            .mappings()
            .all()
        )
        snapshots = [
            encode({
                "competency_id": row["competency_id"],
                "grade": row["grade"],
                "grant": "supervised" if row["grant_state"] == SkillGrantState.PROVISIONAL else "confirmed",
                "expert_confirmed": row["grade"] == "expert",
                "evidence_ids": [row["grant_id"]],
                "snapshotted_at": assignment["created_at"],
            })[:-1]
            + b',"value":'
            + number(row["value"])
            + b"}"
            for row in rows
        ]
        status = assignment["state"]
        if status == AssignmentState.ACTIVE:
            status = "assigned"
        review_id, revision_id = None, None
        if assignment["kind"] == ReviewKind.QUALITY_CHECK:
            revisions = meta_data.tables["review_revisions"]
            target = (
                (
                    await self.session.execute(
                        select(revisions.c.review_id, revisions.c.id)
                        .join(jobs, jobs.c.reviewed_revision_id == revisions.c.id)
                        .where(jobs.c.id == assignment["job_id"])
                    )
                )
                .mappings()
                .one()
            )
            review_id, revision_id = target["review_id"], target["id"]
        return (
            encode({
                "id": assignment["id"],
                "kind": assignment["kind"],
                "submission_id": assignment["submission_id"],
                "reviewer_id": assignment["reviewer_id"],
                "review_id": review_id,
                "revision_id": revision_id,
                "recheck_id": None,
                "draft_id": draft_id,
                "status": status,
                "can_continue": can_continue,
                "assigned_at": assignment["created_at"],
                "lease_expires_at": assignment["lease_expires_at"],
                "supervised_competency_ids": [row["competency_id"] for row in rows if row["probation_id"] is not None],
            })[:-1]
            + b',"reviewer_skills":['
            + b",".join(snapshots)
            + b"]}"
        )

    async def draft(self, assignment: Mapping[str, object]) -> bytes:
        drafts, comments = meta_data.tables["review_drafts"], meta_data.tables["review_comments"]
        # Whole-draft GETs must not combine one version's fields with a concurrent
        # replacement's comments under PostgreSQL READ COMMITTED.
        row = (
            (
                await self.session.execute(
                    select(drafts).where(drafts.c.assignment_id == assignment["id"]).with_for_update(read=True)
                )
            )
            .mappings()
            .one()
        )
        rows = (
            await self.session.execute(
                select(comments).where(comments.c.draft_id == row["id"]).order_by(comments.c.created_at, comments.c.id)
            )
        ).mappings()
        fields = {
            "id": row["id"],
            "assignment_id": assignment["id"],
            "submission_id": assignment["submission_id"],
            "reviewer_id": assignment["reviewer_id"],
            "version": row["version"],
            "verdict": row["verdict"],
            "feedback": {key: row[key] for key in ("strengths", "problems", "next_steps")},
            "comments": [
                {
                    "path": item["path"],
                    "line": item["line"],
                    "side": item["side"],
                    "body": item["text"],
                    **({"end_line": item["end_line"]} if item["end_line"] is not None else {}),
                }
                for item in rows
            ],
            "saved_at": row["updated_at"] or row["created_at"],
        }
        return encode(fields)[:-1] + b',"score":' + number(row["score"]) + b"}"

    async def workspace(self, assignment: Mapping[str, object], submission: Mapping[str, object]) -> bytes:
        contents, requirements, results, intents = (
            meta_data.tables[name]
            for name in ("case_content_versions", "submission_competencies", "submission_results", "submission_intents")
        )
        content = (
            (await self.session.execute(select(contents).where(contents.c.id == submission["content_version_id"])))
            .mappings()
            .one()
        )
        git = (
            (
                await self.session.execute(
                    select(results, intents.c.base_commit_sha)
                    .join(intents, intents.c.operation_id == results.c.operation_id)
                    .where(results.c.operation_id == submission["operation_id"])
                )
            )
            .mappings()
            .one()
        )
        rows = (
            await self.session.execute(
                select(requirements)
                .where(requirements.c.submission_id == submission["id"])
                .order_by(requirements.c.competency_id)
            )
        ).mappings()
        skills = [
            encode({"competency_id": row["competency_id"], "author_grade": row["author_grade"]})[:-1]
            + b',"required_value":'
            + number(row["required_value"])
            + b',"weight":'
            + number(row["weight"])
            + b',"author_value":'
            + number(row["author_value"])
            + b"}"
            for row in rows
        ]
        root = f"/api/v1/review-assignments/{value(assignment, 'id', UUID)}"
        fields = {
            "submission_id": submission["id"],
            "case_id": content["case_id"],
            "case_version": submission["case_version"],
            "case": {key: content[key] for key in ("title", "description", "expected_result")},
            "commit_sha": git["commit_sha"],
            "base_commit_sha": git["base_commit_sha"],
            "pull_request_url": None,
            "snapshot": None,
            "files_url": root + "/files",
            "diff_url": root + "/diff",
        }
        return (
            encode(fields)[:-1]
            + b',"assignment":'
            + await self.task(assignment)
            + b',"draft":'
            + await self.draft(assignment)
            + b',"competencies":['
            + b",".join(skills)
            + b"]}"
        )
