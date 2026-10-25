"""Direct eBay and Etsy sign-in, through Anteroom's relay.

eBay and Etsy only send a sign-in back to an HTTPS address, and eBay's code
exchange needs the app's Cert ID, so the relay hosts the callback and hands
this Mac the tokens once. They live in the Keychain from then on. Refreshes go
through the relay for the same reason; Etsy API calls go through it because
every one needs the app's shared secret.

Signing in links the account to this Mac on the relay, which is what routes
that account's sales here.
"""
from __future__ import annotations

import asyncio
import logging
import time
import webbrowser
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from vendoo_studio.services import relay_client
from vendoo_studio.services.keychain import (
    delete_marketplace_account,
    delete_relay_device,
    get_marketplace_accounts,
    get_relay_device,
    set_marketplace_account,
)
from vendoo_studio.services.relay_client import RelayError, relay_request

log = logging.getLogger("vendoo_studio.marketplace_accounts")

MARKETPLACES = ("ebay", "etsy")
NAMES = {
    "ebay": "eBay",
    "etsy": "Etsy",
    "depop": "Depop",
    "poshmark": "Poshmark",
    "mercari": "Mercari",
    "sellwild": "Sellwild",
}
SIGN_IN_TIMEOUT_S = 10 * 60
POLL_INTERVAL_S = 2
# Refresh a little before the marketplace says the token expires.
REFRESH_MARGIN_S = 120


@dataclass
class PendingSignIn:
    marketplace: str
    state: str
    authorize_url: str
    started_at: float = field(default_factory=time.time)
    error: str | None = None
    done: bool = False
    task: asyncio.Task | None = None


_pending: dict[str, PendingSignIn] = {}


def _check(marketplace: str) -> str:
    if marketplace not in MARKETPLACES:
        raise ValueError(f"Unknown marketplace: {marketplace}")
    return marketplace


def _account_record(result: dict) -> dict:
    tokens = result.get("tokens") or {}
    now = time.time()
    expires_in = tokens.get("expiresIn")
    refresh_in = tokens.get("refreshTokenExpiresIn")
    return {
        "accountId": result.get("accountId"),
        "label": result.get("username") or result.get("shopName"),
        "userId": result.get("userId"),
        "accessToken": tokens.get("accessToken"),
        "refreshToken": tokens.get("refreshToken"),
        "expiresAt": now + expires_in if expires_in else None,
        "refreshExpiresAt": now + refresh_in if refresh_in else None,
        "connectedAt": now,
    }


async def _wait_for_sign_in(pending: PendingSignIn) -> None:
    deadline = pending.started_at + SIGN_IN_TIMEOUT_S
    name = NAMES[pending.marketplace]
    try:
        while time.time() < deadline:
            await asyncio.sleep(POLL_INTERVAL_S)
            resp = await relay_request("GET", "/studio/oauth/result", params={"state": pending.state})
            if resp.status_code == 202:
                continue
            if resp.status_code == 404:
                pending.error = f"The {name} sign-in expired. Try again."
                return
            if resp.status_code != 200:
                continue
            result = resp.json()
            if result.get("error"):
                pending.error = result["error"]
                return
            set_marketplace_account(pending.marketplace, _account_record(result))
            for warning in result.get("warnings") or []:
                log.warning("%s sign-in: %s", name, warning)
            start_listening()
            return
        pending.error = f"The {name} sign-in timed out. Try again."
    except asyncio.CancelledError:
        pending.error = "Sign-in cancelled"
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced in status
        log.warning("%s sign-in failed: %s", name, exc)
        pending.error = f"{name} sign-in failed. Try again."
    finally:
        pending.done = True


async def start_sign_in(marketplace: str, features: list[str] | None = None) -> dict:
    """Open the marketplace's sign-in page; finish in the background."""
    _check(marketplace)
    previous = _pending.pop(marketplace, None)
    if previous and previous.task and not previous.task.done():
        previous.task.cancel()
    resp = await relay_request("POST", f"/studio/oauth/{marketplace}/start", **({"params": {"features": ",".join(features)}} if features else {}))
    if resp.status_code == 503:
        raise RelayError(f"{NAMES[marketplace]} sign-in isn't set up on Anteroom's relay yet.", 503)
    if resp.status_code != 200:
        raise RelayError(f"Could not start {NAMES[marketplace]} sign-in ({resp.status_code}).", resp.status_code)
    body = resp.json()
    pending = PendingSignIn(marketplace=marketplace, state=body["state"], authorize_url=body["authorizeUrl"])
    pending.task = asyncio.get_running_loop().create_task(_wait_for_sign_in(pending))
    _pending[marketplace] = pending
    try:
        webbrowser.open(pending.authorize_url)
    except Exception:  # noqa: BLE001 - the page shows the link too
        log.debug("could not open %s sign-in", marketplace, exc_info=True)
    return {"authorizeUrl": pending.authorize_url}


