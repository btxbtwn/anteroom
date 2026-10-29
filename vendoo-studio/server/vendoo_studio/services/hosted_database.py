"""Postgres sessions for verified sellers, including explicitly scoped workers."""
from __future__ import annotations

import json
from uuid import UUID

from fastapi import HTTPException, Request
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from vendoo_studio.models.ownership import LOCAL_USER_ID, SellerOwned
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

    with SellerDatabaseSession(user_id=identity.user_id, bind=engine) as db:
        yield db


@event.listens_for(SellerDatabaseSession, "after_begin")
def _scope_transaction(session, transaction, connection):
    if transaction.nested:
        return
    # Use the transaction's connection, not Session.execute (which would
    # recurse). Both settings disappear on commit/rollback before pool reuse.
    connection.execute(text("SET LOCAL ROLE anteroom_seller"))
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
    connection.execute(
        text("SELECT set_config('request.jwt.claims', :claims, true)"),
        {"claims": json.dumps({"sub": session.user_id})},
    )


@event.listens_for(SellerDatabaseSession, "before_flush")
def _assign_owner(session, flush_context, instances):
    for obj in session.new:
        if isinstance(obj, SellerOwned) and obj.user_id is None:
            obj.user_id = session.user_id
    for obj in session.new | session.dirty | session.deleted:
        if isinstance(obj, SellerOwned) and obj.user_id != session.user_id:
            raise ValueError("A seller session cannot write another owner's record")
