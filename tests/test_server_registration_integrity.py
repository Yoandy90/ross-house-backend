import ast
from pathlib import Path


SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"


def _server_tree():
    return ast.parse(SERVER_PATH.read_text(encoding="utf-8"))


def test_router_registration_failure_stops_startup():
    """A partial API must never report healthy after router wiring fails."""
    tree = _server_tree()
    guarded_blocks = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Try)
        and any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "include_router"
            for child in ast.walk(node)
        )
    ]

    assert len(guarded_blocks) == 1
    handlers = guarded_blocks[0].handlers
    assert handlers
    assert any(
        isinstance(child, ast.Raise)
        and child.exc is not None
        for handler in handlers
        for child in ast.walk(handler)
    )


def test_ai_brain_router_is_registered_once():
    """Duplicate registration creates duplicate routes and OpenAPI operations."""
    tree = _server_tree()
    registrations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "include_router":
            continue
        if node.args and isinstance(node.args[0], ast.Name):
            registrations.append(node.args[0].id)

    assert registrations.count("ai_brain_router") == 1
