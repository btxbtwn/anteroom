"""Direct eBay and Etsy sign-in, through Anteroom's relay.

eBay and Etsy only send a sign-in back to an HTTPS address, and eBay's code
exchange needs the app's Cert ID, so the relay hosts the callback. It then sends
the seller's browser back to this Anteroom with a one-time claim, and hands over
the tokens only for that claim: someone who sends the seller a sign-in link they
started cannot collect the seller's tokens. They live in the Keychain from then on. Refreshes go
through the relay for the same reason; Etsy API calls go through it because
every one needs the app's shared secret.

Signing in links the account to this Mac on the relay, which is what routes
that account's sales here.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import secrets
import time
import webbrowser
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from vendoo_studio.services import relay_client
from vendoo_studio.services.keychain import (
    delete_marketplace_account,
    get_marketplace_accounts,
    get_relay_device,
    set_marketplace_account,
    update_marketplace_accounts,
)
from vendoo_studio.services.relay_client import RelayError, relay_request
from vendoo_studio.services.marketplace_errors import marketplace_call

log = logging.getLogger("vendoo_studio.marketplace_accounts")

MARKETPLACES = ("ebay", "etsy")
NAMES = {
    "ebay": "eBay",
    "etsy": "Etsy",
    "depop": "Depop",
    "poshmark": "Poshmark",
    "mercari": "Mercari",
}
SIGN_IN_TIMEOUT_S = 10 * 60
POLL_INTERVAL_S = 2
# Refresh a little before the marketplace says the token expires.
REFRESH_MARGIN_S = 120
# Where the iPhone app asks the relay to send the browser back.
APP_RETURN = "anteroom://marketplace-signin"


@dataclass
class PendingSignIn:
    marketplace: str
    state: str
    authorize_url: str
    started_at: float = field(default_factory=time.time)
    error: str | None = None
    done: bool = False
    task: asyncio.Task | None = None


_pending: dict[tuple[str, str], PendingSignIn] = {}


def _seller() -> str:
    """Whose sign-ins these are. The Mac has one seller; the web app has many."""
    from vendoo_studio.services.service_context import current

    context = current()
    return context.seller_id if context is not None else ""


def _on_web() -> bool:
    from vendoo_studio.services.service_context import current

    return current() is not None


async def _accounts() -> dict:
    """Saved accounts, read in a worker thread: on the web this is a database read,
    and waiting for a pooled connection on the request loop stalls every request."""
    return await asyncio.to_thread(get_marketplace_accounts)


async def account_id(marketplace: str) -> str:
    """The connected account's id, or "" when none is connected. Choices saved for one
    account are never shown for another."""
    account = (await _accounts()).get(_check(marketplace)) or {}
    return str(account.get("accountId") or account.get("userId") or "")


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
    """Report a sign-in that failed or expired. Success arrives through finish_sign_in."""
    deadline = pending.started_at + SIGN_IN_TIMEOUT_S
    name = NAMES[pending.marketplace]
    try:
        while time.time() < deadline:
            await asyncio.sleep(POLL_INTERVAL_S)
            if pending.done:
                return
            resp = await relay_request("GET", "/studio/oauth/result", params={"state": pending.state})
            if pending.done:
                return
            if resp.status_code == 404:
                pending.error = f"The {name} sign-in expired. Try again."
                return
            if resp.status_code == 200 and resp.json().get("error"):
                pending.error = resp.json()["error"]
                return
        pending.error = f"The {name} sign-in timed out. Try again."
    except asyncio.CancelledError:
        if not pending.done:
            pending.error = "Sign-in cancelled"
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced in status
        log.warning("%s sign-in failed: %s", name, exc)
        pending.error = f"{name} sign-in failed. Try again."
    finally:
        pending.done = True


class SignInNotFound(RuntimeError):
    """The browser brought back a sign-in this Anteroom did not start, or one that ended."""


async def finish_sign_in(marketplace: str, state: str, claim: str) -> dict:
    """Collect the tokens for a sign-in this Anteroom started, with the claim its browser brought back."""
    _check(marketplace)
    name = NAMES[marketplace]
    pending = _pending.get((_seller(), marketplace))
    if pending is None or pending.done or not hmac.compare_digest(pending.state.encode(), state.encode()):
        raise SignInNotFound(f"This {name} sign-in was not started here, or it already ended. Start again from Connections.")
    resp = await relay_request("GET", "/studio/oauth/result", params={"state": state, "claim": claim})
    result = resp.json() if resp.status_code == 200 else {}
    if resp.status_code != 200 or result.get("error") or not (result.get("tokens") or {}).get("accessToken"):
        message = str(result.get("error") or f"{name} sign-in could not be confirmed. Try again.")
        pending.error = message
        pending.done = True
        raise RelayError(message, resp.status_code)
    account = _account_record(result)
    await asyncio.to_thread(set_marketplace_account, marketplace, account)
    pending.done = True
    for warning in result.get("warnings") or []:
        log.warning("%s sign-in: %s", name, warning)
    start_listening()
    return {"marketplace": marketplace, "label": account["label"]}


def _return_to(marketplace: str, client: str) -> str:
    """Where the relay sends the browser after the seller approves: back to this Anteroom."""
    if not _on_web():
        from vendoo_studio.config import PORT

        return f"http://127.0.0.1:{PORT}/api/marketplace-accounts/{marketplace}/finish"
    if client == "app":
        return APP_RETURN
    from vendoo_studio.config import HOSTED_URL

    return f"{HOSTED_URL}/"


async def start_sign_in(marketplace: str, features: list[str] | None = None, client: str = "web") -> dict:
    """Open the marketplace's sign-in page; finish in the background."""
    _check(marketplace)
    if marketplace == "ebay":
        # Listing on eBay reads the seller's business policies, which needs this grant.
        features = sorted({*(features or []), "listings"})
    previous = _pending.pop((_seller(), marketplace), None)
    if previous and previous.task and not previous.task.done():
        previous.task.cancel()
    params = {"features": ",".join(features)} if features else {}
    params["returnTo"] = _return_to(marketplace, client)
    resp = await relay_request("POST", f"/studio/oauth/{marketplace}/start", params=params)
    if resp.status_code == 503:
        raise RelayError(f"{NAMES[marketplace]} sign-in isn't set up on Anteroom's relay yet.", 503)
    if resp.status_code != 200:
        raise RelayError(f"Could not start {NAMES[marketplace]} sign-in ({resp.status_code}).", resp.status_code)
    body = resp.json()
    pending = PendingSignIn(marketplace=marketplace, state=body["state"], authorize_url=body["authorizeUrl"])
    pending.task = asyncio.get_running_loop().create_task(_wait_for_sign_in(pending))
    _pending[(_seller(), marketplace)] = pending
    if not _on_web():
        # On the web, the seller's own browser opens the page from the response.
        try:
            webbrowser.open(pending.authorize_url)
        except Exception:  # noqa: BLE001 - the page shows the link too
            log.debug("could not open %s sign-in", marketplace, exc_info=True)
    return {"authorizeUrl": pending.authorize_url}


