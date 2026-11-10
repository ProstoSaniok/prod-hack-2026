from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.adapters.data_gateways.tables import meta_data
from src.application.development.entity import (
    ReviewerApplicationAssessmentId,
    SkillGrade,
    SkillGrant,
    SkillGrantEvidenceKind,
    SkillGrantId,
    SkillGrantState,
    UserCompetency,
    has_expert_rights,
)
from src.application.user.entity import User, UserId
from tests.persistence.helpers import (
    add_revision,
    assess_revision,
    insert,
    seed_grant,
    seed_review,
    seed_subject,
)
from tests.support.development_constraints import contribution_values, readiness_values, seed_application


def test_mapped_grant_revocation_does_not_rewrite_revision_snapshot(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        grant_id = seed_grant(connection, subject.reviewer, subject.competency, expert=True)
        _, revision = seed_review(connection, subject)
        snapshot = insert(
            connection,
            "reviewer_snapshots",
            revision_id=revision,
            competency_id=subject.competency,
            value=90,
            grade="EXPERT",
            grant_id=grant_id,
            grant_version=1,
            user_competency_version=1,
            grant_state="CONFIRMED",
        )
    with Session(core_engine) as session:
        grant = session.get(SkillGrant, grant_id)
        user = session.get(User, subject.reviewer)
        competency = session.scalar(
            select(UserCompetency).where(meta_data.tables["user_competencies"].c.user_id == subject.reviewer)
        )
        assert grant is not None
        assert user is not None
        assert competency is not None
        assert has_expert_rights(user, subject.competency, competency, grant)
        grant.state = SkillGrantState.REVOKED
        grant.revoked_at = datetime.now(UTC)
        grant.version += 1
        competency.contribution_sum += Decimal(1)
        session.commit()
    with Session(core_engine) as session:
        grant = session.get(SkillGrant, grant_id)
        user = session.get(User, subject.reviewer)
        competency = session.scalar(
            select(UserCompetency).where(meta_data.tables["user_competencies"].c.user_id == subject.reviewer)
        )
        assert grant is not None
        assert user is not None
        assert competency is not None
        assert not has_expert_rights(user, subject.competency, competency, grant)
        snapshots = meta_data.tables["reviewer_snapshots"]
        historical = session.execute(select(snapshots).where(snapshots.c.id == snapshot)).mappings().one()
        assert historical["grant_state"] == SkillGrantState.CONFIRMED
        assert historical["grant_version"] == 1
        assert historical["user_competency_version"] == 1
        assert competency.version == 2


def test_second_live_grant_is_rejected(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        seed_grant(connection, subject.reviewer, subject.competency)
        with pytest.raises(IntegrityError, match="uq_live_skill_grant"):
            insert(
                connection,
                "skill_grants",
                user_id=subject.reviewer,
                competency_id=subject.competency,
                user_competency_version=1,
                grade="REVIEW_MINUS",
                state="PROVISIONAL",
                evidence_kind="PROGRESSION",
                evidence="Duplicated rights",
                readiness_decision="CONFIRMED",
                confirmed_by_grade="EXPERT",
            )


def test_numeric_expert_without_confirmation_evidence_is_rejected(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        insert(
            connection,
            "user_competencies",
            user_id=subject.reviewer,
            competency_id=subject.competency,
            contribution_sum=90,
            displayed_value=90,
            grade="EXPERT",
        )
        with pytest.raises(IntegrityError, match="ck_grant_expert_evidence"):
            insert(
                connection,
                "skill_grants",
                user_id=subject.reviewer,
                competency_id=subject.competency,
                user_competency_version=1,
                grade="EXPERT",
                state="CONFIRMED",
                evidence_kind="PROGRESSION",
                evidence="Only numeric points",
                confirmed_at=datetime.now(UTC),
                readiness_decision="CONFIRMED",
                confirmed_by_grade="EXPERT",
            )


def test_readiness_assessor_cannot_be_unrelated_skill_expert(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        seed_grant(connection, subject.reviewer, subject.competency)
        other = insert(connection, "competencies", name="Unrelated", description="Unrelated")
        grant = seed_grant(connection, subject.expert, other, expert=True)
        application = seed_application(connection, subject)
        with pytest.raises(IntegrityError, match="fk_readiness_expert_skill"):
            insert(connection, "reviewer_application_assessments", **readiness_values(subject, application, grant))


def test_readiness_assessment_is_independent(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        grant = seed_grant(connection, subject.reviewer, subject.competency, expert=True)
        application = seed_application(connection, subject)
        values = {**readiness_values(subject, application, grant), "expert_user_id": subject.reviewer}
        with pytest.raises(IntegrityError, match="ck_readiness_independent"):
            insert(connection, "reviewer_application_assessments", **values)


def test_rejected_readiness_cannot_be_used_for_confirmed_expert_grant(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        old_grant = seed_grant(connection, subject.reviewer, subject.competency)
        expert_grant = seed_grant(connection, subject.expert, subject.competency, expert=True)
        application = seed_application(connection, subject)
        assessment = insert(
            connection,
            "reviewer_application_assessments",
            **{**readiness_values(subject, application, expert_grant), "decision": "REJECTED"},
        )
        grants = meta_data.tables["skill_grants"]
        connection.execute(
            grants.update().where(grants.c.id == old_grant).values(state="REVOKED", revoked_at=datetime.now(UTC))
        )
        with pytest.raises(IntegrityError, match="fk_grant_confirmed_readiness"):
            insert(
                connection,
                "skill_grants",
                user_id=subject.reviewer,
                competency_id=subject.competency,
                user_competency_version=1,
                grade="EXPERT",
                state="CONFIRMED",
                evidence_kind="INDEPENDENT_READINESS",
                evidence="Rejected evidence cannot authorize",
                readiness_assessment_id=assessment,
                confirmed_by_user_id=subject.expert,
                confirmed_by_grant_id=expert_grant,
                confirmed_by_grant_version=1,
                confirmed_at=datetime.now(UTC),
                readiness_decision="CONFIRMED",
                confirmed_by_grade="EXPERT",
            )


def test_probation_counts_underlying_review_once_across_revisions(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        reviewer_grant = seed_grant(connection, subject.reviewer, subject.competency)
        expert_grant = seed_grant(connection, subject.expert, subject.competency, expert=True)
        probation = insert(
            connection,
            "probations",
            user_id=subject.reviewer,
            competency_id=subject.competency,
            grade="REVIEW_MINUS",
            skill_grant_id=reviewer_grant,
            epoch=1,
        )
        review, revision = seed_review(connection, subject)
        assessment = assess_revision(connection, subject, revision, expert_grant)
        values = {
            "probation_id": probation,
            "reviewer_id": subject.reviewer,
            "competency_id": subject.competency,
            "review_id": review,
            "review_revision_id": revision,
            "quality_assessment_id": assessment,
            "expert_user_id": subject.expert,
            "expert_grant_id": expert_grant,
            "expert_grant_version": 1,
            "quality_score": 90,
            "quality_decision": "CONFIRMED",
        }
        insert(connection, "probation_successes", **values)
        second_revision = add_revision(connection, subject, review, 2)
        second_assessment = assess_revision(connection, subject, second_revision, expert_grant)
        with pytest.raises(IntegrityError, match="uq_probation_underlying_review"):
            insert(
                connection,
                "probation_successes",
                **{**values, "review_revision_id": second_revision, "quality_assessment_id": second_assessment},
            )


def test_effective_contribution_unique_and_exact(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        grant = seed_grant(connection, subject.expert, subject.competency, expert=True)
        review, revision = seed_review(connection, subject)
        values = contribution_values(subject, review, revision, grant)
        row_id = insert(connection, "progression_contributions", **values)
        table = meta_data.tables["progression_contributions"]
        row = connection.execute(select(table).where(table.c.id == row_id)).mappings().one()
        assert row["contribution"] == values["contribution"]
        assert row["contribution_sum_after"] == values["contribution_sum_after"]
        with pytest.raises(IntegrityError, match="uq_effective_contribution"):
            insert(connection, "progression_contributions", **{**values, "source_id": uuid4()})


def test_source_key_survives_replacement_of_effective_contribution(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        grant = seed_grant(connection, subject.expert, subject.competency, expert=True)
        review, revision = seed_review(connection, subject)
        values = contribution_values(subject, review, revision, grant)
        old = insert(connection, "progression_contributions", **values)
        table = meta_data.tables["progression_contributions"]
        connection.execute(table.update().where(table.c.id == old).values(effective=False))
        insert(
            connection, "progression_contributions", **{**values, "source_id": uuid4(), "source_contribution_id": old}
        )
        with pytest.raises(IntegrityError, match="uq_contribution_source_skill"):
            insert(connection, "progression_contributions", **{**values, "effective": False})


def test_concurrent_effective_contributions_have_one_winner(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        grant = seed_grant(connection, subject.expert, subject.competency, expert=True)
        review, revision = seed_review(connection, subject)
    barrier = Barrier(2)

    def compete() -> bool:
        try:
            with core_engine.begin() as connection:
                values = contribution_values(subject, review, revision, grant)
                barrier.wait(timeout=10)
                insert(connection, "progression_contributions", **values)
        except IntegrityError as error:
            if "uq_effective_contribution" not in str(error):
                raise
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.submit(compete), executor.submit(compete)
        assert sorted([first.result(timeout=15), second.result(timeout=15)]) == [False, True]


@pytest.mark.parametrize("bad_sum", [Decimal(-1), Decimal("Infinity"), Decimal("NaN")])
def test_database_rejects_invalid_uncapped_sum(core_engine: Engine, bad_sum: Decimal) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        with pytest.raises(IntegrityError, match="ck_skill_uncapped_sum"):
            insert(
                connection,
                "user_competencies",
                user_id=subject.author,
                competency_id=subject.competency,
                contribution_sum=bad_sum,
                displayed_value=0,
                grade="PERFORMER",
            )


def test_provisional_grant_can_be_confirmed_but_not_rewritten(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        grant_id = seed_grant(connection, subject.reviewer, subject.competency)
    with Session(core_engine) as session:
        grant = session.get(SkillGrant, grant_id)
        assert grant is not None
        grant.state = SkillGrantState.CONFIRMED
        grant.confirmed_at = datetime.now(UTC)
        session.commit()
        assert grant.version == 2
        grant.evidence = "Overwritten evidence"
        with pytest.raises(ValueError, match="write-once evidence"):
            session.flush()


def test_revoked_grant_cannot_be_restored_in_place(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        grant_id = seed_grant(connection, subject.reviewer, subject.competency, expert=True)
    with Session(core_engine) as session:
        grant = session.get(SkillGrant, grant_id)
        assert grant is not None
        grant.state = SkillGrantState.REVOKED
        grant.revoked_at = datetime.now(UTC)
        session.commit()
        grant.state = SkillGrantState.CONFIRMED
        grant.revoked_at = None
        with pytest.raises(ValueError, match="cannot be restored"):
            session.flush()


@pytest.mark.parametrize("mismatch", [None, "applicant", "requested_grade", "assessor", "grant_version"])
def test_independent_readiness_grant_round_trip_and_evidence_binding(core_engine: Engine, mismatch: str | None) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        previous = seed_grant(connection, subject.reviewer, subject.competency)
        expert_grant = seed_grant(connection, subject.expert, subject.competency, expert=True)
        application = seed_application(connection, subject)
        assessment = insert(
            connection, "reviewer_application_assessments", **readiness_values(subject, application, expert_grant)
        )
        grants = meta_data.tables["skill_grants"]
        connection.execute(
            grants.update()
            .where(grants.c.id == previous)
            .values(state="REVOKED", revoked_at=datetime.now(UTC), version=2)
        )
        values: dict[str, object] = {
            "user_id": subject.reviewer,
            "competency_id": subject.competency,
            "user_competency_version": 2,
            "grade": "EXPERT",
            "state": "CONFIRMED",
            "evidence_kind": "INDEPENDENT_READINESS",
            "evidence": "Independent readiness source",
            "readiness_assessment_id": assessment,
            "confirmed_by_user_id": subject.expert,
            "confirmed_by_grant_id": expert_grant,
            "confirmed_by_grant_version": 1,
            "confirmed_at": datetime.now(UTC),
            "readiness_decision": "CONFIRMED",
            "confirmed_by_grade": "EXPERT",
        }
        if mismatch == "applicant":
            insert(
                connection,
                "user_competencies",
                user_id=subject.author,
                competency_id=subject.competency,
                contribution_sum=90,
                displayed_value=90,
                grade="EXPERT",
            )
            values["user_id"] = subject.author
        elif mismatch == "requested_grade":
            values["grade"] = "REVIEW_PLUS"
        elif mismatch == "assessor":
            other = seed_grant(connection, subject.owner, subject.competency, expert=True)
            values.update(confirmed_by_user_id=subject.owner, confirmed_by_grant_id=other)
        elif mismatch == "grant_version":
            values["confirmed_by_grant_version"] = 2
        if mismatch is not None:
            with pytest.raises(IntegrityError, match="fk_grant_confirmed_readiness"):
                insert(connection, "skill_grants", **values)
            return
        competencies = meta_data.tables["user_competencies"]
        connection.execute(
            competencies.update()
            .where(competencies.c.user_id == subject.reviewer)
            .values(grade="EXPERT", contribution_sum=90, displayed_value=90, version=2)
        )
        grant = SkillGrant(
            id=SkillGrantId(uuid4()),
            created_at=datetime.now(UTC),
            user_id=UserId(subject.reviewer),
            competency_id=subject.competency,
            user_competency_version=2,
            grade=SkillGrade.EXPERT,
            state=SkillGrantState.CONFIRMED,
            evidence_kind=SkillGrantEvidenceKind.INDEPENDENT_READINESS,
            evidence="Independent readiness source",
            readiness_assessment_id=ReviewerApplicationAssessmentId(assessment),
            confirmed_by_user_id=UserId(subject.expert),
            confirmed_by_grant_id=SkillGrantId(expert_grant),
            confirmed_by_grant_version=1,
            confirmed_at=datetime.now(UTC),
        )
        with Session(bind=connection) as session:
            session.add(grant)
            session.flush()
            session.expunge_all()
            loaded = session.get(SkillGrant, grant.id)
            assert loaded is not None
            assert loaded is not grant
            assert loaded.has_expert_evidence()
            assert loaded.user_competency_version == 2
            assert loaded.readiness_assessment_id == assessment
            assert loaded.confirmed_by_grant_id == expert_grant
            assert loaded.confirmed_by_grant_version == 1
            assert loaded.evidence_kind == SkillGrantEvidenceKind.INDEPENDENT_READINESS
