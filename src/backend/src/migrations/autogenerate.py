"""Preserve explicitly marked inline snapshot checks in Alembic ADD COLUMN output."""

from typing import Literal

from alembic.autogenerate.api import AutogenContext
from sqlalchemy import CheckConstraint, Column, Integer


def render_snapshot_version(kind: str, item: object, context: AutogenContext) -> str | Literal[False]:
    if kind != "column" or not isinstance(item, Column) or item.info.get("prodguild_snapshot_version") is not True:
        return False
    if item.name != "user_competency_version" or not isinstance(item.type, Integer) or item.nullable:
        raise ValueError("Snapshot versions require a non-null integer column")
    checks = [constraint for constraint in item.constraints if isinstance(constraint, CheckConstraint)]
    if len(checks) != 1 or checks[0].name is None:
        raise ValueError("Snapshot versions require one named inline check")
    context.imports.add("import sqlalchemy as sa")
    return (
        f"sa.Column({item.name!r}, sa.Integer(), "
        f"sa.CheckConstraint({str(checks[0].sqltext)!r}, name={str(checks[0].name)!r}), nullable=False)"
    )
