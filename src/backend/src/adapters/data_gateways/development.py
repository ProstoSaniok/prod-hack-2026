"""Expert readiness transactions, private reads and permanent business deduplication."""

import hashlib
import json
from dataclasses import asdict, replace
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.development_portfolio import DevelopmentPortfolio
from src.adapters.data_gateways.development_requests import DevelopmentRequests
from src.adapters.data_gateways.grade_advancement import SqlGradeAdvancement
from src.adapters.data_gateways.grade_evidence import GradeCandidate, SqlGradeEvidence, gate_record
from src.adapters.data_gateways.learning_views import encode, row_mapping
from src.adapters.data_gateways.review_access import ReviewAccess
from src.adapters.data_gateways.tables import meta_data
from src.adapters.operations.sql_records import value
from src.adapters.operations.sql_work import database_now
from src.application.auth_identity.errors import ForbiddenError
from src.application.catalog.management import CaseConflictError, CaseNotFoundError
from src.application.development.capture import APPLICATION_FIELDS, ASSESSMENT_FIELDS
from src.application.development.entity import ReadinessDecision as StoredDecision, ReviewerApplicationState, SkillGrade
from src.application.development.policy import assess_expert_readiness, request_expert_readiness
from src.application.development.policy_types import (
    Grade,
    GradeGateEvidence,
    Grant,
    PolicyViolationError,
    ReadinessDecision,
    ReadinessRequest,
)
from src.application.development.workflow import ApplicationInput, AssessmentInput
from src.application.operations.idempotency import CachedResponse
from src.application.user.capability import ProductCapability
from src.application.user.entity import UserId

BASE = "/api/v1/reviewer-applications"


def application_metadata(record: dict[str, object]) -> dict[str, object]:
    status = {
        ReviewerApplicationState.PENDING: "pending",
        ReviewerApplicationState.CONFIRMED: "approved",
        ReviewerApplicationState.REJECTED: "needs_development",
        ReviewerApplicationState.CANCELLED: "cancelled",
    }
    return {
        **{name: record[name] for name in APPLICATION_FIELDS if name not in {"status", "content_url"}},
        "status": status[value(record, "state", ReviewerApplicationState)],
        "content_url": f"{BASE}/{record['id']}",
    }


def assessment_metadata(record: dict[str, object]) -> dict[str, object]:
    return {
        **{name: record[name] for name in ASSESSMENT_FIELDS if name not in {"decision", "content_url"}},
        "decision": "approved" if record["decision"] == StoredDecision.CONFIRMED else "needs_development",
        "content_url": f"{BASE}/{record['application_id']}/assessments/{record['id']}",
    }


def receipt(metadata: dict[str, object], status: int) -> CachedResponse:
    return CachedResponse(status_code=status, location=value(metadata, "content_url", str), body=encode(metadata))


def stored_evidence(record: dict[str, object]) -> dict[str, object]:
    parsed = json.loads(value(record, "evidence", str))
    if not isinstance(parsed, dict):
        raise TypeError("Invalid persisted application evidence")
    return cast(dict[str, object], parsed)


def require_expert(candidate: GradeCandidate) -> None:
    right = candidate.authority
    if right.grade != Grade.EXPERT or right.grant != Grant.CONFIRMED or not right.expert_confirmed or right.value < 75:
        raise CaseNotFoundError


class DefaultDevelopmentStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.access = ReviewAccess(session)
        self.evidence = SqlGradeEvidence(session)
        self.requests = DevelopmentRequests(session)

    async def apply(self, actor: UserId, command: ApplicationInput, key: str) -> CachedResponse:
        user = await self.access.actor(actor, lock=True)
        candidate = await self.evidence.candidate(user, command.competency_id)
        application_id = uuid4()
        request = await self._eligible(candidate, application_id)
        replay = await self.requests.begin(actor, BASE, key, encode(asdict(command)))
        if replay is not None:
            return replace(replay, status_code=200)
        applications = meta_data.tables["reviewer_applications"]
        pending = (
            (
                await self.session.execute(
                    select(applications)
                    .where(
                        applications.c.user_id == actor,
                        applications.c.competency_id == command.competency_id,
                        applications.c.requested_grade == SkillGrade.EXPERT,
                        applications.c.state == ReviewerApplicationState.PENDING,
                        applications.c.deleted_at.is_(None),
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if pending is not None:
            return await self._finish(receipt(application_metadata(row_mapping(pending)), 200))
        require_current(candidate, command.expected_user_competency_version)
        record: dict[str, object] = {
            "id": application_id,
            "created_at": await database_now(self.session),
            "user_id": actor,
            "competency_id": command.competency_id,
            "requested_grade": SkillGrade.EXPERT,
            "user_competency_version": candidate.skill.version,
            "evidence": encode(gate_record(request.evidence)).decode(),
            "state": ReviewerApplicationState.PENDING,
            "version": 1,
        }
        await self.session.execute(insert(applications).values(**record))
        return await self._finish(receipt(application_metadata(record), 201))

    async def assess(self, actor: UserId, application_id: UUID, command: AssessmentInput, key: str) -> CachedResponse:
        application, candidate, expert = await self._context(actor, application_id)
        await self._authorize_assessment(actor, application, candidate, expert)
        payload = encode(asdict(command))
        fingerprint = hashlib.sha256(payload).hexdigest()
        replay = await self.requests.begin(actor, f"{BASE}/{application_id}/assessments", key, payload)
        if replay is not None:
            return replace(replay, status_code=200)
        assessments = meta_data.tables["reviewer_application_assessments"]
        existing = (
            (await self.session.execute(select(assessments).where(assessments.c.application_id == application_id)))
            .mappings()
            .one_or_none()
        )
        evidence = stored_evidence(application)
        if existing is not None:
            if existing["expert_user_id"] != actor or evidence.get("assessment_input_hash") != fingerprint:
                raise CaseConflictError("APPLICATION_COMPLETED", "Application already has a different assessment")
            return await self._finish(receipt(assessment_metadata(row_mapping(existing)), 200))
        if application["state"] != ReviewerApplicationState.PENDING:
            raise CaseConflictError("APPLICATION_COMPLETED", "Application is no longer pending")
        if application["version"] != command.expected_application_version:
            raise CaseConflictError("STALE_VERSION", "Application version changed")
        require_current(candidate, command.expected_user_competency_version)
        current = await self.evidence.load(candidate.skill.user_id, candidate.skill.competency_id)
        try:
            # Re-evaluate the canonical entry gate even for needs-development;
            # neither a request nor a client counter is durable gate evidence.
            request = request_expert_readiness(application_id, candidate.authority, current)
            assessment = assess_expert_readiness(
                ReadinessRequest(application_id=application_id, evidence=original_gate(application, request.evidence)),
                candidate.authority,
                current,
                expert.authority,
                decision=ReadinessDecision.APPROVED if command.decision == "approved" else ReadinessDecision.REJECTED,
                feedback=command.feedback,
            )
        except PolicyViolationError:
            raise CaseConflictError(
                "GRADE_REQUIREMENTS_NOT_MET", "Current readiness prerequisites are not satisfied"
            ) from None
        now = await database_now(self.session)
        if expert.grant is None:
            raise CaseNotFoundError
        record: dict[str, object] = {
            "id": uuid4(),
            "created_at": now,
            "application_id": application_id,
            "application_version": application["version"],
            "applicant_id": candidate.skill.user_id,
            "competency_id": candidate.skill.competency_id,
            "requested_grade": SkillGrade.EXPERT,
            "expert_user_id": actor,
            "expert_grant_id": expert.grant.id,
            "expert_grant_version": expert.grant.version,
            "expert_competency_version": expert.skill.version,
            "expert_value": expert.authority.value,
            "decision": StoredDecision.CONFIRMED if command.decision == "approved" else StoredDecision.REJECTED,
            "feedback": command.feedback,
            "expert_grade": SkillGrade.EXPERT,
            "expert_grant_state": expert.grant.state,
        }
        await self.session.execute(insert(assessments).values(**record))
        if assessment.current_evidence is not None:
            await SqlGradeAdvancement(self.session).replace(
                candidate, assessment.current_evidence, now, assessment=record
            )
        applications = meta_data.tables["reviewer_applications"]
        await self.session.execute(
            update(applications)
            .where(applications.c.id == application_id)
            .values(
                state=ReviewerApplicationState.CONFIRMED
                if command.decision == "approved"
                else ReviewerApplicationState.REJECTED,
                version=applications.c.version + 1,
                evidence=encode({
                    **evidence,
                    "assessment_input_hash": fingerprint,
                    "assessment_candidate_version": candidate.skill.version,
                    "assessment_gate": gate_record(request.evidence),
                }).decode(),
            )
        )
        return await self._finish(receipt(assessment_metadata(record), 201))

    async def _authorize_assessment(
        self, actor: UserId, application: dict[str, object], candidate: GradeCandidate, expert: GradeCandidate
    ) -> None:
        if actor == application["user_id"]:
            raise CaseNotFoundError
        require_expert(expert)
        # A completed approval can replay under the resulting live expert grant.
        # Other states still require the current confirmed plus entry gate. Check
        # both parties before either the request journal or permanent receipts.
        if application["state"] == ReviewerApplicationState.CONFIRMED:
            require_expert(candidate)
        else:
            await self._eligible(candidate, value(application, "id", UUID))

    async def _eligible(self, candidate: GradeCandidate, application_id: UUID) -> ReadinessRequest:
        try:
            return request_expert_readiness(
                application_id,
                candidate.authority,
                await self.evidence.load(candidate.skill.user_id, candidate.skill.competency_id),
            )
        except PolicyViolationError:
            raise CaseConflictError(
                "GRADE_REQUIREMENTS_NOT_MET", "Current expert readiness prerequisites are not satisfied"
            ) from None

    async def _context(
        self, actor: UserId, application_id: UUID
    ) -> tuple[dict[str, object], GradeCandidate, GradeCandidate]:
        table = meta_data.tables["reviewer_applications"]
        query = select(table).where(table.c.id == application_id, table.c.deleted_at.is_(None))
        initial = (await self.session.execute(query)).mappings().one_or_none()
        if initial is None:
            raise CaseNotFoundError
        applicant = UserId(value(initial, "user_id", UUID))
        # Same NO KEY UPDATE user fence as publication, in the same global order.
        users = {
            owner: await self.access.actor(owner, lock=True)
            for owner in sorted({actor, applicant}, key=lambda owner: owner.int)
        }
        candidates = {
            owner: await self.evidence.candidate(users[owner], initial["competency_id"])
            for owner in sorted(users, key=lambda owner: owner.int)
        }
        if actor != applicant:
            require_expert(candidates[actor])
        row = (await self.session.execute(query.with_for_update())).mappings().one_or_none()
        if row is None:
            raise CaseNotFoundError
        return row_mapping(row), candidates[applicant], candidates[actor]

    async def applications(self, actor: UserId, *, limit: int, offset: int) -> bytes:
        await self.access.actor(actor, lock=True)
        table = meta_data.tables["reviewer_applications"]
        rows = (
            []
            if offset > 2**63 - 1
            else (
                await self.session.execute(
                    select(table)
                    .where(table.c.user_id == actor, table.c.deleted_at.is_(None))
                    .order_by(table.c.created_at.desc(), table.c.id)
                    .limit(limit + 1)
                    .offset(offset)
                )
            )
            .mappings()
            .all()
        )
        return encode({
            "items": [await self._metadata(row_mapping(row)) for row in rows[:limit]],
            "limit": limit,
            "offset": offset,
            "next_offset": offset + limit if len(rows) > limit else None,
        })

    async def _metadata(self, record: dict[str, object]) -> dict[str, object]:
        definitions = meta_data.tables["competencies"]
        name = await self.session.scalar(select(definitions.c.name).where(definitions.c.id == record["competency_id"]))
        return {**application_metadata(record), "competency_name": name or "Навык"}

    async def inbox(self, actor: UserId, *, limit: int, offset: int) -> bytes:
        user = await self.access.actor(actor)
        skills = await self.evidence.expert_skills(user)
        if not skills:
            raise ForbiddenError
        table, users, capabilities, definitions, competencies = (
            meta_data.tables[name]
            for name in ("reviewer_applications", "users", "user_capabilities", "competencies", "user_competencies")
        )
        participant = (
            select(capabilities.c.id)
            .where(
                capabilities.c.user_id == table.c.user_id,
                capabilities.c.capability == ProductCapability.PARTICIPANT,
                capabilities.c.deleted_at.is_(None),
                capabilities.c.revoked_at.is_(None),
            )
            .exists()
        )
        query = (
            select(table, definitions.c.name.label("competency_name"))
            .join(users, users.c.id == table.c.user_id)
            .join(definitions, definitions.c.id == table.c.competency_id)
            .join(
                competencies,
                (competencies.c.user_id == table.c.user_id) & (competencies.c.competency_id == table.c.competency_id),
            )
            .where(
                table.c.state == ReviewerApplicationState.PENDING,
                table.c.requested_grade == SkillGrade.EXPERT,
                table.c.deleted_at.is_(None),
                table.c.user_id != actor,
                table.c.competency_id.in_(skills),
                users.c.deleted_at.is_(None),
                definitions.c.deleted_at.is_(None),
                competencies.c.deleted_at.is_(None),
                participant,
            )
            .order_by(table.c.created_at, table.c.id)
            .limit(limit + 1)
            .offset(offset)
        )
        rows = [] if offset > 2**63 - 1 else (await self.session.execute(query)).mappings().all()
        return encode({
            "items": [
                application_metadata(row_mapping(row)) | {"competency_name": row["competency_name"]}
                for row in rows[:limit]
            ],
            "limit": limit,
            "offset": offset,
            "next_offset": offset + limit if len(rows) > limit else None,
        })

    async def application(self, actor: UserId, application_id: UUID) -> bytes:
        record, candidate, _ = await self._context(actor, application_id)
        table = meta_data.tables["reviewer_application_assessments"]
        rows = (await self.session.execute(select(table).where(table.c.application_id == application_id))).mappings()
        evidence = stored_evidence(record)
        evidence.pop("assessment_input_hash", None)
        can_assess = actor != record["user_id"] and record["state"] == ReviewerApplicationState.PENDING
        if can_assess:
            try:
                await self._eligible(candidate, application_id)
            except CaseConflictError:
                can_assess = False
        return encode({
            **await self._metadata(record),
            "evidence": evidence,
            "current_user_competency_version": candidate.skill.version,
            "assessments": [assessment_metadata(row_mapping(row)) for row in rows],
            "can_assess": can_assess,
            "evidence_summary": await DevelopmentPortfolio(self.session, record, evidence).summary(),
        })

    async def evidence_review(self, actor: UserId, application_id: UUID, review_id: UUID) -> bytes:
        record, _, _ = await self._context(actor, application_id)
        return await DevelopmentPortfolio(self.session, record, stored_evidence(record)).review(review_id)

    async def assessment(self, actor: UserId, application_id: UUID, assessment_id: UUID) -> bytes:
        await self._context(actor, application_id)
        table = meta_data.tables["reviewer_application_assessments"]
        row = (
            (
                await self.session.execute(
                    select(table).where(
                        table.c.id == assessment_id,
                        table.c.application_id == application_id,
                        table.c.deleted_at.is_(None),
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise CaseNotFoundError
        return encode({**assessment_metadata(row_mapping(row)), "feedback": row["feedback"]})

    async def _finish(self, response: CachedResponse) -> CachedResponse:
        await self.requests.complete(response)
        await self.session.commit()
        return response


def require_current(candidate: GradeCandidate, version: int) -> None:
    if candidate.skill.version != version:
        raise CaseConflictError("STALE_VERSION", "Candidate competency version changed")


def original_gate(application: dict[str, object], current: GradeGateEvidence) -> GradeGateEvidence:
    # Identity/policy provenance is immutable from creation; current evidence is
    # separately rechecked by assess_expert_readiness and captured on decision.
    evidence = stored_evidence(application)
    if evidence.get("user_id") != str(current.user_id) or evidence.get("skill_id") != str(current.skill_id):
        raise PolicyViolationError("Application evidence identity is inconsistent")
    if evidence.get("target_grade") != Grade.EXPERT or evidence.get("policy_version") != current.policy_version:
        raise PolicyViolationError("Application evidence policy is unsupported")
    return GradeGateEvidence(
        user_id=current.user_id,
        skill_id=current.skill_id,
        target_grade=Grade.EXPERT,
        value=Decimal(value(evidence, "value", str)),
        successful_case_ids=evidence_ids(evidence, "successful_case_ids"),
        good_review_ids=evidence_ids(evidence, "good_review_ids"),
        passed_probation_ids=evidence_ids(evidence, "passed_probation_ids"),
        policy_version=value(evidence, "policy_version", str),
    )


def evidence_ids(evidence: dict[str, object], field: str) -> frozenset[UUID]:
    items = evidence[field]
    if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
        raise TypeError("Invalid persisted gate identities")
    return frozenset(UUID(item) for item in items)
