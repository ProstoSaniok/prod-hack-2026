"""Durable single-POST intention and successful general PR-comment receipt."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    Uuid,
)


def register_review_projection_tables(metadata: MetaData) -> Table:
    """Central metadata owner calls once, then autogenerates the migration.

    Generic projection_receipts cannot represent a durable *ambiguous POST* or
    its remote ID. This record is an intention first, a receipt only on success.
    """
    return Table(
        "review_git_projections",
        metadata,
        Column("message_id", Uuid, ForeignKey("outbox_messages.id"), primary_key=True),
        Column("review_id", Uuid, nullable=False),
        Column("revision_id", Uuid, nullable=False),
        Column("revision_number", Integer, nullable=False),
        Column("body_hash", String(64), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("post_started_at", DateTime(timezone=True)),
        Column("external_comment_id", BigInteger),
        Column("completed_at", DateTime(timezone=True)),
        ForeignKeyConstraint(
            ["revision_id", "review_id"],
            ["review_revisions.id", "review_revisions.review_id"],
            name="fk_review_git_projection_revision",
        ),
        UniqueConstraint("review_id", "revision_number", name="uq_review_git_projection_revision"),
        CheckConstraint("revision_number > 0", name="ck_review_git_projection_number"),
        CheckConstraint("body_hash ~ '^[0-9a-f]{64}$'", name="ck_review_git_projection_hash"),
        CheckConstraint("external_comment_id > 0", name="ck_review_git_projection_comment"),
        CheckConstraint(
            "(external_comment_id IS NULL) = (completed_at IS NULL)", name="ck_review_git_projection_completion"
        ),
    )
