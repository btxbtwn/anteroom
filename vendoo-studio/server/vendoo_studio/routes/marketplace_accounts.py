"""Direct marketplace connections, sale alerts, and local sales reconciliation.

Matching a sale only updates its local item association. These routes do not
publish or delist; seller-confirmed marketplace actions are handled separately.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from html import escape
from typing import Literal, cast

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.services import marketplace_accounts, marketplace_delist
from vendoo_studio.services.relay_client import RelayError
from vendoo_studio.services.vendoo_import import vendoo_binding

router = APIRouter(tags=["marketplace-accounts"])


def _marketplace(marketplace: str) -> str:
    if marketplace not in marketplace_accounts.MARKETPLACES:
        raise HTTPException(404, "Unknown marketplace")
    return marketplace


@router.get("/api/marketplace-accounts")
def get_status():
    return marketplace_accounts.status()


class ConnectionRequest(BaseModel):
    features: list[str] = Field(default_factory=list, max_length=8)
    # The iPhone app finishes sign-in itself; everyone else returns to the web page.
    client: Literal["web", "app"] = "web"


@router.post("/api/marketplace-accounts/{marketplace}/connect")
async def connect(marketplace: str, body: ConnectionRequest | None = None):
    try:
        body = body or ConnectionRequest()
        features = body.features
        if any(feature not in {"analytics", "finances", "orders", "disputes", "messages", "feedback", "marketing", "listings"} for feature in features):
            raise HTTPException(422, "Unknown eBay permission group")
        return await marketplace_accounts.start_sign_in(_marketplace(marketplace), **({"features": features} if features else {}),
                                                        client=body.client)
    except HTTPException:
        raise
    except RelayError as exc:
        raise HTTPException(503 if exc.status in (None, 429, 503) else 502, str(exc)) from exc
    except (httpx.HTTPError, OSError) as exc:  # offline and the like
        raise HTTPException(503, "Can't reach Anteroom's relay. Check your internet connection.") from exc


class FinishRequest(BaseModel):
    state: str = Field(min_length=1, max_length=200)
    claim: str = Field(min_length=1, max_length=200)


async def _finish(marketplace: str, state: str, claim: str) -> dict:
    try:
        return await marketplace_accounts.finish_sign_in(_marketplace(marketplace), state, claim)
    except marketplace_accounts.SignInNotFound as exc:
        raise HTTPException(409, str(exc)) from exc
    except RelayError as exc:
        raise HTTPException(502, str(exc)) from exc
    except (httpx.HTTPError, OSError) as exc:
        raise HTTPException(503, "Can't reach Anteroom's relay. Check your internet connection.") from exc


@router.post("/api/marketplace-accounts/{marketplace}/finish")
async def finish(marketplace: str, body: FinishRequest):
    """The web page or the iPhone app hands over the claim the relay sent its browser back with."""
    return await _finish(marketplace, body.state, body.claim)


def _finish_page(title: str, detail: str, status: int) -> HTMLResponse:
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(title)} · Anteroom</title>
<style>:root{{color-scheme:light dark;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
body{{margin:0;min-height:100vh;display:grid;place-items:center}}main{{max-width:26rem;padding:2rem;text-align:center}}
h1{{font-size:1.25rem}}p{{opacity:.75;line-height:1.5}}</style></head>
<body><main><h1>{escape(title)}</h1><p>{escape(detail)}</p></main></body></html>"""
    return HTMLResponse(html, status, headers={
        "cache-control": "no-store", "referrer-policy": "no-referrer",
        "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'",
    })


@router.get("/api/marketplace-accounts/{marketplace}/finish", include_in_schema=False)
async def finish_page(marketplace: str, state: str = "", claim: str = ""):
    """Where the seller's browser lands on this Mac after approving eBay or Etsy."""
    name = marketplace_accounts.NAMES.get(_marketplace(marketplace), marketplace)
    if not state or not claim or len(state) > 200 or len(claim) > 200:
        return _finish_page(f"{name} was not connected", "This link is incomplete. Start again from Anteroom's Connections settings.", 400)
    try:
        done = await _finish(marketplace, state, claim)
    except HTTPException as exc:
        return _finish_page(f"{name} was not connected", str(exc.detail), exc.status_code)
    who = f"{done['label']} is" if done.get("label") else "Your account is"
    return _finish_page(f"{who} connected", f"{who} now linked to Anteroom. You can close this tab.", 200)


@router.delete("/api/marketplace-accounts/{marketplace}/connect")
def cancel_connect(marketplace: str):
    marketplace_accounts.cancel_sign_in(_marketplace(marketplace))
    return {"ok": True}


@router.delete("/api/marketplace-accounts/{marketplace}")
async def disconnect(marketplace: str):
    await marketplace_accounts.disconnect(_marketplace(marketplace))
    return {"ok": True}


def _other_marketplaces(listings: dict, sold_on: str) -> list[str]:
    """Other listings still present, including direct records and seller links."""
    return sorted(name for name, entry in listings.items()
                  if name != sold_on and entry.get("listing_id") and not entry.get("sold_here")
                  and entry.get("state") not in {"deleted", "deleting"})


