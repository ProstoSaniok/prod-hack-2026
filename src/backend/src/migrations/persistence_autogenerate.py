"""Autogenerate cyclic FK installation and the metadata-declared ledger trigger."""

from __future__ import annotations

from collections.abc import Collection
from typing import override

from alembic.autogenerate import comparators, renderers
from alembic.autogenerate.api import AutogenContext
from alembic.operations import ops
from sqlalchemy import MetaData, text

from src.adapters.data_gateways.ledger_ddl import (
    LEDGER_GUARD_BODY,
    LEDGER_GUARD_FUNCTION,
    LEDGER_GUARD_TRIGGER,
    ledger_schema,
    migration_ledger_statements,
)


class LedgerGuardOp(ops.MigrateOperation):
    def __init__(self, *, create: bool) -> None:
        self.create = create

    @override
    def reverse(self) -> LedgerGuardOp:
        return LedgerGuardOp(create=not self.create)

    @override
    def to_diff_tuple(self) -> tuple[str, bool]:
        return ("prodguild_ledger_guard", self.create)


@renderers.dispatch_for(LedgerGuardOp)
def render_ledger_guard(context: AutogenContext, operation: LedgerGuardOp) -> list[str]:
    statements = migration_ledger_statements(create=operation.create)
    return [f"op.execute({statement!r})" for statement in statements]


@comparators.dispatch_for("schema")
def compare_persistence_ddl(
    context: AutogenContext, upgrade: ops.UpgradeOps, schemas: Collection[str | None]
) -> None:
    metadata = context.metadata
    if not isinstance(metadata, MetaData) or context.connection is None:
        return
    new_tables = {operation.table_name for operation in upgrade.ops if isinstance(operation, ops.CreateTableOp)}
    # Alembic omits use_alter FKs from CREATE TABLE. Install them in this SAME
    # generated revision, after both ends exist; reverse() drops them first.
    for name in sorted(new_tables):
        for constraint in sorted(metadata.tables[name].foreign_key_constraints, key=lambda item: str(item.name)):
            if constraint.use_alter:
                upgrade.ops.append(ops.CreateForeignKeyOp.from_constraint(constraint))
    ledger = metadata.tables.get("wallet_ledger")
    if ledger is None or ledger.info.get("prodguild_append_only") is not True:
        return
    if "wallet_ledger" in new_tables:
        upgrade.ops.append(LedgerGuardOp(create=True))
        return
    found = context.connection.execute(
        text(
            "SELECT t.tgtype, t.tgenabled, p.prosrc, p.proname, pn.nspname = n.nspname AS same_schema, "
            "t.tgqual IS NULL AS unconditional, t.tgattr::text = '' AS all_columns, t.tgnargs "
            "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace JOIN pg_proc p ON p.oid = t.tgfoid "
            "JOIN pg_namespace pn ON pn.oid = p.pronamespace "
            "WHERE n.nspname = :schema AND c.relname = 'wallet_ledger' "
            "AND t.tgname = :trigger AND NOT t.tgisinternal"
        ),
        {"trigger": LEDGER_GUARD_TRIGGER, "schema": ledger_schema(context.connection, ledger)},
    ).mappings().one_or_none()
    if found is None:
        # Do not silently declare an existing table safe when enforcement was
        # removed. A repair must explicitly reconcile any orphan function.
        raise ValueError("Wallet ledger immutability trigger is missing; explicit repair required")
    if (
        found["tgtype"] != 58
        or found["tgenabled"] != "O"
        or found["prosrc"].strip() != LEDGER_GUARD_BODY
        or found["proname"] != LEDGER_GUARD_FUNCTION
        or not found["same_schema"]
        or not found["unconditional"]
        or not found["all_columns"]
        or found["tgnargs"] != 0
    ):
        raise ValueError("Wallet ledger immutability trigger differs from metadata")
