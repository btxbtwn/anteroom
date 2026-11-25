"""Hosted migrations run on deploy against a live database; they must not stall it."""
from __future__ import annotations

import ast
import asyncio
import re
import time
from argparse import Namespace
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy.exc import OperationalError

from test_seller_ownership import migration_config, migration_engine  # noqa: F401
from anteroom.hosted import create_app
from anteroom.services import schema_migrations

# Revisions up to this one predate the rule and are not rewritten.
LAST_UNCHECKED_REVISION = 'e3bea75f1b9b'
# Revisions up to this one predate the expand/contract drop guard and are not rewritten.
LAST_UNCHECKED_DROP_REVISION = 'c7a3e9f41b28'

_SQL_TABLE = re.compile(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:[\w"]+\.)?"?(\w+)"?', re.I)
_SQL_INDEX = re.compile(
    r'CREATE\s+(?:UNIQUE\s+)?INDEX\s+(CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?[\w"]+\s+ON\s+(?:ONLY\s+)?'
    r'(?:[\w"]+\.)?"?(\w+)"?', re.I)
_SQL_DESTRUCTIVE_DROP = re.compile(r'\bDROP\s+(TABLE|COLUMN|FUNCTION)\b', re.I)


def _string(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _contract_after_marker(tree: ast.Module) -> str | None:
    """Return the non-empty module-level CONTRACT_AFTER marker, if declared."""
    for node in tree.body:
        value = None
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == 'CONTRACT_AFTER' for target in node.targets):
                value = _string(node.value)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == 'CONTRACT_AFTER':
                value = _string(node.value)
        if value is not None and value.strip():
            return value.strip()
    return None


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    constants: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and (text := _string(node.value)) is not None:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = text
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if (text := _string(node.value)) is not None:
                constants[node.target.id] = text
    return constants


def _upgrade_functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if 'upgrade' not in functions:
        return []
    reachable: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    seen: set[str] = set()
    queue = ['upgrade']
    while queue:
        name = queue.pop(0)
        if name in seen or name == 'downgrade' or name not in functions:
            continue
        seen.add(name)
        fn = functions[name]
        reachable.append(fn)
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Name)
                and node.id in functions
                and node.id not in seen
                and node.id != 'downgrade'
            ):
                queue.append(node.id)
    return reachable


