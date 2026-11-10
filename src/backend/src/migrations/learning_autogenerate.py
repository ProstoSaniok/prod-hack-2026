"""Generate learning CHECK/enum changes and write-once DDL from central metadata."""

from __future__ import annotations

import re
from collections.abc import Collection
from typing import override
from uuid import uuid4

from alembic.autogenerate import comparators, renderers
from alembic.autogenerate.api import AutogenContext
from alembic.operations import ops
from sqlalchemy import CheckConstraint, Column, Enum, MetaData, String, Table, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.schema import CreateTable

from src.adapters.data_gateways.learning_ddl import (
    LEARNING_GUARD_BODY,
    learning_guard_names,
    migration_learning_statements,
)
from src.adapters.data_gateways.ledger_ddl import ledger_schema


class LearningCheckOp(ops.MigrateOperation):
    def __init__(self, table: str, name: str, expression: str, *, create: bool) -> None:
        self.table, self.name, self.expression, self.create = table, name, expression, create

    @override
    def reverse(self) -> LearningCheckOp:
        return LearningCheckOp(self.table, self.name, self.expression, create=not self.create)

    @override
    def to_diff_tuple(self) -> tuple[str, str, str, bool]:
        return ("prodguild_learning_check", self.table, self.name, self.create)


@renderers.dispatch_for(LearningCheckOp)
def render_check(context: AutogenContext, operation: LearningCheckOp) -> str:
    if operation.create:
        return f"op.create_check_constraint({operation.name!r}, {operation.table!r}, {operation.expression!r})"
    return f"op.drop_constraint({operation.name!r}, {operation.table!r}, type_='check')"


class LearningGuardOp(ops.MigrateOperation):
    def __init__(self, table: str, *, create: bool) -> None:
        self.table, self.create = table, create

    @override
    def reverse(self) -> LearningGuardOp:
        return LearningGuardOp(self.table, create=not self.create)

    @override
    def to_diff_tuple(self) -> tuple[str, str, bool]:
        return ("prodguild_learning_guard", self.table, self.create)


@renderers.dispatch_for(LearningGuardOp)
def render_guard(context: AutogenContext, operation: LearningGuardOp) -> list[str]:
    return [f"op.execute({statement!r})" for statement in migration_learning_statements(operation.table, create=operation.create)]


def _catalog_check_expressions(context: AutogenContext, table: Table) -> dict[str, str]:
    connection = context.connection
    if connection is None:
        raise ValueError("Learning CHECK comparison requires an online PostgreSQL connection")
    preparer = connection.dialect.identifier_preparer
    relation = f"{preparer.quote_schema(ledger_schema(connection, table))}.{preparer.quote(table.name)}"
    # SQLAlchemy's CHECK reflection strips apparent outer parentheses with a
    # regex, which can unbalance `(a = b) = (c IS NULL)`. Keep the server's exact
    # expression for both comparison and a reversible downgrade instead.
    return dict(connection.execute(text(
        "SELECT conname, pg_get_expr(conbin, conrelid) FROM pg_constraint "
        "WHERE conrelid = to_regclass(:relation) AND contype = 'c'"
    ), {"relation": relation}).tuples().all())


def _changed_learning_checks(
    context: AutogenContext, table: Table, expressions: dict[str, tuple[str, str]]
) -> set[str]:
    """Compare PostgreSQL parse/deparse results, never strip semantic parentheses.

    Reflection adds casts, rewrites IN/BETWEEN and parenthesizes expressions.
    Normalize the desired form against the intended column types and compare to
    the intact existing pg_get_expr result. Do not reparse the old expression:
    PostgreSQL can distribute a deparsed array cast into its elements on reparse.
    A temporary, empty probe is always rolled back; no application table is
    altered or locked. Failure to parse/normalize fails autogenerate closed.
    """
    if not expressions:
        return set()
    connection = context.connection
    if connection is None or connection.dialect.name != "postgresql":
        raise ValueError("Learning CHECK comparison requires an online PostgreSQL connection")
    probe = Table(f"prodguild_check_{uuid4().hex}", MetaData(), schema="pg_temp", prefixes=["TEMPORARY"])
    for column in table.columns:
        column_type = column.type.copy()
        if isinstance(column_type, Enum):
            column_type.create_constraint = False
        probe.append_column(Column(column.name, column_type))
    keys: dict[str, str] = {}
    for index, (name, (_, desired)) in enumerate(expressions.items()):
        new_key = f"new_{index}"
        keys[name] = new_key
        probe.append_constraint(CheckConstraint(desired, name=new_key))
    relation = connection.dialect.identifier_preparer.format_table(probe)
    try:
        with connection.begin_nested() as savepoint:
            try:
                connection.execute(CreateTable(probe))
                canonical = dict(connection.execute(text(
                    "SELECT conname, pg_get_expr(conbin, conrelid) FROM pg_constraint "
                    "WHERE conrelid = to_regclass(:relation) AND contype = 'c'"
                ), {"relation": relation}).tuples().all())
                if set(canonical) != set(keys.values()):
                    raise ValueError("PostgreSQL did not return all learning CHECK expressions")
                return {name for name, new_key in keys.items() if expressions[name][0] != canonical[new_key]}
            finally:
                savepoint.rollback()
    except DBAPIError as error:
        raise ValueError(f"Cannot establish learning CHECK equivalence for {table.name}") from error


