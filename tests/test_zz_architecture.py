import ast
import importlib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _imports_for(package: str) -> set[str]:
    imports: set[str] = set()
    for path in (PROJECT_ROOT / "app" / package).rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
    return imports


def test_domain_and_application_do_not_import_delivery_or_adapters():
    forbidden = ("fastapi", "starlette", "sqlite3", "huey", "app.infrastructure")
    for package in ("domain", "application"):
        assert not {
            name for name in _imports_for(package) if name in forbidden or name.startswith(forbidden)
        }


def test_layer_packages_import_without_web_framework():
    importlib.import_module("app.domain")
    importlib.import_module("app.application")
    importlib.import_module("app.application.ports")


def test_factory_preserves_existing_routes():
    from app.web import create_app

    paths = {route.path for route in create_app().routes}
    assert {"/", "/login", "/api/library/shelves", "/health"} <= paths
