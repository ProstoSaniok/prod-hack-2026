"""Keep dependency direction and domain failure contracts executable."""

import ast
import importlib
import inspect
from pathlib import Path

from src.application.common.errors import ApplicationError

SOURCE = Path(__file__).resolve().parents[2] / "src"


def imported_modules(path: Path) -> list[str]:
    return [
        name
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        for name in (
            [node.module or ""]
            if isinstance(node, ast.ImportFrom)
            else [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else []
        )
    ]


def test_application_has_no_framework_or_adapter_dependencies() -> None:
    forbidden = ("src.adapters", "src.web_api", "fastapi", "starlette", "sqlalchemy", "dishka")
    violations = [
        f"{path.relative_to(SOURCE)} imports {module}"
        for path in (SOURCE / "application").rglob("*.py")
        for module in imported_modules(path)
        if any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden)
    ]
    assert not violations, "\n".join(violations)


def test_adapters_do_not_depend_on_http_composition() -> None:
    violations = [
        f"{path.relative_to(SOURCE)} imports {module}"
        for path in (SOURCE / "adapters").rglob("*.py")
        for module in imported_modules(path)
        if module.startswith(("src.web_api", "fastapi", "starlette"))
    ]
    assert not violations, "\n".join(violations)


def test_endpoints_only_depend_on_application_and_shared_http() -> None:
    violations = [
        f"{path.name} imports {module}"
        for path in (SOURCE / "web_api/routes").glob("*.py")
        for module in imported_modules(path)
        if module.startswith(("src.web_api.routes", "src.adapters", "sqlalchemy"))
    ]
    assert not violations, "\n".join(violations)


def test_declared_domain_errors_share_application_error() -> None:
    violations = []
    for path in (SOURCE / "application").rglob("*.py"):
        module_name = ".".join(path.relative_to(SOURCE.parent).with_suffix("").parts)
        module = importlib.import_module(module_name)
        for name, value in vars(module).items():
            if (
                name.endswith("Error")
                and inspect.isclass(value)
                and value.__module__ == module_name
                and issubclass(value, Exception)
                and not issubclass(value, ApplicationError)
            ):
                violations.append(f"{module_name}.{name}")
    assert not violations, "\n".join(violations)


def test_test_support_does_not_import_test_modules() -> None:
    tests = SOURCE.parent / "tests"
    violations = [
        f"{path.relative_to(tests)} imports {module}"
        for path in tests.rglob("*.py")
        for module in imported_modules(path)
        if module.startswith("tests.") and any(part.startswith("test_") for part in module.split("."))
    ]
    assert not violations, "\n".join(violations)