def cancel_sign_in(marketplace: str) -> None:
    pending = _pending.pop((_seller(), _check(marketplace)), None)
    if pending and pending.task and not pending.task.done():
        pending.task.cancel()


async def disconnect(marketplace: str) -> None:
    """Unlink the account on the relay and forget its tokens here."""
    _check(marketplace)
    cancel_sign_in(marketplace)
    account = (await _accounts()).get(marketplace)
    if account and account.get("accountId") and await asyncio.to_thread(get_relay_device):
        try:
            await relay_request("DELETE", f"/studio/accounts/{marketplace}/{account['accountId']}")
        except Exception as exc:  # noqa: BLE001 - forget locally regardless
            log.warning("could not unlink %s on the relay: %s", marketplace, exc)
    await asyncio.to_thread(delete_marketplace_account, marketplace)
    if not await _accounts() and not _on_web():
        relay_client.stop_stream()


def forget_deleted_ebay_account(user_id: str | None) -> None:
    """eBay said this account was closed: drop its tokens (the relay already unlinked it)."""
    account = get_marketplace_accounts().get("ebay")
    if user_id and account and account.get("accountId") == user_id:
        log.warning("eBay reported the connected account closed; forgetting it")
        delete_marketplace_account("ebay")


class SignInRequired(RuntimeError):
    pass


# One refresh per account at a time, across event loops, processes and
# Machines. Etsy rotates its refresh token, so a second refresh sent with the
# old one would sign the seller out. The claim is saved with the account, in
# one short atomic update (a locked Vault transaction on the web, the Keychain
# lock on the Mac); the relay call itself runs with no transaction open. It
# outlives relay_request's 30 s deadline, so it cannot lapse mid-refresh.
REFRESH_CLAIM_S = 60
CLAIM_POLL_S = 0.5


