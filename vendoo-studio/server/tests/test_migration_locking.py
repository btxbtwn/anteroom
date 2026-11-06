"""Hosted migrations run on deploy against a live database; they must not stall it."""
from __future__ import annotations

import ast
import asyncio
import re
import time

import httpx
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory

from test_seller_ownership import migration_config, migration_engine  # noqa: F401
from vendoo_studio.hosted import create_app
from vendoo_studio.services import schema_migrations

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


def test_a_deploy_migration_gives_up_on_a_held_table_lock(migration_engine):  # noqa: F811
    if migration_engine.dialect.name != 'postgresql':
        pytest.skip('Lock timeouts apply to Postgres')
    schema_migrations.ensure_schema(migration_engine)
    config = migration_config(migration_engine)
    command.downgrade(config, LAST_UNCHECKED_REVISION)
    with migration_engine.connect() as holder:
        holder.execute(sa.text('LOCK TABLE alembic_version IN ACCESS EXCLUSIVE MODE'))
        started = time.monotonic()
        with pytest.raises(sa.exc.OperationalError, match='lock timeout'):
            command.upgrade(config, 'head')
        assert time.monotonic() - started < 30
        holder.rollback()
    command.upgrade(config, 'head')


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
    monkeypatch.setattr('vendoo_studio.database.engine', migration_engine)
    app = create_app()
    assert _ready(app).json() == {'status': 'ok'}
    with migration_engine.begin() as connection:
        connection.execute(sa.text("UPDATE alembic_version SET version_num = 'from_a_newer_build'"))
    stale = _ready(app)
    assert stale.status_code == 503
    assert stale.json() == {'status': 'schema_mismatch'}