def cancel_sign_in(marketplace: str) -> None:
    pending = _pending.pop(_check(marketplace), None)
    if pending and pending.task and not pending.task.done():
        pending.task.cancel()


async def disconnect(marketplace: str) -> None:
    """Unlink the account on the relay and forget its tokens here."""
    _check(marketplace)
    cancel_sign_in(marketplace)
    account = get_marketplace_accounts().get(marketplace)
    if account and account.get("accountId") and get_relay_device():
        try:
            await relay_request("DELETE", f"/studio/accounts/{marketplace}/{account['accountId']}")
        except Exception as exc:  # noqa: BLE001 - forget locally regardless
            log.warning("could not unlink %s on the relay: %s", marketplace, exc)
    delete_marketplace_account(marketplace)
    if not get_marketplace_accounts():
        relay_client.stop_stream()


async def forget_device() -> None:
    """Remove this Mac from the relay entirely: links, inbox, token."""
    for marketplace in MARKETPLACES:
        cancel_sign_in(marketplace)
    relay_client.stop_stream()
    if get_relay_device():
        try:
            await relay_request("DELETE", "/studio/device")
        except Exception as exc:  # noqa: BLE001
            log.warning("could not remove this Mac from the relay: %s", exc)
    for marketplace in MARKETPLACES:
        delete_marketplace_account(marketplace)
    delete_relay_device()


def forget_deleted_ebay_account(user_id: str | None) -> None:
    """eBay said this account was closed: drop its tokens (the relay already unlinked it)."""
    account = get_marketplace_accounts().get("ebay")
    if user_id and account and account.get("accountId") == user_id:
        log.warning("eBay reported the connected account closed; forgetting it")
        delete_marketplace_account("ebay")


class SignInRequired(RuntimeError):
    pass


_refresh_locks: dict[tuple[asyncio.AbstractEventLoop, str], asyncio.Lock] = {}


def _refresh_lock(marketplace: str) -> asyncio.Lock:
    """One refresh per marketplace at a time. Etsy rotates its refresh token, so
    a second refresh sent with the old one would sign the seller out."""
    loop = asyncio.get_running_loop()
    lock = _refresh_locks.get((loop, marketplace))
    if lock is None:
        for key in [key for key in _refresh_locks if key[0] is not loop]:
            del _refresh_locks[key]
        lock = _refresh_locks[(loop, marketplace)] = asyncio.Lock()
    return lock


def relay_refused(resp) -> bool:
    """The relay itself refused this Mac, as opposed to the marketplace refusing the seller."""
    if resp.status_code != 401:
        return False
    try:
        return resp.json() == {"error": "unauthorized"}
    except ValueError:
        return False


RELAY_REFUSED = "Anteroom's relay did not recognize this Mac. Quit and reopen Anteroom, then try again."


def _fresh(account: dict, rejected: str | None) -> bool:
    expires_at = account.get("expiresAt")
    return bool(expires_at and expires_at - REFRESH_MARGIN_S > time.time() and account.get("accessToken") != rejected)