def _claim_refresh(marketplace: str, claim: str, rejected: str | None) -> tuple[dict | None, bool]:
    """The saved account, and whether this caller now holds its refresh.

    No claim is taken when the token is still fresh, cannot be refreshed, or
    another caller's claim has not run out.
    """
    outcome: dict = {"claimed": False}

    def take(accounts):
        account = outcome["account"] = accounts.get(marketplace)
        if (not account or not account.get("accessToken") or not account.get("refreshToken")
                or _fresh(account, rejected)
                or (account.get("refreshClaim") or {}).get("until", 0) > time.time()):
            return
        account = {**account, "refreshClaim": {"id": claim, "until": time.time() + REFRESH_CLAIM_S}}
        accounts[marketplace] = outcome["account"] = account
        outcome["claimed"] = True

    update_marketplace_accounts(take)
    return outcome["account"], outcome["claimed"]


def _store_refresh(marketplace: str, claim: str, used: dict, refreshed: dict) -> bool:
    """Save refreshed tokens unless the connection changed since the claim."""
    outcome = {"stored": False}

    def store(accounts):
        current = accounts.get(marketplace)
        if not current or any(current.get(key) != used.get(key) for key in ("accountId", "connectedAt", "refreshToken")):
            return
        held = current.get("refreshClaim")
        accounts[marketplace] = {**refreshed, "refreshClaim": held} if held and held.get("id") != claim else refreshed
        outcome["stored"] = True

    update_marketplace_accounts(store)
    return outcome["stored"]


def _release_refresh(marketplace: str, claim: str) -> None:
    def release(accounts):
        current = accounts.get(marketplace)
        if current and (current.get("refreshClaim") or {}).get("id") == claim:
            accounts[marketplace] = {key: value for key, value in current.items() if key != "refreshClaim"}

    update_marketplace_accounts(release)


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
    account = (await _accounts()).get(_check(marketplace))
    if not account or not account.get("accessToken"):
        raise SignInRequired(f"{NAMES[marketplace]} isn't connected.")
    if _fresh(account, rejected):
        return account["accessToken"]
    claim = secrets.token_hex(16)
    # A claim always runs out, so waiting for another caller's refresh is bounded too.
    deadline = time.monotonic() + REFRESH_CLAIM_S + 15
    while True:
        # Whoever held the claim may have refreshed already; use what it stored.
        account, claimed = await asyncio.to_thread(_claim_refresh, marketplace, claim, rejected)
        if not account or not account.get("accessToken"):
            raise SignInRequired(f"{NAMES[marketplace]} isn't connected.")
        if _fresh(account, rejected):
            return account["accessToken"]
        if not account.get("refreshToken"):
            raise SignInRequired(f"Sign in to {NAMES[marketplace]} again.")
        if claimed:
            break
        if time.monotonic() > deadline:
            raise RelayError(f"Refreshing the {NAMES[marketplace]} sign-in is taking too long. Try again in a minute.", 503)
        await asyncio.sleep(CLAIM_POLL_S)
    stored = False
    try:
        resp = await relay_request(
            "POST", f"/studio/oauth/{marketplace}/refresh", json_body={"refreshToken": account["refreshToken"]},
        )
        if relay_refused(resp):
            raise RelayError(RELAY_REFUSED, 401)
        if resp.status_code == 401:
            raise SignInRequired(f"Sign in to {NAMES[marketplace]} again.")
        if resp.status_code != 200:
            raise RelayError(f"Could not refresh the {NAMES[marketplace]} sign-in ({resp.status_code}).", resp.status_code)
        tokens = resp.json().get("tokens") or {}
        refreshed = {
            **{key: value for key, value in account.items() if key != "refreshClaim"},
            "accessToken": tokens.get("accessToken"),
            # eBay keeps its refresh token; Etsy issues a new one each time.
            "refreshToken": tokens.get("refreshToken") or account["refreshToken"],
            "expiresAt": time.time() + tokens["expiresIn"] if tokens.get("expiresIn") else None,
        }
        stored = await asyncio.to_thread(_store_refresh, marketplace, claim, account, refreshed)
        if not stored:
            # Disconnect or a new sign-in during the request revokes this refresh.
            # Never recreate the connection using a response for its old grant.
            raise SignInRequired(f"The {NAMES[marketplace]} connection changed. Sign in again.")
        return refreshed["accessToken"]
    finally:
        if not stored:
            try:
                await asyncio.to_thread(_release_refresh, marketplace, claim)
            except Exception:  # noqa: BLE001 - the claim runs out on its own
                log.warning("could not release the %s refresh claim", marketplace)


