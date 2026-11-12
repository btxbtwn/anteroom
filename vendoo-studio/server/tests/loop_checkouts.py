"""Find route handlers that use the database on the event loop.

An ``async def`` route runs on the event loop. A blocking query there stops every
other request while it waits for a pooled connection, and a session kept across
an ``await`` holds its connection while Chrome, a marketplace or a model answers.
That is how the hosted pool ran dry. Route handlers that use the database are
plain ``def`` functions (FastAPI runs them in its threadpool), or async functions
that take no request session and run each database step through
``database.in_worker_session``.

There are no exceptions: a new case fails where it is introduced.
"""
from __future__ import annotations

import ast
import asyncio
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
PACKAGE = SERVER / "vendoo_studio"
SESSION_DEPENDENCIES = {"get_db", "get_seller_db"}


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


SESSION_FACTORIES = {"SessionLocal", "session_factory"}


def _own_nodes(function: ast.AsyncFunctionDef):
    """The function's own statements, leaving out nested functions and lambdas (worker-thread steps)."""
    stack: list[ast.AST] = list(function.body)
    while stack:
        node = stack.pop()
        yield node
        stack.extend(child for child in ast.iter_child_nodes(node)
                     if not isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef))


def _is_session(annotation: ast.expr | None) -> bool:
    return annotation is not None and ast.unparse(annotation).replace("'", "").replace('"', "") in {
        "Session", "Session | None", "Optional[Session]", "orm.Session"}


def _is_factory(annotation: ast.expr | None) -> bool:
    text = "" if annotation is None else ast.unparse(annotation)
    return text.startswith("Callable[") and text.endswith("Session]")


def _called(node: ast.expr) -> str:
    return ast.unparse(node.func).rsplit(".", 1)[-1] if isinstance(node, ast.Call) else ""


def loop_session_functions(folders=("services", "routes")) -> set[str]:
    """Every ``async def`` that opens a blocking session or uses one on the event loop.

    A session's queries and commits block the loop: on the Mac's SQLite a
    commit can wait 30 s for the write lock, freezing every request, stream
    and Chrome socket meanwhile. Async code runs each database step through
    ``database.in_worker_session`` instead, as a nested function or a plain
    ``def`` helper, which this leaves out.
    """
    found = set()
    for folder in folders:
        for path in sorted((PACKAGE / folder).rglob("*.py")):
            for function in ast.walk(ast.parse(path.read_text())):
                if not isinstance(function, ast.AsyncFunctionDef):
                    continue
                arguments = [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]
                sessions = {arg.arg for arg in arguments if arg.arg == "db" or _is_session(arg.annotation)}
                factories = SESSION_FACTORIES | {arg.arg for arg in arguments if _is_factory(arg.annotation)}
                nodes = list(_own_nodes(function))
                opens = False
                for node in nodes:
                    if _called(node) in factories:
                        opens = True
                    if isinstance(node, ast.withitem) and node.optional_vars is not None and _called(node.context_expr) in factories:
                        sessions.add(ast.unparse(node.optional_vars))
                    if isinstance(node, ast.Assign) and _called(node.value) in factories:
                        sessions.update(ast.unparse(target) for target in node.targets)
                uses = any(isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in sessions
                           for node in nodes)
                if opens or uses:
                    found.add(_name(function, path))
    return found


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
