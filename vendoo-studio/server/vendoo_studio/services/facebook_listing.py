"""Read one linked Facebook Marketplace listing through Chrome, when the seller asks.

The extension reads the listing's own page in the seller's signed-in Chrome
and returns only its state and whether it is on the seller's account. A
listing Facebook reports sold is recorded the same way sale detection records
it. Nothing on Facebook changes.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sqlalchemy.orm import Session

from vendoo_studio.services import browser_bridge
from vendoo_studio.services.listing_result import scrub
from vendoo_studio.services.marketplace_delist import DelistError, listings

STATES = ("live", "pending", "sold", "hidden", "draft", "not_live")
OWNERS = ("you", "other")
ERROR_CODES = ("signed_out", "challenge", "rate_limited", "schema_drift", "network", "rejected")


async def _read_in_chrome(listing_id: str) -> object:
    """The Bridge's read of the listing page: through the Mac's paired Chrome, or on the
    web through the seller's own Anteroom tab."""
    from vendoo_studio.services.extension_connection import extension_manager
    from vendoo_studio.services.service_context import current

    payload = {"marketplace": "facebook", "listing_id": listing_id}
    context = current()
    if context is not None:
        from vendoo_studio.services.hosted_browser_relay import ChromeReadError, relay_for

        try:
            return await relay_for(context.seller_id).read("marketplace.listing_state", payload, 50)
        except ChromeReadError as exc:
            raise DelistError(409, str(exc)) from exc
    if not extension_manager.connected:
        raise DelistError(409, "Connect Chrome first.")
    try:
        return await browser_bridge.request(SimpleNamespace(id=None), "marketplace.listing_state", payload, timeout=45)
    except browser_bridge.BrowserBridgeError as exc:
        raise DelistError(502, "Chrome did not answer. Try again.") from exc


def _reply(raw: object) -> dict:
    data = raw if isinstance(raw, dict) else {}
    if data.get("ok") is True and data.get("state") in STATES:
        return {"ok": True, "state": data["state"], "owner": data.get("owner") if data.get("owner") in OWNERS else None}
    code = data.get("error_code") if data.get("error_code") in ERROR_CODES else "rejected"
    error = data.get("error")
    if not isinstance(error, str):
        error = ""
    return {"ok": False, "error_code": code,
            "error": scrub(error)[:300] if error else "Chrome could not read the Facebook listing.",
            "marketplace_message": scrub(data.get("marketplace_message"))}


async def check(db: Session, conv_id: str) -> dict:
    """The linked Facebook listing's state now, and a recorded sale if Facebook says it sold."""
    from vendoo_studio.services.chrome_sales import record_reported_sales
    from vendoo_studio.services.marketplace_accounts import announce_sales

    entry = listings(db, conv_id).get("facebook") or {}
    listing_id = entry.get("listing_id")
    if not listing_id:
        raise DelistError(409, "Anteroom has no Facebook listing link for this item.")
    raw = await _read_in_chrome(listing_id)
    result = _reply(raw)
    result["sale_recorded"] = False
    watched = entry.get("state") == "live" and not entry.get("sold_here")
    if result["ok"] and result["state"] == "sold" and result["owner"] != "other" and watched:
        created = await asyncio.to_thread(record_reported_sales, "facebook", [listing_id],
                                           conversation_ids={listing_id: conv_id})
        if created:
            await announce_sales(created)
        result["sale_recorded"] = bool(created)
    db.expire_all()
    return {**result, "listings": listings(db, conv_id)}
