"""Postgres sessions for verified sellers, including explicitly scoped workers."""
from __future__ import annotations

import json
from uuid import UUID

from fastapi import HTTPException, Request
from sqlalchemy import event, func, inspect, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from vendoo_studio.models.ownership import LOCAL_USER_ID, SellerOwned
from vendoo_studio.models.seller_change import TOPIC_BY_TABLE, SellerChange
from vendoo_studio.services.seller_sessions import SellerIdentity


class SellerDatabaseSession(Session):
    def __init__(self, *, user_id: str, bind, **kwargs):
        self._seller_id = str(UUID(user_id))
        if self._seller_id == LOCAL_USER_ID:
            raise ValueError("Hosted sessions require an account owner")
        if bind.dialect.name != "postgresql":
            raise ValueError("Hosted seller isolation requires PostgreSQL")
        super().__init__(bind=bind, **kwargs)

    @property
    def user_id(self) -> str:
        return self._seller_id


def get_seller_db(request: Request):
    """Hosted route dependency: trust only the middleware's verified identity."""
    identity = getattr(request.state, "seller", None)
    if not isinstance(identity, SellerIdentity):
        raise HTTPException(status_code=401, detail="A verified seller session is required")
    from vendoo_studio.database import engine

    if engine.dialect.name != "postgresql":
        raise HTTPException(status_code=503, detail="The cloud database is not configured")
    with SellerDatabaseSession(user_id=identity.user_id, bind=engine) as db:
        try:
            yield db
        except SQLAlchemyError:
            db.rollback()
            raise HTTPException(status_code=503, detail="Cloud data is temporarily unavailable") from None


@event.listens_for(SellerDatabaseSession, "after_begin")
def _scope_transaction(session, transaction, connection):
    if transaction.nested:
        return
    # Use the transaction's connection, not Session.execute (which would
    # recurse). Both settings disappear on commit/rollback before pool reuse.
    # One statement, as PostgREST scopes a request: Postgres is a network hop
    # away, and each extra statement here is paid by every transaction.
    connection.execute(
        text("SELECT set_config('role', 'anteroom_seller', true), set_config('request.jwt.claims', :claims, true)"),
        {"claims": json.dumps({"sub": session.user_id})},
    )
    # The role and the row security it relies on are checked once per pooled
    # connection: a new connection is checked before its first seller query.
    if not connection.info.get("seller_isolation_checked"):
        _check_isolation(connection)
        connection.info["seller_isolation_checked"] = True


def _check_isolation(connection):
    unsafe = connection.execute(text("""
        SELECT rolsuper OR rolbypassrls OR rolinherit OR EXISTS (
            SELECT 1 FROM pg_class
            WHERE relowner = pg_roles.oid AND relnamespace = 'public'::regnamespace
        ) FROM pg_roles WHERE rolname = current_user
    """)).scalar_one()
    if unsafe:
        raise RuntimeError("Hosted database role could bypass seller isolation")
    from vendoo_studio.database import Base, load_models

    load_models()
    protected = [table.name for table in Base.metadata.tables.values() if "user_id" in table.c]
    installed = connection.execute(text("""
        SELECT count(DISTINCT relation.relname)
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        JOIN pg_policy AS policy ON policy.polrelid = relation.oid
        WHERE namespace.nspname = 'public' AND relation.relname = ANY(:tables)
            AND relation.relrowsecurity AND policy.polname = 'seller_owner'
            AND NOT policy.polpermissive
    """), {"tables": protected}).scalar_one()
    if installed != len(protected):
        raise RuntimeError("Hosted seller row security is not fully installed")


@event.listens_for(SellerDatabaseSession, "before_flush")
def _assign_owner(session, flush_context, instances):
    if (lease := session.info.get('background_lease')) is not None:
        from vendoo_studio.services.background_tasks import check_write_lease
        check_write_lease(session, *lease)
    for obj in session.new:
        if isinstance(obj, SellerOwned) and obj.user_id is None:
            obj.user_id = session.user_id
    for obj in session.new | session.dirty | session.deleted:
        if isinstance(obj, SellerOwned) and obj.user_id != session.user_id:
            raise ValueError("A seller session cannot write another owner's record")
    note_changes(session, *(
        inspect(obj).mapper.local_table.name for obj in session.new | session.deleted
    ), *(
        inspect(obj).mapper.local_table.name for obj in session.dirty if session.is_modified(obj)
    ))


# The change feed. Other devices subscribe to seller_changes (Supabase
# Realtime) and re-read what a topic covers. Topics are written in the same
# transaction as the data, so a device never hears about an uncommitted change.

def note_changes(session: Session, *tables: str) -> None:
    topics = {TOPIC_BY_TABLE.get(table) for table in tables} - {None}
    if topics:
        session.info.setdefault("changed_topics", set()).update(topics)


@event.listens_for(SellerDatabaseSession, "do_orm_execute")
def _note_statement(state):
    if state.is_insert or state.is_update or state.is_delete:
        note_changes(state.session, state.statement.table.name)


@event.listens_for(SellerDatabaseSession, "before_commit")
def _publish_changes(session):
    session.flush()
    topics = session.info.pop("changed_topics", None)
    # A Mac moving into the account writes in hundreds of batches; it
    # announces once when it finishes instead (account_import.finish).
    if not topics or session.info.get("quiet_changes"):
        return
    # Sorted so concurrent commits lock feed rows in the same order.
    rows = [{"user_id": session.user_id, "topic": topic, "changed_at": func.now()} for topic in sorted(topics)]
    statement = insert(SellerChange).values(rows)
    session.connection().execute(statement.on_conflict_do_update(
        index_elements=["user_id", "topic"], set_={"changed_at": statement.excluded.changed_at}))


@event.listens_for(SellerDatabaseSession, "after_soft_rollback")
def _forget_changes(session, previous_transaction):
    if previous_transaction.parent is None:
        session.info.pop("changed_topics", None)
