from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata, produce_migrations, render_python_code
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import CheckConstraint, Engine, MetaData, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.adapters.data_gateways.tables import meta_data
from tests.persistence.conftest import upgrade
from tests.persistence.helpers import insert, seed_subject
from tests.persistence.learning_helpers import operation, publication, submission, workspace


def test_publication_is_immutable_same_case_and_exact_retained_commit(core_engine: Engine) -> None:
    content, cases = meta_data.tables["case_content_versions"], meta_data.tables["cases"]
    with core_engine.begin() as connection:
        published = publication(connection)
        values = dict(connection.execute(select(content).where(content.c.id == published.content)).mappings().one())
        with pytest.raises(IntegrityError, match="fk_content_published_commit"), connection.begin_nested():
            connection.execute(
                content.insert().values(**{
                    **values,
                    "id": uuid4(),
                    "content_version": 2,
                    "starter_commit_sha": "9" * 40,
                })
            )
        other = insert(
            connection,
            "cases",
            owner_id=published.author,
            title="Other",
            description="Other",
            expected_result="Other",
            status="DRAFT",
        )
        with pytest.raises(IntegrityError, match="fk_case_current_content"), connection.begin_nested():
            connection.execute(
                cases.update().where(cases.c.id == other).values(current_published_content_id=published.content)
            )
        connection.execute(cases.update().where(cases.c.id == published.case).values(status="DRAFT", version=9))
        refs = meta_data.tables["reference_solutions"]
        connection.execute(refs.update().where(refs.c.id == published.reference_solution).values(version=2))
        saved = connection.execute(select(content).where(content.c.id == published.content)).mappings().one()
        assert (saved["content_version"], saved["reference_version"], saved["starter_tree_sha"]) == (1, 1, "4" * 40)
        for statement in (
            "UPDATE case_content_versions SET title='changed'",
            "DELETE FROM case_content_versions",
            "TRUNCATE case_content_versions CASCADE",
        ):
            with pytest.raises(DBAPIError) as caught, connection.begin_nested():
                connection.execute(text(statement))
            assert getattr(caught.value.orig, "sqlstate", None) == "55000"


def test_recoverable_file_bytes_bound_to_actor_target_digest_and_operation(core_engine: Engine) -> None:
    intent = meta_data.tables["project_file_intents"]
    content = "Привет\x00\n".encode() + b"x" * 1000000
    with core_engine.begin() as connection:
        work = workspace(connection)
        op = operation(connection, "project.file", work.project)
        values = {
            "operation_id": op,
            "created_at": datetime.now(UTC),
            "payload_namespace": "prodguild-json-v1",
            "payload_hash": "a" * 64,
            "actor_id": work.publication.author,
            "project_id": work.project,
            "repository_id": work.repository,
            "working_branch": "work",
            "verb": "create",
            "path": "solution/текст.txt",
            "expected_head_sha": "5" * 40,
            "expected_blob_sha": None,
            "message_present": True,
            "commit_message": "",
            "content": content,
            "byte_length": len(content),
            "content_sha256": sha256(content).hexdigest(),
        }
        for changes, constraint in (
            ({"content_sha256": "b" * 64}, "ck_file_content"),
            ({"actor_id": uuid4()}, "fk_project_file_intents_target"),
            ({"verb": "delete", "expected_blob_sha": "8" * 40}, "ck_file_content"),
        ):
            with pytest.raises(IntegrityError, match=constraint), connection.begin_nested():
                connection.execute(intent.insert().values(**{**values, **changes}))
        connection.execute(intent.insert().values(**values))
        operations = meta_data.tables["operations"]
        connection.execute(operations.update().where(operations.c.id == op).values(state="SUCCEEDED"))
    with core_engine.begin() as connection:
        row = connection.execute(select(intent).where(intent.c.operation_id == op)).mappings().one()
        assert row["content"] == content
        assert row["content"].decode("utf-8").startswith("Привет\x00\n")
        assert row["content_sha256"] == sha256(content).hexdigest()
        assert (row["message_present"], row["commit_message"]) == (True, "")
        with pytest.raises(DBAPIError) as caught, connection.begin_nested():
            connection.execute(intent.update().where(intent.c.operation_id == op).values(path="other.txt"))
        assert getattr(caught.value.orig, "sqlstate", None) == "55000"
        start = meta_data.tables["project_start_intents"]
        result = meta_data.tables["project_start_results"]
        assert connection.scalar(select(start.c.starter_commit_sha)) == "2" * 40
        assert connection.scalar(select(result.c.workspace_base_commit_sha)) == "5" * 40
        start_values = dict(connection.execute(select(start)).mappings().one())
        repeat = operation(connection, "project.start", work.project)
        reserved = uuid4()
        with pytest.raises(IntegrityError, match="uq_start_participant_case"), connection.begin_nested():
            connection.execute(
                start.insert().values(**{
                    **start_values,
                    "operation_id": repeat,
                    "reserved_project_id": reserved,
                    "reserved_repository_id": uuid4(),
                    "repository_slug": f"prodguild-project-{reserved.hex}",
                })
            )