async def access_token(marketplace: str, *, rejected: str | None = None) -> str:
    """A current access token, refreshed through the relay when it is about to expire.

    ``rejected`` is a token the marketplace just refused: refresh even though it
    has not expired, unless another caller already replaced it.
    """
    account = get_marketplace_accounts().get(_check(marketplace))
    if not account or not account.get("accessToken"):
        raise SignInRequired(f"{NAMES[marketplace]} isn't connected.")
    if _fresh(account, rejected):
        return account["accessToken"]
    async with _refresh_lock(marketplace):
        # Whoever held the lock may have refreshed already; use what it stored.
        account = get_marketplace_accounts().get(marketplace)
        if not account or not account.get("accessToken"):
            raise SignInRequired(f"{NAMES[marketplace]} isn't connected.")
        if _fresh(account, rejected):
            return account["accessToken"]
        if not account.get("refreshToken"):
            raise SignInRequired(f"Sign in to {NAMES[marketplace]} again.")
        resp = await relay_request(
            "POST", f"/studio/oauth/{marketplace}/refresh", json_body={"refreshToken": account["refreshToken"]},
        )
        if relay_refused(resp):
            raise RelayError(RELAY_REFUSED, 401)
        if resp.status_code == 401:
            raise SignInRequired(f"Sign in to {NAMES[marketplace]} again.")
        if resp.status_code != 200:
            raise RelayError(f"Could not refresh the {NAMES[marketplace]} sign-in ({resp.status_code}).", resp.status_code)
        current = get_marketplace_accounts().get(marketplace)
        if not current or any(current.get(key) != account.get(key) for key in ("accountId", "connectedAt", "refreshToken")):
            # Disconnect or a new sign-in during the request revokes this refresh.
            # Never recreate the connection using a response for its old grant.
            raise SignInRequired(f"The {NAMES[marketplace]} connection changed. Sign in again.")
        tokens = resp.json().get("tokens") or {}
        now = time.time()
        account = {
            **account,
            "accessToken": tokens.get("accessToken"),
            # eBay keeps its refresh token; Etsy issues a new one each time.
            "refreshToken": tokens.get("refreshToken") or account["refreshToken"],
            "expiresAt": now + tokens["expiresIn"] if tokens.get("expiresIn") else None,
        }
        set_marketplace_account(marketplace, account)
        return account["accessToken"]


async def etsy_api(method: str, path: str, **kwargs: Any):
    """Call Etsy's API (``path`` under /v3/application/) through the relay.

    A 401 from Etsy means the call was refused before it ran, so it is sent once
    more with a refreshed token.
    """
    async def send(token: str):
        for upload in (kwargs.get("files") or {}).values():
            handle = upload[1] if isinstance(upload, tuple) else None
            if hasattr(handle, "seek"):
                handle.seek(0)
        return await relay_request(
            method,
            f"/studio/etsy/v3/application/{path.lstrip('/')}",
            headers={"X-Etsy-Authorization": f"Bearer {token}"},
            **kwargs,
        )

    token = await access_token("etsy")
    resp = await send(token)
    if resp.status_code == 401 and not relay_refused(resp):
        resp = await send(await access_token("etsy", rejected=token))
    if relay_refused(resp):
        raise RelayError(RELAY_REFUSED, 401)
    if resp.status_code == 401:
        raise SignInRequired("Sign in to Etsy again.")
    resp.raise_for_status()
    # Deletes answer 204 with no body.
    return resp.json() if resp.content else {}


async def read_etsy_receipt(shop_id: str, receipt_id: str) -> dict:
    return await etsy_api("GET", f"shops/{shop_id}/receipts/{receipt_id}")


def _notify_sales(sale_ids: list[str], notes: dict[str, str] | None = None) -> None:
    from vendoo_studio.database import SessionLocal
    from vendoo_studio.desktop import notify
    from vendoo_studio.models.conversation import Conversation
    from vendoo_studio.models.marketplace_sale import MarketplaceSale

    notes = notes or {}
    with SessionLocal() as db:
        for sale in db.query(MarketplaceSale).filter(MarketplaceSale.id.in_(sale_ids)).all():
            conv = db.get(Conversation, sale.conversation_id) if sale.conversation_id else None
            title = conv.title if conv and conv.title else "An item"
            where = NAMES.get(sale.marketplace, sale.marketplace)
            extra = notes.get(sale.id) or "Open Anteroom to delist it elsewhere."
            notify(f"Sold on {where}: {title}. {extra}")


def _join_names(names: list[str]) -> str:
    labels = [NAMES.get(name, name) for name in names]
    if len(labels) <= 1:
        return labels[0] if labels else ""
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return f"{', '.join(labels[:-1])}, and {labels[-1]}"


def _delist_sentence(done: list[str], failed: dict[str, str]) -> str:
    if done and not failed:
        return f"Delisted from {_join_names(done)}."
    if done:
        return f"Delisted from {_join_names(done)}. {_join_names(list(failed))} could not be delisted. You can delist it from the item."
    detail = next(iter(failed.values()), "The marketplace did not confirm the delete.")
    return f"Couldn't delist it elsewhere: {detail} You can delist it from the item."