@comparators.dispatch_for("table")
def compare_learning_checks(context: AutogenContext, changes: ops.ModifyTableOps, schema: str | None,
                            name: str, reflected: Table | None, desired: Table | None) -> None:
    if reflected is None or desired is None:
        return
    # Reflection reports emulated ENUM as VARCHAR. Render only its physical type
    # alteration here; the explicit CHECK replacement below owns both directions.
    for operation in changes.ops:
        if isinstance(operation, ops.AlterColumnOp) and isinstance(operation.modify_type, Enum):
            if "PUBLISHED_MATERIALS" in operation.modify_type.enums:
                operation.modify_type = String(operation.modify_type.length)
    previous = {str(check.name): check for check in reflected.constraints if isinstance(check, CheckConstraint)}
    new_columns = {operation.column.name for operation in changes.ops if isinstance(operation, ops.AddColumnOp)}
    existing_learning_checks: dict[str, tuple[str, str]] = {}
    catalog_expressions: dict[str, str] | None = None
    for constraint in sorted(desired.constraints, key=lambda item: str(item.name)):
        if not isinstance(constraint, CheckConstraint) or constraint.name is None:
            continue
        expression = str(constraint.sqltext.compile(dialect=context.dialect, compile_kwargs={"literal_binds": True}))
        enum_check = "PUBLISHED_MATERIALS" in expression
        if not enum_check and not constraint.info.get("prodguild_learning_check"):
            continue
        old = previous.get(str(constraint.name))
        if old is None:
            # Alembic AddColumn already installs Enum's type-bound CHECK.
            if enum_check and any(column.name in new_columns for column in constraint.columns):
                continue
            changes.ops.append(LearningCheckOp(name, str(constraint.name), expression, create=True))
        elif enum_check and set(re.findall(r"'([^']*)'", str(old.sqltext))) != set(re.findall(r"'([^']*)'", expression)):
            changes.ops.append(LearningCheckOp(name, str(constraint.name), str(old.sqltext), create=False))
            changes.ops.append(LearningCheckOp(name, str(constraint.name), expression, create=True))
        elif constraint.info.get("prodguild_learning_check"):
            if catalog_expressions is None:
                catalog_expressions = _catalog_check_expressions(context, reflected)
            old_expression = catalog_expressions.get(str(constraint.name))
            if old_expression is None:
                raise ValueError(f"Cannot read existing learning CHECK: {name}.{constraint.name}")
            existing_learning_checks[str(constraint.name)] = old_expression, expression
    for constraint_name in sorted(_changed_learning_checks(context, desired, existing_learning_checks)):
        old_expression, desired_expression = existing_learning_checks[constraint_name]
        changes.ops.append(LearningCheckOp(name, constraint_name, old_expression, create=False))
        changes.ops.append(LearningCheckOp(name, constraint_name, desired_expression, create=True))


@comparators.dispatch_for("schema")
def compare_learning_ddl(context: AutogenContext, upgrade: ops.UpgradeOps, schemas: Collection[str | None]) -> None:
    metadata = context.metadata
    if not isinstance(metadata, MetaData) or context.connection is None:
        return
    # Existing tables acquire new composite UNIQUEs in this revision. All their
    # incoming FKs must be installed after those ALTER TABLEs, not alphabetically.
    foreign_keys: list[ops.MigrateOperation] = []
    kept: list[ops.MigrateOperation] = []
    for operation in upgrade.ops:
        if isinstance(operation, ops.CreateForeignKeyOp):
            foreign_keys.append(operation)
        else:
            if isinstance(operation, ops.ModifyTableOps):
                foreign_keys.extend(item for item in operation.ops if isinstance(item, ops.CreateForeignKeyOp))
                operation.ops = [item for item in operation.ops if not isinstance(item, ops.CreateForeignKeyOp)]
            kept.append(operation)
    upgrade.ops = [*kept, *foreign_keys]
    new_tables = {operation.table_name for operation in upgrade.ops if isinstance(operation, ops.CreateTableOp)}
    for table in metadata.tables.values():
        if not table.info.get("prodguild_learning_immutable"):
            continue
        trigger, function = learning_guard_names(table.name)
        found = context.connection.execute(text(
            "SELECT t.tgtype, t.tgenabled, p.prosrc, p.proname, pn.nspname = n.nspname AS same_schema, "
            "t.tgqual IS NULL AS unconditional, t.tgattr::text = '' AS all_columns, t.tgnargs "
            "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace JOIN pg_proc p ON p.oid = t.tgfoid "
            "JOIN pg_namespace pn ON pn.oid = p.pronamespace "
            "WHERE n.nspname = :schema AND c.relname = :table AND t.tgname = :trigger AND NOT t.tgisinternal"
        ), {"schema": ledger_schema(context.connection, table), "table": table.name, "trigger": trigger}).mappings().one_or_none()
        if found is None:
            # The first rollout also protects the existing author snapshot table.
            baseline_author = table.name == "submission_competencies" and any(
                isinstance(operation, ops.ModifyTableOps) and operation.table_name == table.name
                and any(isinstance(item, ops.AddColumnOp) and item.column.name == "author_snapshotted_at" for item in operation.ops)
                for operation in upgrade.ops
            )
            if table.name in new_tables or baseline_author:
                upgrade.ops.append(LearningGuardOp(table.name, create=True))
                continue
            raise ValueError(f"Learning immutability trigger missing: {table.name}")
        if (found["tgtype"] != 58 or found["tgenabled"] != "O" or found["prosrc"].strip() != LEARNING_GUARD_BODY
                or found["proname"] != function or not found["same_schema"] or not found["unconditional"]
                or not found["all_columns"] or found["tgnargs"] != 0):
            raise ValueError(f"Learning immutability trigger differs from metadata: {table.name}")