def test_submission_snapshot_retains_zero_performer_and_revoked_grant_history(core_engine: Engine) -> None:
    snapshots = meta_data.tables["submission_competencies"]
    with core_engine.begin() as connection:
        work = workspace(connection)
        second_skill = insert(connection, "competencies", name=str(uuid4()), description="Second")
        insert(
            connection,
            "case_content_competencies",
            content_version_id=work.publication.content,
            competency_id=second_skill,
            required_value=0,
            weight=Decimal("0.25"),
        )
        for skill in (work.publication.skill, second_skill):
            insert(
                connection,
                "user_competencies",
                user_id=work.publication.author,
                competency_id=skill,
                contribution_sum=0,
                displayed_value=0,
                grade="PERFORMER",
            )
        now = datetime.now(UTC)
        grant = insert(
            connection,
            "skill_grants",
            user_id=work.publication.author,
            competency_id=second_skill,
            user_competency_version=1,
            grade="REVIEW_MINUS",
            state="REVOKED",
            evidence_kind="SEED",
            evidence="Synthetic explicit historical grant, not numeric credit",
            seed_reference="test-only-grant",
            revoked_at=now,
            version=3,
            readiness_decision="CONFIRMED",
            confirmed_by_grade="EXPERT",
        )
        subject = submission(connection, work)
        common = {
            "submission_id": subject,
            "content_version_id": work.publication.content,
            "author_id": work.publication.author,
            "author_user_competency_version": 1,
            "author_snapshotted_at": now,
            "author_value": 0,
            "author_grade": "PERFORMER",
        }
        insert(
            connection,
            "submission_competencies",
            **common,
            competency_id=work.publication.skill,
            required_value=Decimal("20.12345678901234567890123456789"),
            weight=Decimal("0.125"),
        )
        revoked = insert(
            connection,
            "submission_competencies",
            **common,
            competency_id=second_skill,
            required_value=0,
            weight=Decimal("0.25"),
            author_grant_id=grant,
            author_grant_version=3,
            author_grant_state="REVOKED",
            author_grant_grade="REVIEW_MINUS",
            author_grant_user_competency_version=1,
            author_grant_evidence_kind="SEED",
            author_grant_evidence="Synthetic explicit historical grant, not numeric credit",
            author_seed_reference="test-only-grant",
            author_grant_revoked_at=now,
        )
        grants = meta_data.tables["skill_grants"]
        connection.execute(grants.update().where(grants.c.id == grant).values(version=4))
        row = connection.execute(select(snapshots).where(snapshots.c.id == revoked)).mappings().one()
        assert (row["author_grant_version"], row["author_grant_state"].value, row["author_grant_revoked_at"]) == (
            3,
            "revoked",
            now,
        )
        assert row["author_value"] == Decimal(0)
        assert row["author_user_competency_version"] == 1
        intents = meta_data.tables["submission_intents"]
        saved = connection.execute(select(intents)).mappings().one()
        assert (saved["summary_present"], saved["summary"]) == (True, "")
        with pytest.raises(DBAPIError) as caught, connection.begin_nested():
            connection.execute(
                snapshots.update().where(snapshots.c.id == revoked).values(author_grant_state="CONFIRMED")
            )
        assert getattr(caught.value.orig, "sqlstate", None) == "55000"