async def autodelist_for_sales(sale_ids: list[str], *, session_factory=None) -> dict[str, str]:
    """Delist the other live marketplaces after a sale, once, when the seller turned that on.

    The marketplace that sold is never included. ``confirm`` is the seller's saved choice,
    and the names are the other listings Anteroom still has up. Nothing is retried.
    """
    from vendoo_studio.services.user_settings import autodelist_enabled

    if not sale_ids or not autodelist_enabled():
        return {}
    if session_factory is None:
        from vendoo_studio.database import SessionLocal

        session_factory = SessionLocal
    from vendoo_studio.models.marketplace_sale import MarketplaceSale
    from vendoo_studio.services.marketplace_delist import MARKETPLACES, DelistError, delist, listings

    notes: dict[str, str] = {}
    groups: dict[str, list[str]] = {}
    order: list[str] = []
    with session_factory() as db:
        sales = db.query(MarketplaceSale).filter(MarketplaceSale.id.in_(sale_ids)).all()
        for sale in sales:
            if not sale.conversation_id:
                notes[sale.id] = "Anteroom doesn't have this listing, so nothing else was delisted."
                continue
            if sale.conversation_id not in groups:
                groups[sale.conversation_id] = []
                order.append(sale.conversation_id)
            groups[sale.conversation_id].append(sale.id)
    for conv_id in order:
        try:
            with session_factory() as db:
                known = listings(db, conv_id)
        except DelistError as exc:
            text = f"Couldn't delist it elsewhere: {exc}. You can delist it from the item."
        else:
            others = [
                name for name in MARKETPLACES
                if (entry := known.get(name))
                and entry.get("state") == "live"
                and entry.get("listing_id")
                and not entry.get("sold_here")
            ]
            if not others:
                text = "It isn't listed anywhere else."
            else:
                try:
                    with session_factory() as db:
                        result = await delist(db, conv_id, others, confirm=True)
                except DelistError as exc:
                    text = f"Couldn't delist it elsewhere: {exc}. You can delist it from the item."
                else:
                    failed = {
                        name: str((result.get(name) or {}).get("error") or "The marketplace did not confirm the delete.")
                        for name in others
                        if (result.get(name) or {}).get("state") == "failed"
                    }
                    done = [name for name in others if name not in failed]
                    text = _delist_sentence(done, failed)
        for sale_id in groups[conv_id]:
            notes[sale_id] = text
    return notes


async def announce_sales(sale_ids: list[str], *, session_factory=None) -> None:
    """Tell the seller about new sales. Delist the other marketplaces only when they opted in."""
    from vendoo_studio.services.user_settings import autodelist_enabled

    notes: dict[str, str] = {}
    try:
        notes = await autodelist_for_sales(sale_ids, session_factory=session_factory)
    except Exception:  # noqa: BLE001 - the sale is already recorded
        log.exception("could not delist after a sale")
        if autodelist_enabled():
            notes = {sale_id: "Couldn't delist it elsewhere. You can delist it from the item." for sale_id in sale_ids}
    try:
        notify = partial(_notify_sales, sale_ids, notes)
        await asyncio.to_thread(notify)
    except Exception:  # noqa: BLE001 - a missed banner must not drop the recorded sale
        log.debug("could not show the sale notification", exc_info=True)


def _handler():
    from vendoo_studio.database import SessionLocal
    from vendoo_studio.services.relay_sales import handle_event

    record = partial(handle_event, session_factory=SessionLocal, read_etsy_receipt=read_etsy_receipt)

    async def handle(event: dict) -> None:
        created = await record(event)
        if created:
            await announce_sales(created)

    return handle


def start_listening() -> None:
    """Listen for sales while any account is connected. Call on the server loop."""
    if get_marketplace_accounts():
        relay_client.start_stream(_handler())


def status() -> dict:
    accounts = get_marketplace_accounts()
    out = {}
    for marketplace in MARKETPLACES:
        account = accounts.get(marketplace)
        pending = _pending.get(marketplace)
        out[marketplace] = {
            "connected": bool(account),
            "label": account.get("label") if account else None,
            "connectedAt": account.get("connectedAt") if account else None,
            "pending": bool(pending and not pending.done),
            "authorizeUrl": pending.authorize_url if pending and not pending.done else None,
            "error": pending.error if pending and pending.done else None,
        }
    return {"accounts": out, "relay": relay_client.stream_state()}
