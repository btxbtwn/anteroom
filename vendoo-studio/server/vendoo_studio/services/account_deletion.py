"""Delete a hosted seller's Anteroom account at their request.

Removes the seller's records in every seller table, their photos and chat
images, their stored credentials (AI keys, eBay and Etsy sign-ins), their
relay registration and finally their Supabase Auth user. It never contacts a
marketplace listing: live listings stay on the marketplaces.

Every step can run again, so a failure part-way is finished by retrying.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import Table, select, text

from vendoo_studio.models.job import ACTIVE_JOB_STATUSES, Job
from vendoo_studio.models.marketplace_listing import UNSETTLED_STATES, MarketplaceListing
from vendoo_studio.services.hosted_database import SellerDatabaseSession

# Seller tables the restricted role may only read; anteroom_private.delete_account() clears them.
DELETED_BY_DATABASE = frozenset({'user_secret_references'})


class AccountDeletionRefused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def seller_tables() -> list[Table]:
    """Every table with a seller owner, children before parents."""
    from vendoo_studio.database import Base, load_models

    load_models()
    return [table for table in reversed(Base.metadata.sorted_tables)
            if 'user_id' in table.c and table.name not in DELETED_BY_DATABASE]


def _session(owner: str) -> SellerDatabaseSession:
    from vendoo_studio import database

    return SellerDatabaseSession(user_id=owner, bind=database.engine)


def _check(db: SellerDatabaseSession) -> None:
    if not db.scalar(text('SELECT anteroom_private.account_deletion_ready()')):
        raise AccountDeletionRefused(503, 'Account deletion is not available right now. Nothing was deleted. '
                                          'Contact Anteroom support to delete your account.')
    running = db.scalar(select(Job.id).where(Job.status.in_(ACTIVE_JOB_STATUSES)).limit(1))
    unsettled = db.scalar(select(MarketplaceListing.id).where(MarketplaceListing.state.in_(UNSETTLED_STATES)).limit(1))
    if running or unsettled:
        raise AccountDeletionRefused(409, 'A marketplace action is still running or waiting for its result. '
                                          'Let it finish, or check its result, then try again. Nothing was deleted.')


def _photo_keys(owner: str) -> list[str]:
    with _session(owner) as db:
        _check(db)
        keys = list(db.scalars(text('SELECT * FROM anteroom_private.account_photo_keys()')))
    # The function already scopes to this seller; never send another prefix to Storage.
    return [key for key in keys if key.startswith(owner + '/')]


def _delete_records(owner: str) -> None:
    with _session(owner) as db:
        # The account is going: no other device needs to hear about these deletes.
        db.info['quiet_changes'] = True
        db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))'),
                   {'identity': f'anteroom-account-delete:{owner}'})
        _check(db)
        for table in seller_tables():
            # Row security limits each delete to this seller; the filter says so too.
            db.execute(table.delete().where(table.c.user_id == owner))
        db.execute(text('SELECT anteroom_private.delete_account()'))
        db.commit()


async def _forget_relay_device() -> None:
    """Unlink eBay and Etsy from Anteroom's relay and drop its waiting events."""
    from vendoo_studio.services.keychain import get_relay_device
    from vendoo_studio.services.relay_client import relay_request

    if not await asyncio.to_thread(get_relay_device):
        return
    try:
        response = await relay_request('DELETE', '/studio/device')
    except Exception:  # noqa: BLE001 - offline relay: stop before deleting anything else
        response = None
    # 401: the relay no longer knows this device, which is the goal.
    if response is None or response.status_code not in {200, 401}:
        raise AccountDeletionRefused(503, "Could not disconnect eBay and Etsy from Anteroom's relay. "
                                          'Try again in a minute.')


async def delete_account(owner: str, token: str) -> None:
    from vendoo_studio.services.hosted_context import forget_catalog_state
    from vendoo_studio.services.hosted_photos import remove_objects

    keys = await asyncio.to_thread(_photo_keys, owner)
    await _forget_relay_device()
    await asyncio.to_thread(remove_objects, keys, token)
    await asyncio.to_thread(_delete_records, owner)
    forget_catalog_state(owner)
