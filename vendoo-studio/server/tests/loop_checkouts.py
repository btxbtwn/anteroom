"""Find route handlers that use the database on the event loop.

An ``async def`` route runs on the event loop. A blocking query there stops every
other request while it waits for a pooled connection, and a session kept across
an ``await`` holds its connection while Chrome, a marketplace or a model answers.
That is how the hosted pool ran dry. Route handlers that use the database are
plain ``def`` functions (FastAPI runs them in its threadpool), or async functions
that take no request session and run each database step through
``database.in_worker_session``.

``async_route_sessions_baseline.txt`` records the handlers that did this before
the check existed. It may only shrink: fix a handler, then remove its line.
"""
from __future__ import annotations

import ast
import asyncio
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
PACKAGE = SERVER / "vendoo_studio"
BASELINE = Path(__file__).with_name("async_route_sessions_baseline.txt")
SESSION_DEPENDENCIES = {"get_db", "get_seller_db"}


def recorded() -> set[str]:
    lines = BASELINE.read_text().splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def _name(node: ast.AST, path: Path) -> str:
    return f"{path.relative_to(PACKAGE).as_posix()}:{node.name}"  # type: ignore[attr-defined]


def _depends_on(function: ast.FunctionDef | ast.AsyncFunctionDef, names: set[str]) -> bool:
    defaults = [*function.args.defaults, *(value for value in function.args.kw_defaults if value is not None)]
    for value in defaults:
        if (isinstance(value, ast.Call) and getattr(value.func, "id", None) == "Depends" and value.args
                and getattr(value.args[0], "id", getattr(value.args[0], "attr", None)) in names):
            return True
    return False


def async_session_handlers() -> set[str]:
    """Every ``async def`` that takes a request session through ``Depends``, directly or through
    another dependency that does (such as ``get_import_db``)."""
    trees = [(path, ast.parse(path.read_text())) for path in sorted(PACKAGE.rglob("*.py"))]
    functions = [(path, node) for path, tree in trees for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)]
    sessions = set(SESSION_DEPENDENCIES)
    while True:
        more = {node.name for _path, node in functions
                if isinstance(node, ast.FunctionDef) and node.name not in sessions and _depends_on(node, sessions)}
        if not more:
            break
        sessions |= more
    return {_name(node, path) for path, node in functions
            if isinstance(node, ast.AsyncFunctionDef) and _depends_on(node, sessions)}


class EndpointCheckouts:
    """Records each async route handler that checks out a pooled connection on the event loop."""

    def __init__(self, root: Path = PACKAGE):
        self.root = root
        self.found: dict[str, str] = {}

    def checkout(self, *_args) -> None:
        if asyncio._get_running_loop() is None:  # a worker thread: allowed
            return
        frame = sys._getframe(1)
        while frame is not None and frame.f_back is not None:
            caller = frame.f_back
            if caller.f_code.co_name == "run_endpoint_function" and caller.f_code.co_filename.endswith("fastapi/routing.py"):
                path = Path(frame.f_code.co_filename)
                if self.root in path.parents:
                    name = f"{path.relative_to(self.root).as_posix()}:{frame.f_code.co_name}"
                    self.found.setdefault(name, _site())
                return
            frame = caller


def _site() -> str:
    import traceback

    frames = [frame for frame in traceback.extract_stack() if "/vendoo_studio/" in frame.filename]
    return " < ".join(f"{Path(frame.filename).name}:{frame.lineno}" for frame in reversed(frames[-6:]))
