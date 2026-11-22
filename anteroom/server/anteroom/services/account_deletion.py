"""Delete a hosted seller's Anteroom account at their request.

Removes the seller's records in every seller table, their photos and chat
images, their stored credentials (including bank connections), their
relay registration and finally their Supabase Auth user. It never contacts a
marketplace listing: live listings stay on the marketplaces.

Every step can run again, so a failure part-way is finished by retrying.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import wraps
import time

from sqlalchemy import Table, select, text
from sqlalchemy.exc import DBAPIError

from anteroom.models.job import ACTIVE_JOB_STATUSES, Job
from anteroom.models.marketplace_listing import UNSETTLED_STATES, MarketplaceListing
from anteroom.models.plaid import PlaidConnection
from anteroom.services.hosted_database import SellerDatabaseSession
from anteroom.services.plaid_client import PlaidClient

# Seller tables the restricted role may only read; anteroom_private.delete_account() clears them.
DELETED_BY_DATABASE = frozenset({'user_secret_references'})


class AccountDeletionRefused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def seller_tables() -> list[Table]:
    """Every table with a seller owner, children before parents."""
    from anteroom.database import Base, load_models

    load_models()
    return [table for table in reversed(Base.metadata.sorted_tables)
            if 'user_id' in table.c and table.name not in DELETED_BY_DATABASE]


def _session(owner: str) -> SellerDatabaseSession:
    from anteroom import database

    return SellerDatabaseSession(user_id=owner, bind=database.engine)


def _check(db: SellerDatabaseSession, *, finishing: bool = False) -> None:
    unchanged = "" if finishing else " Nothing was deleted."
    if not db.scalar(text('SELECT anteroom_private.account_deletion_ready()')):
        raise AccountDeletionRefused(503, 'Account deletion is not available right now.' + unchanged + ' '
                                          'Contact Anteroom support to delete your account.')
    running = db.scalar(select(Job.id).where(Job.status.in_(ACTIVE_JOB_STATUSES)).limit(1))
    unsettled = db.scalar(select(MarketplaceListing.id).where(MarketplaceListing.state.in_(UNSETTLED_STATES)).limit(1))
    if running or unsettled:
        raise AccountDeletionRefused(409, 'A marketplace action is still running or waiting for its result. '
                                          'Let it finish, or check its result, then try again.' + unchanged)


def _retry_deadlocks[**Args, Result](operation: Callable[Args, Result]) -> Callable[Args, Result]:
    @wraps(operation)
    def run(*args: Args.args, **kwargs: Args.kwargs) -> Result:
        for attempt in range(3):
            try:
                return operation(*args, **kwargs)
            except DBAPIError as exc:
                if getattr(exc.orig, 'sqlstate', None) != '40P01' or attempt == 2:
                    raise
                # The session context has rolled back and closed before retrying.
                # Reacquire the fence and recheck safety/cleanup receipts in full.
                time.sleep(0.05 * (attempt + 1))
        raise AssertionError('unreachable')
    return run


def _lock_deletion(db: SellerDatabaseSession, owner: str) -> None:
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))'),
               {'identity': f'anteroom-account-delete:{owner}'})


@_retry_deadlocks
def _stage_plaid_cleanup(owner: str, environment: str) -> list[str]:
    with _session(owner) as db:
        _lock_deletion(db, owner)
        _check(db)
        db.execute(text('SELECT * FROM anteroom_private.stage_account_deletion()'))
        ids = list(db.scalars(select(PlaidConnection.id).where(
            PlaidConnection.user_id == owner,
            PlaidConnection.environment == environment,
        )))
        db.commit()
    return ids


@_retry_deadlocks
def _delete_records(owner: str) -> list[str]:
    with _session(owner) as db:
        # The account is going: no other device needs to hear about these deletes.
        db.info['quiet_changes'] = True
        _lock_deletion(db, owner)
        _check(db)
        # Collect Storage cleanup targets under the lock before removing database records.
        # The function already scopes to this seller; never send another prefix to Storage.
        raw_keys = list(db.scalars(text('SELECT * FROM anteroom_private.stage_account_deletion()')))
        keys = [key for key in raw_keys if key.startswith(owner + '/')]
        for table in seller_tables():
            # Row security limits each delete to this seller; the filter says so too.
            db.execute(table.delete().where(table.c.user_id == owner))
        # Keep Vault credentials and Auth until every external cleanup succeeds.
        db.commit()
    return keys


def _read_relay_device() -> dict | None:
    from anteroom.services.keychain import get_relay_device

    # A failed credential read is not proof there is no relay registration.
    return get_relay_device()


@_retry_deadlocks
def _finish_deletion(owner: str) -> None:
    with _session(owner) as db:
        db.info['quiet_changes'] = True
        _lock_deletion(db, owner)
        _check(db, finishing=True)
        db.execute(text('SELECT anteroom_private.finish_account_deletion()'))
        db.commit()


async def _forget_relay_device(device: dict | None = None) -> None:
    """Unlink eBay and Etsy from Anteroom's relay and drop its waiting events."""
    from anteroom.services.keychain import get_relay_device
    from anteroom.services.relay_client import relay_request

    target = device if device is not None else await asyncio.to_thread(get_relay_device)
    if not target:
        return
    try:
        response = await relay_request('DELETE', '/studio/device', device=target)
    except Exception:  # noqa: BLE001 - offline relay
        response = None
    # 401: the relay no longer knows this device, which is the goal.
    if response is None or response.status_code not in {200, 401}:
        raise AccountDeletionRefused(503, "Could not disconnect eBay and Etsy from Anteroom's relay. "
                                          'Try again in a minute.')


async def delete_account(owner: str, token: str) -> None:
    from anteroom.services.hosted_context import CredentialStorageUnavailable, forget_catalog_state
    from anteroom.services.hosted_photos import remove_objects

    # Capture cleanup keys under the locked active-job check. The durable
    # receipt survives deletion of listing rows and a restart of this request.
    # Auth and Vault remain available to a fresh retry until cleanup succeeds.
    relay_device = await asyncio.to_thread(_read_relay_device)
    if plaid := PlaidClient.configured():
        from anteroom.services.plaid_expenses import revoke_hosted_items

        # Fence new connections before revoking; keep the rows and credentials
        # until this step returns so an interrupted attempt can be repeated.
        ids = await asyncio.to_thread(_stage_plaid_cleanup, owner, plaid.environment)
        try:
            await revoke_hosted_items(plaid, owner, ids)
        except CredentialStorageUnavailable:
            raise AccountDeletionRefused(503, 'Could not read your bank connection credentials. '
                                              'Try deleting your account again in a minute.') from None
    keys = await asyncio.to_thread(_delete_records, owner)
    forget_catalog_state(owner)
    if relay_device:
        await _forget_relay_device(relay_device)
    if keys:
        try:
            await asyncio.to_thread(remove_objects, keys, token)
        except Exception as exc:
            raise AccountDeletionRefused(503, 'Could not finish removing your photos. '
                                              'Try deleting your account again in a minute.') from exc
    await asyncio.to_thread(_finish_deletion, owner)
