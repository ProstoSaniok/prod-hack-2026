"""Schema-aware append-only ledger DDL, including schema_translate_map fixtures."""

from sqlalchemy import Connection, Table, event, text
from sqlalchemy.sql.compiler import IdentifierPreparer

LEDGER_GUARD_FUNCTION = "prodguild_reject_ledger_mutation"
LEDGER_GUARD_TRIGGER = "prodguild_wallet_ledger_immutable"
LEDGER_GUARD_BODY = """BEGIN
    RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'Wallet ledger is append-only';
END;"""


def ledger_statements(schema: str, preparer: IdentifierPreparer, *, create: bool) -> tuple[str, str]:
    namespace = preparer.quote_schema(schema)
    function = f"{namespace}.{LEDGER_GUARD_FUNCTION}"
    table = f"{namespace}.wallet_ledger"
    if create:
        return (
            f"CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$\n{LEDGER_GUARD_BODY}\n$$",
            f"CREATE TRIGGER {LEDGER_GUARD_TRIGGER} BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {function}()",
        )
    return (f"DROP TRIGGER {LEDGER_GUARD_TRIGGER} ON {table}", f"DROP FUNCTION {function}()")


def ledger_schema(connection: Connection, table: Table) -> str:
    schema = connection.schema_for_object(table)
    if schema is None:
        schema = connection.scalar(text("SELECT current_schema()"))
    if not isinstance(schema, str) or not schema:
        raise ValueError("Wallet ledger requires an actual PostgreSQL schema")
    return schema


def _create_guard(table: Table, connection: Connection, **kwargs: object) -> None:
    if connection.dialect.name == "postgresql":
        for statement in ledger_statements(
            ledger_schema(connection, table), connection.dialect.identifier_preparer, create=True
        ):
            connection.exec_driver_sql(statement)


def _drop_guard(table: Table, connection: Connection, **kwargs: object) -> None:
    if connection.dialect.name == "postgresql":
        for statement in ledger_statements(
            ledger_schema(connection, table), connection.dialect.identifier_preparer, create=False
        ):
            connection.exec_driver_sql(statement)


def register_ledger_guard(table: Table) -> None:
    table.info["prodguild_append_only"] = True
    event.listen(table, "after_create", _create_guard)
    event.listen(table, "before_drop", _drop_guard)


def migration_ledger_statements(*, create: bool) -> tuple[str, str]:
    """Self-contained generated SQL resolves the migration's schema at execution."""
    if create:
        # PostgreSQL format %I quotes identifiers and %L quotes the function body.
        body = LEDGER_GUARD_BODY.replace("'", "''")
        return (
            "DO $prodguild$ BEGIN EXECUTE format('CREATE FUNCTION %I."
            f"{LEDGER_GUARD_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS %L', current_schema(), '{body}'); "
            "END $prodguild$",
            f"DO $prodguild$ BEGIN EXECUTE format('CREATE TRIGGER {LEDGER_GUARD_TRIGGER} "
            "BEFORE UPDATE OR DELETE OR TRUNCATE ON %I.wallet_ledger FOR EACH STATEMENT "
            f"EXECUTE FUNCTION %I.{LEDGER_GUARD_FUNCTION}()', current_schema(), current_schema()); END $prodguild$",
        )
    return (
        f"DO $prodguild$ BEGIN EXECUTE format('DROP TRIGGER {LEDGER_GUARD_TRIGGER} ON %I.wallet_ledger', "
        "current_schema()); END $prodguild$",
        f"DO $prodguild$ BEGIN EXECUTE format('DROP FUNCTION %I.{LEDGER_GUARD_FUNCTION}()', "
        "current_schema()); END $prodguild$",
    )
