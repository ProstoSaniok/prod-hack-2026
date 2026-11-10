"""Schema-aware write-once guards for publication, recovery and author evidence."""

from sqlalchemy import Connection, Table, event
from sqlalchemy.sql.compiler import IdentifierPreparer

from src.adapters.data_gateways.ledger_ddl import ledger_schema

LEARNING_GUARD_BODY = """BEGIN
    RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'Learning evidence is write-once';
END;"""


def learning_guard_names(table: str) -> tuple[str, str]:
    return f"prodguild_{table}_immutable", f"prodguild_reject_{table}"


def learning_statements(schema: str, table: str, preparer: IdentifierPreparer, *, create: bool) -> tuple[str, str]:
    trigger, function_name = learning_guard_names(table)
    namespace = preparer.quote_schema(schema)
    function = f"{namespace}.{preparer.quote(function_name)}"
    relation = f"{namespace}.{preparer.quote(table)}"
    if create:
        return (
            f"CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$\n{LEARNING_GUARD_BODY}\n$$",
            f"CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE OR TRUNCATE ON {relation} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {function}()",
        )
    return f"DROP TRIGGER {trigger} ON {relation}", f"DROP FUNCTION {function}()"


def _create(table: Table, connection: Connection, **kwargs: object) -> None:
    if connection.dialect.name == "postgresql":
        for statement in learning_statements(
            ledger_schema(connection, table), table.name, connection.dialect.identifier_preparer, create=True
        ):
            connection.exec_driver_sql(statement)


def _drop(table: Table, connection: Connection, **kwargs: object) -> None:
    if connection.dialect.name == "postgresql":
        for statement in learning_statements(
            ledger_schema(connection, table), table.name, connection.dialect.identifier_preparer, create=False
        ):
            connection.exec_driver_sql(statement)


def register_learning_guard(table: Table) -> None:
    table.info["prodguild_learning_immutable"] = True
    event.listen(table, "after_create", _create)
    event.listen(table, "before_drop", _drop)


def migration_learning_statements(table: str, *, create: bool) -> tuple[str, str]:
    trigger, function = learning_guard_names(table)
    if create:
        body = LEARNING_GUARD_BODY.replace("'", "''")
        return (
            "DO $prodguild$ BEGIN EXECUTE format('CREATE FUNCTION %I."
            f"{function}() RETURNS trigger LANGUAGE plpgsql AS %L', current_schema(), '{body}'); END $prodguild$",
            f"DO $prodguild$ BEGIN EXECUTE format('CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE OR TRUNCATE "
            f"ON %I.{table} FOR EACH STATEMENT EXECUTE FUNCTION %I.{function}()', "
            "current_schema(), current_schema()); END $prodguild$",
        )
    return (
        f"DO $prodguild$ BEGIN EXECUTE format('DROP TRIGGER {trigger} ON %I.{table}', "
        "current_schema()); END $prodguild$",
        f"DO $prodguild$ BEGIN EXECUTE format('DROP FUNCTION %I.{function}()', current_schema()); END $prodguild$",
    )
