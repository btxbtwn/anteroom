"""Import Plaid deltas without overwriting seller changes or holding a DB slot during HTTP."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ValidationError
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from anteroom import database
from anteroom.models.business_expense import BusinessExpense
from anteroom.models.conversation import new_id
from anteroom.models.ownership import LOCAL_USER_ID
from anteroom.models.plaid import PlaidConnection, PlaidTransaction
from anteroom.services import keychain
from anteroom.services.business_expenses import Category, ExpenseInput, apply_input, round_cents
from anteroom.services.hosted_context import CredentialStorageUnavailable, VaultSecretStore, for_seller
from anteroom.services.hosted_database import SellerDatabaseSession
from anteroom.services.plaid_client import PlaidClient, PlaidError
from anteroom.services.service_context import current, use

MAX_CONNECTIONS = 2
HISTORY_DAYS = 730
logger = logging.getLogger(__name__)


class PlaidConflict(Exception):
    pass


class PlaidNotFound(Exception):
    pass


class AccountSummary(BaseModel):
    name: str
    mask: str | None = None
    type: str
    subtype: str | None = None


class ConnectionStatus(BaseModel):
    id: str
    institution_name: str
    accounts: list[AccountSummary]
    last_synced_at: datetime | None
    last_error: str | None
    has_cursor: bool
    status: Literal["waiting", "current", "error", "disconnecting"]


class PlaidStatus(BaseModel):
    configured: bool
    max_connections: int
    connections: list[ConnectionStatus]


def seller_id() -> str:
    context = current()
    return context.seller_id if context else LOCAL_USER_ID


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _slot(connection_id: str) -> str:
    return f"plaid:{connection_id}"


def _summary(row: PlaidConnection, client: PlaidClient | None) -> ConnectionStatus:
    error = row.last_error
    if client and client.environment != row.environment:
        error = "This connection belongs to a different Plaid environment."
    return ConnectionStatus(
        id=row.id, institution_name=row.institution_name,
        accounts=[AccountSummary.model_validate(account) for account in row.accounts],
        last_synced_at=row.last_synced_at.replace(tzinfo=UTC) if row.last_synced_at else None,
        last_error=error, has_cursor=bool(row.cursor),
        status="disconnecting" if row.disconnecting else "error" if error
        else "current" if row.history_complete else "waiting",
    )


def status(db: Session, client: PlaidClient | None) -> PlaidStatus:
    rows = db.scalars(select(PlaidConnection).order_by(PlaidConnection.institution_name))
    return PlaidStatus(configured=client is not None, max_connections=MAX_CONNECTIONS,
                       connections=[_summary(row, client) for row in rows])


def _require_capacity(db: Session) -> None:
    if len(list(db.scalars(select(PlaidConnection.id).limit(MAX_CONNECTIONS)))) >= MAX_CONNECTIONS:
        raise PlaidConflict("Disconnect a bank or card before connecting another institution.")


def _lock_seller(db: Session) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                   {"key": f"anteroom-plaid:{seller_id()}"})
    else:
        db.execute(text("BEGIN IMMEDIATE"))


def _connection(db: Session, connection_id: str) -> PlaidConnection:
    row = db.get(PlaidConnection, connection_id)
    if row is None:
        raise PlaidNotFound("Plaid connection not found.")
    return row


async def link_token(client: PlaidClient, redirect_uri: str | None) -> str:
    await database.in_worker_session(_require_capacity)
    payload = await client.post(
        "/link/token/create", user={"client_user_id": seller_id()}, client_name="Anteroom",
        country_codes=["US", "CA"], language="en", products=["transactions"],
        account_filters={"depository": {"account_subtypes": ["all"]},
                         "credit": {"account_subtypes": ["credit card"]}},
        transactions={"days_requested": HISTORY_DAYS},
        **({"redirect_uri": redirect_uri} if redirect_uri else {}),
    )
    return _required_string(payload, "link_token")


def _required_string(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PlaidError()
    return value


def _vault() -> VaultSecretStore | None:
    context = current()
    return context.secrets if context and isinstance(context.secrets, VaultSecretStore) else None


def _insert_connection(db: Session, row: PlaidConnection) -> None:
    _lock_seller(db)
    _require_capacity(db)
    if db.scalar(select(PlaidConnection.id).where(PlaidConnection.institution_id == row.institution_id)):
        raise PlaidConflict("This institution is already connected.")
    db.add(row)


def _save_connection(row: PlaidConnection, token: str) -> None:
    slot = _slot(row.id)
    vault = _vault()
    if vault is not None:
        with database.SessionLocal() as db:
            assert isinstance(db, SellerDatabaseSession)
            _insert_connection(db, row)
            db.flush()  # Enforce the account deletion fence before writing Vault.
            vault.write_in_session(db, slot, token)
            db.commit()
        return
    try:
        if not keychain.write_slot(slot, token):
            raise CredentialStorageUnavailable()
        with database.SessionLocal() as db:
            _insert_connection(db, row)
            db.commit()
    except Exception:
        keychain.delete_slot(slot)
        raise


async def _revoke(client: PlaidClient, token: str) -> None:
    try:
        await client.post("/item/remove", access_token=token)
    except PlaidError:
        logger.warning("Plaid could not revoke an Item")


async def exchange(client: PlaidClient, public_token: str, institution_name: str) -> ConnectionStatus:
    await database.in_worker_session(_require_capacity)
    exchanged = await client.post("/item/public_token/exchange", public_token=public_token)
    token = _required_string(exchanged, "access_token")
    connection_id = new_id()
    try:
        payload = await client.post("/accounts/get", access_token=token)
        try:
            institution_id = _required_string(payload["item"], "institution_id")
            accounts = [AccountSummary.model_validate(account).model_dump() for account in payload["accounts"]]
        except (KeyError, TypeError, ValidationError):
            raise PlaidError() from None
    except (Exception, asyncio.CancelledError):
        await _revoke(client, token)
        raise
    row = PlaidConnection(id=connection_id, institution_id=institution_id, institution_name=institution_name,
                          environment=client.environment, accounts=accounts)

    async def register() -> None:
        try:
            await database.run_worker_db_step(_save_connection, row, token)
        except Exception:
            await _revoke(client, token)
            raise

    registration = asyncio.create_task(register())
    try:
        await asyncio.shield(registration)
    except asyncio.CancelledError:
        await asyncio.gather(registration, return_exceptions=True)
        raise
    return await sync(client, connection_id)


@dataclass
class Delta:
    cursor: str | None
    changes: dict[str, dict | None]
    history_complete: bool = False


async def _delta(client: PlaidClient, token: str, cursor: str | None) -> Delta:
    # Plaid requires restarting from the original cursor if transactions mutate mid-pagination.
    async with asyncio.timeout(120):
        for attempt in range(3):
            result = Delta(cursor, {})
            try:
                while True:
                    body = {"cursor": result.cursor} if result.cursor is not None else {}
                    page = await client.post("/transactions/sync", access_token=token, count=500, **body)
                    for kind in ("added", "modified", "removed"):
                        for row in page[kind]:
                            key = _required_string(row, "transaction_id")
                            result.changes[key] = None if kind == "removed" else row
                    next_cursor = page["next_cursor"]
                    if not isinstance(next_cursor, str):
                        raise PlaidError()
                    if not page["has_more"]:
                        result.cursor = next_cursor or None
                        update = page.get("transactions_update_status")
                        result.history_complete = update == "HISTORICAL_UPDATE_COMPLETE" or (update is None and bool(next_cursor))
                        return result
                    if not next_cursor or next_cursor == result.cursor:
                        raise PlaidError()
                    result.cursor = next_cursor
            except PlaidError as exc:
                if exc.code != "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION" or attempt == 2:
                    raise
            except (KeyError, TypeError, AttributeError):
                raise PlaidError() from None
    raise PlaidError()


def _category(detail: str, vendor: str) -> Category:
    merchant = vendor.casefold()
    if any(name in merchant for name in ("pirate ship", "usps", "fedex", "united parcel service", "canada post")):
        return "shipping_labels"
    if "uline" in merchant or "staples" in merchant:
        return "shipping_supplies"
    if detail.startswith("GENERAL_MERCHANDISE_") and detail.endswith(("_CLOTHING_AND_ACCESSORIES", "_THRIFT_STORES")):
        return "inventory_purchases"
    if any(name in merchant for name in ("vendoo", "list perfectly", "adobe", "quickbooks")):
        return "software_subscriptions"
    if "advertis" in merchant or merchant.startswith(("meta ads", "google ads")):
        return "advertising"
    if detail.startswith("TRANSPORTATION_"):
        return "sourcing_transportation"
    if detail.endswith("_BANK_FEES") or detail.startswith("BANK_FEES_"):
        return "platform_fees"
    if detail.endswith("_OFFICE_SUPPLIES"):
        return "home_office"
    return "other"


def _expense(row: dict) -> ExpenseInput | None:
    finance = row.get("personal_finance_category") or {}
    primary = finance.get("primary", "")
    if row.get("pending") or primary in {"INCOME", "TRANSFER_IN", "TRANSFER_OUT", "LOAN_PAYMENTS"}:
        return None
    if any(term in str(row.get("name", "")).casefold() for term in ("credit card payment", "autopay payment")):
        return None
    currency = row.get("iso_currency_code")
    if not currency:
        return None
    amount = round_cents(Decimal(str(row["amount"])) * 100)
    if amount <= 0:
        return None
    vendor = str(row.get("merchant_name") or row["name"])
    return ExpenseInput(incurred_date=row["date"], amount_cents=amount, currency=currency,
                        vendor=vendor[:256], description=str(row["name"])[:512],
                        category=_category(finance.get("detailed") or "", vendor))


def _import_key(environment: str, transaction_id: str) -> str:
    return sha256(f"plaid:{environment}:{transaction_id}".encode()).hexdigest()


def _reconcile(db: Session, environment: str, delta: Delta) -> None:
    changes = {_import_key(environment, key): _expense(row) if row else None for key, row in delta.changes.items()}
    keys = list(changes)
    # Work in chunks within one write transaction (including the cursor).
    for start in range(0, len(keys), 500):
        batch = keys[start:start + 500]
        tracking = {row.import_key: row for row in db.scalars(
            select(PlaidTransaction).where(PlaidTransaction.import_key.in_(batch)),
        )}
        expenses = {row.import_key: row for row in db.scalars(
            select(BusinessExpense).where(BusinessExpense.import_key.in_(batch)).with_for_update(),
        )}
        written = []
        for key in batch:
            record = tracking.get(key)
            expense = expenses.get(key)
            # Missing after a successful import means seller deletion. A
            # revision mismatch (or an adopted record) means seller ownership.
            if expense is None and record is not None and record.imported_revision is not None:
                continue
            if expense is not None and (record is None or expense.revision != record.imported_revision):
                continue
            body = changes[key]
            if body is None:
                if expense is not None and record is not None:
                    db.delete(expense)
                    record.imported_revision = None
                continue
            if record is None:
                record = PlaidTransaction(import_key=key)
                db.add(record)
            if expense is None:
                expense = BusinessExpense(import_source="plaid", import_key=key)
                db.add(expense)
            apply_input(expense, body)
            written.append((record, expense))
        db.flush()
        for record, expense in written:
            record.imported_revision = expense.revision


def _snapshot(db: Session, connection_id: str) -> tuple[str, int, str | None, bool]:
    row = _connection(db, connection_id)
    return row.environment, row.sync_version, row.cursor, row.disconnecting


def _finish_sync(db: Session, connection_id: str, client: PlaidClient, version: int,
                 delta: Delta | None, error: str | None) -> ConnectionStatus:
    _lock_seller(db)
    row = _connection(db, connection_id)
    if not row.disconnecting and row.sync_version == version:
        if delta is not None:
            _reconcile(db, client.environment, delta)
            row.cursor = delta.cursor
            row.history_complete = row.history_complete or delta.history_complete
            if delta.cursor:
                row.last_synced_at = _now()
            row.sync_version += 1
        row.last_attempted_at = _now()
        row.last_error = error
        result = _summary(row, client)
        db.commit()
        return result
    return _summary(row, client)


async def sync(client: PlaidClient, connection_id: str) -> ConnectionStatus:
    environment, version, cursor, disconnecting = await database.in_worker_session(_snapshot, connection_id)
    if disconnecting or environment != client.environment:
        raise PlaidConflict("This connection cannot sync. Check the Plaid environment or finish disconnecting.")
    delta = None
    error = None
    try:
        token = await database.run_worker_db_step(keychain.read_slot, _slot(connection_id))
        if not token:
            error = "The Plaid credential is missing. Disconnect this institution and connect it again."
        else:
            delta = await _delta(client, token, cursor)
    except PlaidError as exc:
        if exc.code != "PRODUCT_NOT_READY":
            error = str(exc)
    except CredentialStorageUnavailable as exc:
        error = str(exc)
    except TimeoutError:
        error = "Plaid transaction sync timed out. Try again shortly."
    try:
        return await database.in_worker_session(_finish_sync, connection_id, client, version, delta, error)
    except (ArithmeticError, KeyError, ValueError, TypeError, AttributeError):
        return await database.in_worker_session(_finish_sync, connection_id, client, version, None,
                                                "Plaid sent transaction data Anteroom could not read.")


def _begin_disconnect(db: Session, connection_id: str) -> str:
    _lock_seller(db)
    row = _connection(db, connection_id)
    row.disconnecting = True
    environment = row.environment
    db.commit()
    return environment


def _finish_disconnect(connection_id: str) -> None:
    vault = _vault()
    if vault is None and not keychain.delete_slot(_slot(connection_id)):
        raise CredentialStorageUnavailable()
    with database.SessionLocal() as db:
        _lock_seller(db)
        if (row := db.get(PlaidConnection, connection_id)) is not None:
            if vault is not None:
                assert isinstance(db, SellerDatabaseSession)
                vault.write_in_session(db, _slot(connection_id), None)
            db.delete(row)
        db.commit()


async def disconnect(client: PlaidClient | None, connection_id: str) -> None:
    environment = await database.in_worker_session(_begin_disconnect, connection_id)
    if client and client.environment == environment:
        token = await database.run_worker_db_step(keychain.read_slot, _slot(connection_id))
        if token:
            await _revoke(client, token)
    await database.run_worker_db_step(_finish_disconnect, connection_id)


def _due(db: Session, environment: str) -> list[str]:
    now = _now()
    return list(db.scalars(select(PlaidConnection.id).where(
        PlaidConnection.environment == environment,
        ~PlaidConnection.disconnecting,
        or_(PlaidConnection.last_attempted_at.is_(None),
            PlaidConnection.last_attempted_at < now - timedelta(hours=1),
            ~PlaidConnection.history_complete & (PlaidConnection.last_attempted_at < now - timedelta(minutes=15))),
    )))


def _owners() -> list[str]:
    with database.engine.connect() as connection:
        return list(connection.scalars(text("SELECT anteroom_private.plaid_owners()")))


async def serve(*, hosted: bool = False) -> None:
    """Schedule due Items in the desktop app or the elected hosted worker."""
    async def check(work: Awaitable[Any]) -> None:
        try:
            await work
        except PlaidNotFound:
            pass  # Disconnected during the sweep.
        except Exception:  # noqa: BLE001 - isolate one Item and retry next sweep
            logger.warning("Plaid background sync could not finish")

    async def check_seller(client: PlaidClient) -> None:
        ids = await database.in_worker_session(_due, client.environment)
        await asyncio.gather(*(check(sync(client, connection_id)) for connection_id in ids))

    while True:
        await asyncio.sleep(60)
        client = PlaidClient.configured()
        if client is None:
            continue
        if not hosted:
            await check(check_seller(client))
            continue
        try:
            owners = await database.run_worker_db_step(_owners)
        except Exception:  # noqa: BLE001 - retry discovery on the next tick
            logger.warning("Plaid background sync could not find due sellers")
            continue

        async def run_seller(owner: str, client: PlaidClient) -> None:
            with use(for_seller(owner)):
                await check_seller(client)

        # Limit scheduler resource use without holding a DB permit during HTTP.
        for start in range(0, len(owners), 8):
            await asyncio.gather(*(check(run_seller(owner, client))
                                   for owner in owners[start:start + 8]))