def _sale_out(sale: MarketplaceSale, conv: Conversation | None, listings: dict, stock: dict) -> dict:
    return {
        "stockStatus": stock["status"], "remainingQuantity": stock["remaining"],
        "id": sale.id,
        "marketplace": sale.marketplace,
        "orderId": sale.order_id,
        "listingId": sale.listing_id,
        "quantity": sale.quantity,
        "orderStatus": sale.order_status,
        "state": sale.state,
        "soldAt": sale.sold_at.isoformat() if sale.sold_at else None,
        "receivedAt": sale.received_at.isoformat() if sale.received_at else None,
        "conversationId": sale.conversation_id,
        "title": conv.title if conv else None,
        "listedElsewhere": _other_marketplaces(listings, sale.marketplace),
        "vendooUrl": vendoo_binding(conv.notes).get("vendooUrl") if conv else None,
    }


@router.get("/api/marketplace-sales")
def list_sales(background_tasks: BackgroundTasks, state: str | None = "new", conversation_id: str | None = None, db: Session = Depends(get_db, scope="function")):
    from vendoo_studio.services import recent_orders

    background_tasks.add_task(recent_orders.refresh)
    query = db.query(MarketplaceSale)
    if state:
        query = query.filter(MarketplaceSale.state == state)
    if conversation_id:
        query = query.filter(MarketplaceSale.conversation_id == conversation_id)
    sales = query.order_by(MarketplaceSale.received_at.desc()).limit(200).all()
    ids = {s.conversation_id for s in sales if s.conversation_id}
    convs = {c.id: c for c in db.query(Conversation).filter(Conversation.id.in_(ids)).all()} if ids else {}
    # An order can have several lines for one item. Read its current listings
    # once, rather than re-querying inventory for every line in the response.
    listings = {conv_id: marketplace_delist.listings(db, conv_id) for conv_id in convs}
    from vendoo_studio.services.sale_inventory import stock_after_sale

    stocks = {}
    for sale in sales:
        key = (sale.conversation_id, sale.marketplace, sale.listing_id)
        if sale.conversation_id is not None and sale.conversation_id in convs and key not in stocks:
            stocks[key] = stock_after_sale(db, sale.conversation_id, sale.marketplace, sale.listing_id)
    return {"sales": [_sale_out(s, convs.get(cast(str, s.conversation_id)), listings.get(cast(str, s.conversation_id), {}),
                               stocks.get((s.conversation_id, s.marketplace, s.listing_id), {"status": "unknown", "remaining": None}))
                      for s in sales]}


class RecoverRequest(BaseModel):
    marketplace: Literal["ebay", "etsy"]
    start: date
    end: date
    page: int = Field(default=1, ge=1, le=4000)
    connected_at: str = Field(min_length=1, max_length=40)
    connection: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    through: datetime | None = None


@router.post("/api/marketplace-sales/recover")
async def recover_orders(body: RecoverRequest):
    """Read one page of changed orders into the local ledger. Never notify or delist."""
    from vendoo_studio.services import order_recovery
    from vendoo_studio.services.ebay_client import EbayFailure
    from vendoo_studio.services.etsy_workspace import EtsyFailure

    try:
        return await order_recovery.recover(body.marketplace, body.start, body.end, body.page, body.connected_at, body.connection, body.through)
    except (order_recovery.RecoveryError, EbayFailure, EtsyFailure) as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except marketplace_accounts.SignInRequired as exc:
        raise HTTPException(403, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - provider payloads must never leak in a response
        raise HTTPException(502, "Could not recover orders. Check the marketplace connection and retry this page.") from exc


class ReconcileResponse(BaseModel):
    matched: int
    unmatched: int


@router.post("/api/marketplace-sales/reconcile", response_model=ReconcileResponse)
def reconcile_sales(
    marketplace: Literal["ebay", "etsy", "poshmark", "mercari", "depop"] | None = None,
    db: Session = Depends(get_db),
):
    """Match new sale alerts against saved listing links. No marketplace calls."""
    from vendoo_studio.services.relay_sales import reconcile_unmatched

    matched = reconcile_unmatched(db, marketplace=marketplace)
    pending = db.query(MarketplaceSale).filter(MarketplaceSale.state == "new", MarketplaceSale.conversation_id.is_(None))
    if marketplace:
        pending = pending.filter(MarketplaceSale.marketplace == marketplace)
    return ReconcileResponse(matched=matched, unmatched=pending.count())


def _get_sale(db: Session, sale_id: str) -> MarketplaceSale:
    sale = db.get(MarketplaceSale, sale_id)
    if not sale:
        raise HTTPException(404, "Sale not found")
    return sale


@router.post("/api/marketplace-sales/{sale_id}/dismiss")
def dismiss_sale(sale_id: str, db: Session = Depends(get_db)):
    sale = _get_sale(db, sale_id)
    sale.state = "dismissed"
    sale.resolved_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()
    return {"ok": True}
