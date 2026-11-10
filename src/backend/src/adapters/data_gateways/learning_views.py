import json
from collections.abc import Mapping
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.priority_view import submission_priority
from src.adapters.data_gateways.tables import meta_data
from src.application.catalog.entity import Case, CaseId, CaseStatus
from src.application.catalog.management import CaseNotFoundError
from src.application.development.entity import SkillGrade, SkillGrantState
from src.application.review.entity import ReviewState
from src.application.user.capability import ProductCapability
from src.application.user.entity import UserId


def encode(value: object) -> bytes:
    return json.dumps(value, default=str, ensure_ascii=False, separators=(",", ":")).encode()


def row_mapping(row: RowMapping) -> dict[str, object]:
    result: dict[str, object] = {}
    for key in row:
        if not isinstance(key, str):
            raise TypeError("Expected named persisted learning fields")
        result[key] = row[key]
    return result


def case_body(case: Case, skills: list[Mapping[str, object]], *, content: Mapping[str, object] | None = None) -> bytes:
    maximum = max((Decimal(str(skill["required_value"])) for skill in skills), default=Decimal(0))
    source = content if content is not None else vars(case)
    fields = {
        "id": str(case.id),
        "version": case.version,
        "title": source["title"],
        "description": source["description"],
        "expected_result": source["expected_result"],
        "difficulty": "expert" if maximum >= 75 else "review_plus" if maximum >= 50 else "review_minus",
        "status": case.status,
        "frozen": case.frozen_at is not None,
        "created_at": case.created_at.isoformat(),
        "updated_at": (case.updated_at or case.created_at).isoformat(),
    }
    if content is not None:
        fields["content_version"] = content["content_version"]
    numeric = b",".join(
        b'{"competency_id":'
        + encode(skill["competency_id"])
        + b',"required_value":'
        + str(skill["required_value"]).encode()
        + b',"weight":'
        + str(skill["weight"]).encode()
        + b"}"
        for skill in skills
    )
    return encode(fields)[:-1] + b',"competencies":[' + numeric + b"]}"


