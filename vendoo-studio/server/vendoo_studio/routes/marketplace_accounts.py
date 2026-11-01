"""Direct marketplace connections, sale alerts, and local sales reconciliation.

Matching a sale only updates its local item association. These routes do not
publish or delist; seller-confirmed marketplace actions are handled separately.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException
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


@router.post("/api/marketplace-accounts/{marketplace}/connect")
async def connect(marketplace: str, body: ConnectionRequest | None = None):
    try:
        features = body.features if body else []
        if any(feature not in {"analytics", "finances", "orders", "disputes", "messages", "feedback", "marketing", "listings"} for feature in features):
            raise HTTPException(422, "Unknown eBay permission group")
        return await marketplace_accounts.start_sign_in(_marketplace(marketplace), **({"features": features} if features else {}))
    except HTTPException:
        raise
    except RelayError as exc:
        raise HTTPException(503 if exc.status in (None, 429, 503) else 502, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - offline and the like
        raise HTTPException(503, "Can't reach Anteroom's relay. Check your internet connection.") from exc


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
def list_sales(state: str | None = "new", conversation_id: str | None = None, db: Session = Depends(get_db)):
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
async def recover_orders(body: RecoverRequest, db: Session = Depends(get_db)):
    """Read one page of changed orders into the local ledger. Never notify or delist."""
    from vendoo_studio.services import order_recovery
    from vendoo_studio.services.ebay_client import EbayFailure
    from vendoo_studio.services.etsy_workspace import EtsyFailure

    try:
        return await order_recovery.recover(db, body.marketplace, body.start, body.end, body.page, body.connected_at, body.connection, body.through)
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
    marketplace: Literal["ebay", "etsy", "poshmark", "mercari", "depop", "sellwild", "vinted"] | None = None,
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
