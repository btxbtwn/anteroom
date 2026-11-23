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

_SQL_TABLE = re.compile(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:[\w"]+\.)?"?(\w+)"?', re.I)
_SQL_INDEX = re.compile(
    r'CREATE\s+(?:UNIQUE\s+)?INDEX\s+(CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?[\w"]+\s+ON\s+(?:ONLY\s+)?'
    r'(?:[\w"]+\.)?"?(\w+)"?', re.I)


def _string(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


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