@marketplace_call("etsy")
async def etsy_api(method: str, path: str, **kwargs: Any):
    """Call Etsy's API (``path`` under /v3/application/) through the relay.

    A 401 from Etsy means the call was refused before it ran, so it is sent once
    more with a refreshed token.
    """
    if method.upper() not in {"GET", "HEAD"}:
        from vendoo_studio.services.listing_mode import require
        require("direct")
    async def send(token: str):
        for upload in (kwargs.get("files") or {}).values():
            handle = upload[1] if isinstance(upload, tuple) else None
            if handle is not None and hasattr(handle, "seek"):
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
    from vendoo_studio.services.mac_workspace import account_shown
    from vendoo_studio.services.user_settings import autodelist_enabled

    # While the Mac's window shows the account, the account delists after its own copy of the sale.
    if not sale_ids or not autodelist_enabled() or account_shown():
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
            if sale.order_status not in {"paid", "partially_refunded"}:
                notes[sale.id] = "The order is cancelled, refunded, or awaiting cancellation review. Nothing was delisted."
                continue
            if not sale.conversation_id:
                notes[sale.id] = "Anteroom doesn't have this listing, so nothing else was delisted."
                continue
            from vendoo_studio.services.sale_inventory import stock_after_sale

            stock = stock_after_sale(db, sale.conversation_id, sale.marketplace, sale.listing_id)
            if stock["status"] != "sold_out":
                notes[sale.id] = "Stock remains or could not be verified. Review quantities before delisting elsewhere."
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
            live = [
                name for name in MARKETPLACES
                if (entry := known.get(name))
                and entry.get("state") == "live"
                and entry.get("listing_id")
                and not entry.get("sold_here")
            ]
            if not live:
                text = "It isn't listed anywhere else."
            else:
                try:
                    result = await delist(conv_id, live, confirm=True)
                except DelistError as exc:
                    text = f"Couldn't delist it elsewhere: {exc}. You can delist it from the item."
                else:
                    failed = {
                        name: str((result.get(name) or {}).get("error") or "The marketplace did not confirm the delete.")
                        for name in live
                        if (result.get(name) or {}).get("state") == "failed"
                    }
                    done = [name for name in live if name not in failed]
                    text = _delist_sentence(done, failed)
        for sale_id in groups[conv_id]:
            notes[sale_id] = text
    return notes


async def announce_sales(sale_ids: list[str], *, session_factory=None) -> None:
    """Tell the seller about new sales. Delist the other marketplaces only when they opted in."""
    from vendoo_studio.services.user_settings import autodelist_enabled

    from vendoo_studio.database import SessionLocal
    from vendoo_studio.services.marketplace_inventory import read_after_sales

    notes: dict[str, str] = {}
    try:
        # What is left of a multi-unit item decides whether it is delisted elsewhere.
        await read_after_sales(sale_ids, session_factory=session_factory or SessionLocal)
    except Exception:  # noqa: BLE001 - the sale is already recorded
        log.exception("could not read stock after a sale")
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
    """Listen for sales while any account is connected. Call on the server loop.

    Hosted sales use hosted_sales_scheduler with a separate context per seller;
    this process-wide stream belongs only to the desktop.
    """
    if _on_web():
        return
    if get_marketplace_accounts():
        relay_client.start_stream(_handler())


def status() -> dict:
    accounts = get_marketplace_accounts()
    out = {}
    for marketplace in MARKETPLACES:
        account = accounts.get(marketplace)
        pending = _pending.get((_seller(), marketplace))
        out[marketplace] = {
            "connected": bool(account),
            "label": account.get("label") if account else None,
            "connectedAt": account.get("connectedAt") if account else None,
            "pending": bool(pending and not pending.done),
            "authorizeUrl": pending.authorize_url if pending and not pending.done else None,
            "error": pending.error if pending and pending.done else None,
        }
    relay = relay_client.stream_state() if not _on_web() else {"running": False, "connected": False, "lastError": None, "lastEventAt": None}
    return {"accounts": out, "relay": relay}
