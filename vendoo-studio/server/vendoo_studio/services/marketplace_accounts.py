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
NAMES = {"ebay": "eBay", "etsy": "Etsy"}
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


async def start_sign_in(marketplace: str) -> dict:
    """Open the marketplace's sign-in page; finish in the background."""
    _check(marketplace)
    previous = _pending.pop(marketplace, None)
    if previous and previous.task and not previous.task.done():
        previous.task.cancel()
    resp = await relay_request("POST", f"/studio/oauth/{marketplace}/start")
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


async def access_token(marketplace: str) -> str:
    """A current access token, refreshed through the relay when it is about to expire."""
    account = get_marketplace_accounts().get(_check(marketplace))
    if not account or not account.get("accessToken"):
        raise SignInRequired(f"{NAMES[marketplace]} isn't connected.")
    expires_at = account.get("expiresAt")
    if expires_at and expires_at - REFRESH_MARGIN_S > time.time():
        return account["accessToken"]
    if not account.get("refreshToken"):
        raise SignInRequired(f"Sign in to {NAMES[marketplace]} again.")
    resp = await relay_request(
        "POST", f"/studio/oauth/{marketplace}/refresh", json_body={"refreshToken": account["refreshToken"]},
    )
    if resp.status_code == 401:
        raise SignInRequired(f"Sign in to {NAMES[marketplace]} again.")
    if resp.status_code != 200:
        raise RelayError(f"Could not refresh the {NAMES[marketplace]} sign-in ({resp.status_code}).", resp.status_code)
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
    """Call Etsy's API (``path`` under /v3/application/) through the relay."""
    token = await access_token("etsy")
    resp = await relay_request(
        method,
        f"/studio/etsy/v3/application/{path.lstrip('/')}",
        headers={"X-Etsy-Authorization": f"Bearer {token}"},
        **kwargs,
    )
    if resp.status_code == 401:
        raise SignInRequired("Sign in to Etsy again.")
    resp.raise_for_status()
    return resp.json()


async def read_etsy_receipt(shop_id: str, receipt_id: str) -> dict:
    return await etsy_api("GET", f"shops/{shop_id}/receipts/{receipt_id}")


def _notify_sales(sale_ids: list[str]) -> None:
    from vendoo_studio.database import SessionLocal
    from vendoo_studio.desktop import notify
    from vendoo_studio.models.conversation import Conversation
    from vendoo_studio.models.marketplace_sale import MarketplaceSale

    with SessionLocal() as db:
        for sale in db.query(MarketplaceSale).filter(MarketplaceSale.id.in_(sale_ids)).all():
            conv = db.get(Conversation, sale.conversation_id) if sale.conversation_id else None
            title = conv.title if conv and conv.title else "An item"
            notify(f"Sold on {NAMES.get(sale.marketplace, sale.marketplace)}: {title}. Open Anteroom to delist it elsewhere.")


def _handler():
    from vendoo_studio.database import SessionLocal
    from vendoo_studio.services.relay_sales import handle_event

    record = partial(handle_event, session_factory=SessionLocal, read_etsy_receipt=read_etsy_receipt)

    async def handle(event: dict) -> None:
        created = await record(event)
        if created:
            try:
                await asyncio.to_thread(_notify_sales, created)
            except Exception:  # noqa: BLE001 - a missed banner must not unacknowledge the sale
                log.debug("could not show the sale notification", exc_info=True)

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