class LearningViews:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def project(self, actor: UserId, project_id: UUID) -> Mapping[str, object]:
        projects, workspaces = meta_data.tables["projects"], meta_data.tables["git_workspaces"]
        submissions = meta_data.tables["submissions"]
        reviews = meta_data.tables["review_results"]
        row = (
            (
                await self.session.execute(
                    select(
                        projects,
                        workspaces.c.head_sha,
                        select(submissions.c.id)
                        .where(submissions.c.project_id == projects.c.id, submissions.c.deleted_at.is_(None))
                        .scalar_subquery()
                        .label("submission_id"),
                        select(reviews.c.id)
                        .join(submissions, submissions.c.effective_review_id == reviews.c.id)
                        .where(
                            submissions.c.project_id == projects.c.id,
                            submissions.c.deleted_at.is_(None),
                            reviews.c.state == ReviewState.PUBLISHED,
                            reviews.c.deleted_at.is_(None),
                        )
                        .scalar_subquery()
                        .label("effective_review_id"),
                    )
                    .join(
                        workspaces,
                        workspaces.c.repository_id == projects.c.repository_id,
                    )
                    .where(
                        projects.c.id == project_id, projects.c.participant_id == actor, projects.c.deleted_at.is_(None)
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise CaseNotFoundError
        return {
            "id": row["id"],
            "case_id": row["case_id"],
            "case_version": row["case_version"],
            "owner_id": row["participant_id"],
            "status": "completed"
            if row["effective_review_id"] is not None
            else "active"
            if row["state"] == "active"
            else "submitted",
            "started_at": row["created_at"],
            "updated_at": row["updated_at"],
            "head_commit_sha": row["head_sha"],
            "working_branch": "work",
            "repository": None,
            "upstream_repository": None,
            "submission_id": row["submission_id"],
        }

    async def submission_record(self, actor: UserId, submission_id: UUID) -> RowMapping:
        reviews, revisions = meta_data.tables["review_results"], meta_data.tables["review_revisions"]
        submissions, projects, intents, results, retained, capabilities, users = (
            meta_data.tables[name]
            for name in (
                "submissions",
                "projects",
                "submission_intents",
                "submission_results",
                "retained_references",
                "user_capabilities",
                "users",
            )
        )
        participant = (
            select(capabilities.c.id)
            .join(users, users.c.id == capabilities.c.user_id)
            .where(
                capabilities.c.user_id == actor,
                capabilities.c.capability == ProductCapability.PARTICIPANT,
                capabilities.c.deleted_at.is_(None),
                capabilities.c.revoked_at.is_(None),
                users.c.deleted_at.is_(None),
            )
            .exists()
        )
        row = (
            (
                await self.session.execute(
                    select(
                        submissions,
                        projects.c.case_id,
                        intents.c.summary,
                        intents.c.base_commit_sha,
                        results.c.commit_sha,
                        results.c.head_ref,
                        results.c.external_repository_id,
                        results.c.external_pr_id,
                        results.c.pr_number,
                        retained.c.external_id.label("base_ref"),
                        revisions.c.verdict.label("published_verdict"),
                    )
                    .select_from(submissions)
                    .join(projects, projects.c.id == submissions.c.project_id)
                    .join(intents, intents.c.operation_id == submissions.c.operation_id)
                    .join(results, results.c.operation_id == submissions.c.operation_id)
                    .join(retained, retained.c.id == submissions.c.base_retained_reference_id)
                    .outerjoin(
                        reviews,
                        and_(
                            reviews.c.id == submissions.c.effective_review_id,
                            reviews.c.submission_id == submissions.c.id,
                            reviews.c.state == ReviewState.PUBLISHED,
                            reviews.c.deleted_at.is_(None),
                        ),
                    )
                    .outerjoin(revisions, revisions.c.id == reviews.c.published_revision_id)
                    .where(
                        submissions.c.id == submission_id,
                        submissions.c.author_id == actor,
                        projects.c.participant_id == actor,
                        projects.c.deleted_at.is_(None),
                        submissions.c.deleted_at.is_(None),
                        participant,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise CaseNotFoundError
        return row

    async def submission(self, actor: UserId, submission_id: UUID, *, include_priority: bool = False) -> bytes:
        row = await self.submission_record(actor, submission_id)
        skills = meta_data.tables["submission_competencies"]
        snapshots = (
            (
                await self.session.execute(
                    select(skills).where(skills.c.submission_id == submission_id).order_by(skills.c.competency_id)
                )
            )
            .mappings()
            .all()
        )
        fields: dict[str, object] = {
            "id": row["id"],
            "project_id": row["project_id"],
            "case_id": row["case_id"],
            "case_version": row["case_version"],
            "author_id": row["author_id"],
            "status": row["published_verdict"] or "submitted",
            "submitted_at": row["created_at"],
            "effective_review_id": row["effective_review_id"] if row["published_verdict"] is not None else None,
            "base_commit_sha": row["base_commit_sha"],
            "commit_sha": row["commit_sha"],
            "base_retained_ref": row["base_ref"],
            "retained_ref": row["head_ref"],
            "pull_request_id": row["external_pr_id"],
            "pull_request_number": row["pr_number"],
            "review_repository_id": row["external_repository_id"],
            "pull_request_url": None,
            "repository": None,
        }
        if include_priority:
            fields["priority"] = await submission_priority(self.session, actor, submission_id)
        if row["summary"] is not None:
            fields["summary"] = row["summary"]
        competencies = [
            encode({"competency_id": skill["competency_id"]})[:-1]
            + b',"required_value":'
            + _number(skill["required_value"])
            + b',"weight":'
            + _number(skill["weight"])
            + b"}"
            for skill in snapshots
        ]
        authors = [_author_skill(skill) for skill in snapshots]
        return (
            encode(fields)[:-1]
            + b',"competencies":['
            + b",".join(competencies)
            + b'],"author_skills":['
            + b",".join(authors)
            + b"]}"
        )

    async def reference(self, case_id: UUID) -> Mapping[str, object]:
        references, workspaces, retained = (
            meta_data.tables[name] for name in ("reference_solutions", "git_workspaces", "retained_references")
        )
        row = (
            (
                await self.session.execute(
                    select(references, workspaces.c.head_sha, retained.c.commit_id)
                    .join(
                        workspaces,
                        workspaces.c.repository_id == references.c.repository_id,
                    )
                    .outerjoin(retained, retained.c.id == references.c.ready_reference_id)
                    .where(
                        references.c.case_id == case_id,
                        references.c.deleted_at.is_(None),
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise CaseNotFoundError
        return {
            "id": row["id"],
            "case_id": row["case_id"],
            "version": row["version"],
            "repository": None,
            "head_commit_sha": row["head_sha"],
            "ready": row["ready_at"] is not None,
            "ready_commit_sha": row["commit_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    async def published(self, case_id: CaseId) -> bytes:
        cases, contents, skills = (
            meta_data.tables[name] for name in ("cases", "case_content_versions", "case_content_competencies")
        )
        case = await self.session.scalar(
            select(Case).where(
                cases.c.id == case_id, cases.c.deleted_at.is_(None), cases.c.status == CaseStatus.PUBLISHED
            )
        )
        if case is None or case.current_published_content_id is None:
            raise CaseNotFoundError
        content = (
            (await self.session.execute(select(contents).where(contents.c.id == case.current_published_content_id)))
            .mappings()
            .one()
        )
        rows = (
            (
                await self.session.execute(
                    select(skills).where(skills.c.content_version_id == content["id"]).order_by(skills.c.competency_id)
                )
            )
            .mappings()
            .all()
        )
        return case_body(case, [row_mapping(row) for row in rows], content=row_mapping(content))


def _number(value: object) -> bytes:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise TypeError("Expected persisted finite decimal")
    return str(value).encode("ascii")


def _author_skill(skill: RowMapping) -> bytes:
    # Only immutable submission facts; never derive the grade from value or join
    # current grants. Arbitrary evidence text/seeds/confirming identities stay SQL-only.
    matching = skill["author_grant_grade"] == skill["author_grade"]
    confirmed = matching and skill["author_grant_state"] == SkillGrantState.CONFIRMED
    supervised = (
        matching
        and skill["author_grant_state"] == SkillGrantState.PROVISIONAL
        and skill["author_grade"] != SkillGrade.EXPERT
    )
    fields = {
        "competency_id": skill["competency_id"],
        "grade": skill["author_grade"],
        "grant": "confirmed" if confirmed else "supervised" if supervised else "none",
        "expert_confirmed": bool(confirmed and skill["author_grade"] == SkillGrade.EXPERT),
        "evidence_ids": [
            skill[key] for key in ("author_grant_id", "author_readiness_assessment_id") if skill[key] is not None
        ],
        "snapshotted_at": skill["author_snapshotted_at"],
    }
    return encode(fields)[:-1] + b',"value":' + _number(skill["author_value"]) + b"}"