def test_learning_migration_preserves_legacy_unknowns_and_checks_guard_drift(preceding_engine: Engine) -> None:
    upgrade("4caf490858cb")
    with preceding_engine.begin() as connection:
        legacy = seed_subject(connection)
    upgrade()
    with preceding_engine.connect() as connection:
        projects = meta_data.tables["projects"]
        row = connection.execute(select(projects).where(projects.c.id == legacy.project)).mappings().one()
        assert row["case_version"] == 1
        assert row["content_version_id"] is None
        assert row["updated_at"] is None
        assert compare_metadata(MigrationContext.configure(connection), meta_data) == []
        with connection.begin_nested():
            connection.execute(
                text("ALTER TABLE project_file_intents DISABLE TRIGGER prodguild_project_file_intents_immutable")
            )
            with pytest.raises(ValueError, match="differs from metadata: project_file_intents"):
                compare_metadata(MigrationContext.configure(connection), meta_data)
        connection.rollback()
    command.downgrade(Config("alembic.ini"), "4caf490858cb")
    upgrade()
    with preceding_engine.connect() as connection:
        assert connection.scalar(select(meta_data.tables["projects"].c.id)) == legacy.project
        assert compare_metadata(MigrationContext.configure(connection), meta_data) == []


def test_same_name_learning_check_expression_changes_generate_reversible_replacement(core_engine: Engine) -> None:
    with core_engine.connect() as connection:
        context = MigrationContext.configure(connection)
        assert compare_metadata(context, meta_data) == []
        with connection.begin_nested() as tamper:
            connection.execute(text("ALTER TABLE project_file_intents DROP CONSTRAINT ck_file_content"))
            connection.execute(text("ALTER TABLE project_file_intents ADD CONSTRAINT ck_file_content CHECK (true)"))
            repair = produce_migrations(context, meta_data)
            assert repair.upgrade_ops is not None
            assert repair.downgrade_ops is not None
            assert repair.upgrade_ops.as_diffs() == [
                ("prodguild_learning_check", "project_file_intents", "ck_file_content", False),
                ("prodguild_learning_check", "project_file_intents", "ck_file_content", True),
            ]
            assert "op.drop_constraint('ck_file_content'" in render_python_code(repair.upgrade_ops)
            assert "sha256(content)" in render_python_code(repair.upgrade_ops)
            assert "'true'" in render_python_code(repair.downgrade_ops)
            tamper.rollback()
        assert compare_metadata(context, meta_data) == []

        revised = MetaData()
        for table in meta_data.tables.values():
            table.to_metadata(revised)
        constraint = next(
            check
            for check in revised.tables["project_file_intents"].constraints
            if isinstance(check, CheckConstraint) and check.name == "ck_file_content"
        )
        constraint.sqltext = text(f"({constraint.sqltext}) AND byte_length <> 999")
        change = produce_migrations(context, revised)
        assert change.upgrade_ops is not None
        assert change.downgrade_ops is not None
        assert change.upgrade_ops.as_diffs() == [
            ("prodguild_learning_check", "project_file_intents", "ck_file_content", False),
            ("prodguild_learning_check", "project_file_intents", "ck_file_content", True),
        ]
        assert "byte_length <> 999" in render_python_code(change.upgrade_ops)
        assert "byte_length <> 999" not in render_python_code(change.downgrade_ops)
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_class WHERE relnamespace = pg_my_temp_schema() "
                    "AND relname LIKE 'prodguild_check_%'"
                )
            )
            == 0
        )
