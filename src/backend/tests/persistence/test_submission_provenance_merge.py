from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, select, text
from sqlalchemy.exc import IntegrityError

from src.adapters.data_gateways.tables import meta_data
from tests.persistence.conftest import upgrade
from tests.persistence.helpers import insert, seed_grant
from tests.persistence.learning_helpers import submission, workspace


def snapshot_with_confirmation(connection: Connection, evidence: str, *, confirmed: bool = True) -> UUID:
    work = workspace(connection)
    author, skill = work.publication.author, work.publication.skill
    expert = insert(connection, "users", role="USER")
    expert_grant = seed_grant(connection, expert, skill, expert=True)
    insert(
        connection,
        "user_competencies",
        user_id=author,
        competency_id=skill,
        contribution_sum=Decimal(40),
        displayed_value=Decimal(40),
        grade="REVIEW_MINUS",
    )
    now = datetime.now(UTC)
    seed = "explicit-supervised-demo-seed" if evidence == "SEED" else None
    grant = insert(
        connection,
        "skill_grants",
        user_id=author,
        competency_id=skill,
        user_competency_version=1,
        grade="REVIEW_MINUS",
        state="CONFIRMED" if confirmed else "PROVISIONAL",
        evidence_kind=evidence,
        evidence="Three independently confirmed reviews",
        confirmed_at=now if confirmed else None,
        seed_reference=seed,
        confirmed_by_user_id=expert,
        confirmed_by_grant_id=expert_grant,
        confirmed_by_grant_version=1,
        confirmed_by_grade="EXPERT",
        readiness_decision="CONFIRMED",
    )
    return insert(
        connection,
        "submission_competencies",
        submission_id=submission(connection, work),
        content_version_id=work.publication.content,
        author_id=author,
        competency_id=skill,
        required_value=Decimal("20.12345678901234567890123456789"),
        weight=Decimal("0.125"),
        author_user_competency_version=1,
        author_snapshotted_at=now,
        author_value=40,
        author_grade="REVIEW_MINUS",
        author_grant_id=grant,
        author_grant_version=1,
        author_grant_state="CONFIRMED" if confirmed else "PROVISIONAL",
        author_grant_grade="REVIEW_MINUS",
        author_grant_user_competency_version=1,
        author_grant_evidence_kind=evidence,
        author_grant_evidence="Three independently confirmed reviews",
        author_grant_confirmed_at=now if confirmed else None,
        author_seed_reference=seed,
        author_confirmed_by_user_id=expert,
        author_confirmed_by_grant_id=expert_grant,
        author_confirmed_by_grant_version=1,
    )


@pytest.mark.parametrize(
    ("starting_revision", "evidence", "confirmed"),
    [
        ("9dc2d00ad972", "PROGRESSION", True),
        ("eef0ecb61367", "SEED", True),
        ("eef0ecb61367", "PROGRESSION", True),
        ("eef0ecb61367", "PROGRESSION", False),
        ("07b1f936e60c", "SEED", True),
        ("07b1f936e60c", "PROGRESSION", True),
        ("07b1f936e60c", "PROGRESSION", False),
    ],
)
def test_merge_preserves_author_snapshot_from_either_reconciled_head(
    preceding_engine: Engine, starting_revision: str, evidence: str, *, confirmed: bool
) -> None:
    snapshots = meta_data.tables["submission_competencies"]
    upgrade(starting_revision)
    with preceding_engine.begin() as connection:
        snapshot = snapshot_with_confirmation(connection, evidence, confirmed=confirmed)
        before = dict(connection.execute(select(snapshots).where(snapshots.c.id == snapshot)).mappings().one())
    upgrade()
    with preceding_engine.connect() as connection:
        after = dict(connection.execute(select(snapshots).where(snapshots.c.id == snapshot)).mappings().one())
        assert after == before
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all() == [
            ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
        ]


@pytest.mark.parametrize(("evidence", "confirmed"), [("SEED", True), ("PROGRESSION", True), ("PROGRESSION", False)])
def test_merged_schema_accepts_independent_graduation_and_rejects_forged_confirmation(
    core_engine: Engine, evidence: str, *, confirmed: bool
) -> None:
    snapshots = meta_data.tables["submission_competencies"]
    with core_engine.begin() as connection:
        snapshot = snapshot_with_confirmation(connection, evidence, confirmed=confirmed)
        values = dict(connection.execute(select(snapshots).where(snapshots.c.id == snapshot)).mappings().one())
        # Only the claimed confirming actor changes; the immutable source stays untouched.
        values["author_confirmed_by_user_id"] = values["author_id"]
        with pytest.raises(IntegrityError, match="ck_author_grant_provenance"), connection.begin_nested():
            connection.execute(snapshots.insert().values(values))