def _function_body_without_docstring(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    body = fn.body
    if body and isinstance(body[0], ast.Expr) and _string(body[0].value) is not None:
        return body[1:]
    return body


def _collect_drop_problems(
    node: ast.AST,
    module_strings: dict[str, str],
    problems: list[str],
) -> None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr == 'drop_column':
            keywords = {kw.arg: kw.value for kw in node.keywords}
            if len(node.args) >= 2:
                target = f'{_string(node.args[0]) or "?"}.{_string(node.args[1]) or "?"}'
            elif node.args:
                first = _string(node.args[0]) or '?'
                if 'column_name' in keywords:
                    target = f'{first}.{_string(keywords["column_name"]) or "?"}'
                else:
                    target = first
            else:
                table = _string(keywords.get('table_name'))
                column = _string(keywords.get('column_name'))
                target = f'{table}.{column}' if table and column else (column or '?')
            problems.append(f'line {node.lineno}: drop_column({target}) needs CONTRACT_AFTER')
        elif node.func.attr == 'drop_table':
            keywords = {kw.arg: kw.value for kw in node.keywords}
            table = _string(node.args[0]) if node.args else _string(keywords.get('table_name'))
            problems.append(f'line {node.lineno}: drop_table({table or "?"}) needs CONTRACT_AFTER')
    if isinstance(node, ast.JoinedStr):
        rendered = ''.join(
            part.value if isinstance(part, ast.Constant) and isinstance(part.value, str) else ' _ '
            for part in node.values
        )
        for match in _SQL_DESTRUCTIVE_DROP.finditer(rendered):
            problems.append(f'line {node.lineno}: SQL DROP {match.group(1).upper()} needs CONTRACT_AFTER')
        for part in node.values:
            if isinstance(part, ast.FormattedValue):
                _collect_drop_problems(part.value, module_strings, problems)
        return
    if (text := _string(node)) is not None:
        for match in _SQL_DESTRUCTIVE_DROP.finditer(text):
            problems.append(f'line {node.lineno}: SQL DROP {match.group(1).upper()} needs CONTRACT_AFTER')
        return
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in module_strings:
        for match in _SQL_DESTRUCTIVE_DROP.finditer(module_strings[node.id]):
            problems.append(f'line {node.lineno}: SQL DROP {match.group(1).upper()} needs CONTRACT_AFTER')
        return
    for child in ast.iter_child_nodes(node):
        _collect_drop_problems(child, module_strings, problems)


def destructive_drop_problems(source: str) -> list[str]:
    """Drops of columns, tables, or functions in upgrade() without CONTRACT_AFTER.

    Deployments run `alembic upgrade head` before rolling out the new image, so
    dropping an object that the previous release still queries breaks blue-green
    overlap and rollback. Index drops do not break queries and are allowed.
    """
    tree = ast.parse(source)
    if _contract_after_marker(tree) is not None:
        return []
    module_strings = _module_string_constants(tree)
    problems: list[str] = []
    for fn in _upgrade_functions(tree):
        for stmt in _function_body_without_docstring(fn):
            _collect_drop_problems(stmt, module_strings, problems)
    return problems


def index_lock_problems(source: str) -> list[str]:
    """Indexes on existing tables built without CONCURRENTLY in an autocommit block.

    An index on a table the same revision creates is fine: nobody reads it yet.
    """
    tree = ast.parse(source)
    created, autocommit = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'create_table':
            if node.args and _string(node.args[0]):
                created.add(_string(node.args[0]))
        if text := _string(node):
            created.update(_SQL_TABLE.findall(text))
        if isinstance(node, ast.With) and any('autocommit_block' in ast.unparse(item.context_expr) for item in node.items):
            autocommit.update(id(inner) for inner in ast.walk(node))
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'create_index':
            keywords = {keyword.arg: keyword.value for keyword in node.keywords}
            table = _string(node.args[1]) if len(node.args) > 1 else _string(keywords.get('table_name'))
            if table in created:
                continue
            concurrent = keywords.get('postgresql_concurrently')
            if not (isinstance(concurrent, ast.Constant) and concurrent.value is True):
                problems.append(f'line {node.lineno}: create_index on {table} needs postgresql_concurrently=True')
            elif id(node) not in autocommit:
                problems.append(f'line {node.lineno}: concurrent create_index on {table} needs an autocommit_block')
        if text := _string(node):
            for concurrently, table in _SQL_INDEX.findall(text):
                if table in created:
                    continue
                if not concurrently:
                    problems.append(f'line {node.lineno}: CREATE INDEX on {table} needs CONCURRENTLY')
                elif id(node) not in autocommit:
                    problems.append(f'line {node.lineno}: CREATE INDEX CONCURRENTLY on {table} needs an autocommit_block')
    return problems


def test_new_revisions_build_indexes_without_locking_live_tables():
    script = ScriptDirectory.from_config(schema_migrations.alembic_config())
    checked = {revision.revision for revision in script.walk_revisions('base', LAST_UNCHECKED_REVISION)}
    problems = {}
    for revision in script.walk_revisions():
        if revision.revision in checked:
            continue
        with open(revision.path, encoding='utf-8') as source:
            found = index_lock_problems(source.read())
        if found:
            problems[revision.path] = found
    assert not problems, (
        'Build indexes on existing tables with op.create_index(..., postgresql_concurrently=True) '
        'inside `with op.get_context().autocommit_block():`. ' + repr(problems))


def test_index_check_accepts_only_concurrent_builds_or_new_tables():
    blocking = '''
def upgrade():
    op.create_index("ix_drafts_owner", "conversations", ["user_id"])
    op.execute("CREATE INDEX ix_jobs_state ON public.jobs (status)")
'''
    assert len(index_lock_problems(blocking)) == 2
    outside_autocommit = '''
def upgrade():
    op.create_index("ix", "conversations", ["user_id"], postgresql_concurrently=True)
'''
    assert index_lock_problems(outside_autocommit) == [
        'line 3: concurrent create_index on conversations needs an autocommit_block']
    safe = '''
def upgrade():
    op.create_table("notes", sa.Column("id", sa.String))
    op.create_index("ix_notes_id", "notes", ["id"])
    op.execute("CREATE TABLE anteroom_private.routes (id text); CREATE INDEX ix_routes ON anteroom_private.routes (id)")
    with op.get_context().autocommit_block():
        op.create_index("ix", "conversations", ["user_id"], postgresql_concurrently=True)
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix2 ON jobs (status)")
'''
    assert index_lock_problems(safe) == []


def test_new_revisions_require_contract_marker_for_destructive_drops():
    script = ScriptDirectory.from_config(schema_migrations.alembic_config())
    grandfathered = {revision.revision for revision in script.walk_revisions('base', LAST_UNCHECKED_DROP_REVISION)}
    problems = {}
    for revision in script.walk_revisions():
        if revision.revision in grandfathered:
            continue
        with open(revision.path, encoding='utf-8') as source:
            found = destructive_drop_problems(source.read())
        if found:
            problems[revision.path] = found
    assert not problems, (
        'Destructive drops in upgrade() must wait one release behind the code that stopped '
        'using the object (deploy/fly/README.md). Declare CONTRACT_AFTER = "#<pr>" naming '
        'that earlier PR or release: ' + repr(problems))


def test_destructive_drop_check_requires_contract_marker_in_upgrade():
    unmarked = '''
SIGNATURE = "anteroom_private.old_fn()"
DROP_SQL = "ALTER TABLE listings DROP COLUMN legacy_flag"

def upgrade():
    op.drop_column("listings", "obsolete")
    with op.batch_alter_table("marketplace_relist_rules") as batch_op:
        batch_op.drop_column("skip_active_offers")
    op.drop_table("scout_checks")
    op.execute(f"DROP FUNCTION IF EXISTS {SIGNATURE}")
    op.execute("DROP TABLE IF EXISTS stale_cache")
    op.execute(DROP_SQL)
'''
    assert destructive_drop_problems(unmarked) == [
        'line 6: drop_column(listings.obsolete) needs CONTRACT_AFTER',
        'line 8: drop_column(skip_active_offers) needs CONTRACT_AFTER',
        'line 9: drop_table(scout_checks) needs CONTRACT_AFTER',
        'line 10: SQL DROP FUNCTION needs CONTRACT_AFTER',
        'line 11: SQL DROP TABLE needs CONTRACT_AFTER',
        'line 12: SQL DROP COLUMN needs CONTRACT_AFTER',
    ]

    helper_drop = '''
def upgrade():
    _cleanup()

def _cleanup():
    op.drop_table("old_table")
'''
    assert destructive_drop_problems(helper_drop) == [
        'line 6: drop_table(old_table) needs CONTRACT_AFTER',
    ]

    blank_marker = '''
CONTRACT_AFTER = "   "

def upgrade():
    op.drop_table("old_table")
'''
    assert destructive_drop_problems(blank_marker) == [
        'line 5: drop_table(old_table) needs CONTRACT_AFTER',
    ]

    marked = '''
CONTRACT_AFTER = "#1959"

def upgrade():
    op.drop_table("scout_checks")
    op.execute("DROP FUNCTION IF EXISTS anteroom_private.old_fn()")
'''
    assert destructive_drop_problems(marked) == []

    annotated_marked = '''
CONTRACT_AFTER: str = "0.1.1258"

def upgrade():
    with op.batch_alter_table("listings") as batch_op:
        batch_op.drop_column("obsolete")
'''
    assert destructive_drop_problems(annotated_marked) == []

    safe = '''
"""Docstring mentioning DROP TABLE old_table is ignored."""

def upgrade():
    """Upgrade docstring mentioning DROP COLUMN foo is ignored."""
    op.add_column("listings", sa.Column("fresh", sa.String(), nullable=True))
    op.drop_index("ix_listings_old", table_name="listings")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_jobs_old")
    op.execute("DROP TRIGGER IF EXISTS account_write_guard ON public.listings")
    op.execute("DROP POLICY IF EXISTS old_policy ON listings")

def downgrade():
    op.drop_column("listings", "fresh")
    op.drop_table("brand_new_table")
    op.execute("DROP FUNCTION IF EXISTS anteroom_private.brand_new_fn()")
'''
    assert destructive_drop_problems(safe) == []


def test_a_deploy_migration_gives_up_on_a_held_table_lock(migration_engine, monkeypatch):  # noqa: F811
    if migration_engine.dialect.name != 'postgresql':
        pytest.skip('Lock timeouts apply to Postgres')
    monkeypatch.delenv('ANTEROOM_MIGRATION_LOCK_TIMEOUT', raising=False)
    schema_migrations.ensure_schema(migration_engine)
    config = migration_config(migration_engine)
    command.downgrade(config, LAST_UNCHECKED_REVISION)
    with migration_engine.connect() as holder:
        holder.execute(sa.text('LOCK TABLE alembic_version IN ACCESS EXCLUSIVE MODE'))
        started = time.monotonic()
        with pytest.raises(OperationalError, match='lock timeout'):
            command.upgrade(config, 'head')
        assert time.monotonic() - started < 30
        holder.rollback()
    command.upgrade(config, 'head')


def test_deploy_workflow_enables_migration_lock_retries():
    import yaml

    workflow = Path(__file__).resolve().parents[3] / '.github' / 'workflows' / 'deploy-hosted.yml'
    steps = yaml.safe_load(workflow.read_text(encoding='utf-8'))['jobs']['deploy']['steps']
    migrate = next(step['run'] for step in steps if step.get('name') == 'Apply pending migrations')
    assert 'python -m alembic -x retry_lock_timeouts=1 upgrade head' in migrate


@pytest.mark.parametrize('release_lock', [True, False], ids=['reader-finishes', 'reader-stays'])
def test_deploy_migration_lock_retries_are_bounded(migration_engine, monkeypatch, release_lock):  # noqa: F811
    if migration_engine.dialect.name != 'postgresql':
        pytest.skip('Lock timeouts apply to Postgres')
    config = migration_config(migration_engine)
    command.upgrade(config, '3f6a9d2c7b14')
    config.cmd_opts = Namespace(x=['retry_lock_timeouts=1'])
    monkeypatch.setenv('ANTEROOM_MIGRATION_LOCK_TIMEOUT', '100ms')
    waits = []

    with migration_engine.connect() as reader:
        reader.execute(sa.text('SELECT 1 FROM listings LIMIT 1'))

        def wait_for_reader(delay):
            waits.append(delay)
            if release_lock:
                reader.rollback()

        monkeypatch.setattr(time, 'sleep', wait_for_reader)
        if release_lock:
            command.upgrade(config, 'head')
        else:
            with pytest.raises(OperationalError, match='lock timeout'):
                command.upgrade(config, 'head')

    assert waits == ([10] if release_lock else [10, 20, 40])
    expected_revision = schema_migrations.head_revision() if release_lock else '3f6a9d2c7b14'
    assert schema_migrations.recorded_revision(migration_engine) == expected_revision
    columns = sa.inspect(migration_engine).get_columns('listings')
    assert ('scheduled_content_digest' in {column['name'] for column in columns}) == release_lock


def test_deploy_does_not_retry_after_an_alembic_autocommit(migration_engine, monkeypatch, tmp_path):  # noqa: F811
    if migration_engine.dialect.name != 'postgresql':
        pytest.skip('Lock timeouts apply to Postgres')
    (tmp_path / 'env.py').write_bytes((schema_migrations.migrations_dir() / 'env.py').read_bytes())
    versions = tmp_path / 'versions'
    versions.mkdir()
    (versions / 'first_revision.py').write_text(
        """from alembic import op

revision = 'first'
down_revision = None

def upgrade():
    with op.get_context().autocommit_block():
        op.execute('INSERT INTO committed_effects VALUES (1)')
    op.execute('ALTER TABLE busy_table ADD COLUMN new_value INTEGER')
""",
        encoding='utf-8',
    )
    config = migration_config(migration_engine)
    config.set_main_option('script_location', str(tmp_path))
    config.cmd_opts = Namespace(x=['retry_lock_timeouts=1'])
    monkeypatch.setenv('ANTEROOM_MIGRATION_LOCK_TIMEOUT', '100ms')
    waits = []
    monkeypatch.setattr(time, 'sleep', waits.append)

    with migration_engine.begin() as connection:
        connection.execute(sa.text('CREATE TABLE committed_effects (value INTEGER)'))
        connection.execute(sa.text('CREATE TABLE busy_table (value INTEGER)'))
    with migration_engine.connect() as reader:
        reader.execute(sa.text('SELECT 1 FROM busy_table LIMIT 1'))
        with pytest.raises(OperationalError, match='lock timeout'):
            command.upgrade(config, 'head')

    assert waits == []
    with migration_engine.connect() as connection:
        assert connection.scalar(sa.text('SELECT count(*) FROM committed_effects')) == 1


def _ready(app):
    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url='http://hosted') as client:
            return await client.get('/api/ready')
    return asyncio.run(run())


def test_ready_reports_a_database_at_another_schema_revision(migration_engine, monkeypatch):  # noqa: F811
    if migration_engine.dialect.name != 'postgresql':
        pytest.skip('Readiness checks the hosted database')
    schema_migrations.ensure_schema(migration_engine)
    monkeypatch.setattr('anteroom.database.engine', migration_engine)
    app = create_app()
    assert _ready(app).json() == {'status': 'ok'}
    with migration_engine.begin() as connection:
        connection.execute(sa.text("UPDATE alembic_version SET version_num = 'from_a_newer_build'"))
    stale = _ready(app)
    assert stale.status_code == 503
    assert stale.json() == {'status': 'schema_mismatch'}
