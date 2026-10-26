"""Direct marketplace connections, sale alerts, and local sales reconciliation.

Matching a sale only updates its local item association. These routes do not
publish or delist; seller-confirmed marketplace actions are handled separately.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

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


def _sale_out(db: Session, sale: MarketplaceSale, conv: Conversation | None, listings: dict) -> dict:
    from vendoo_studio.services.sale_inventory import stock_after_sale

    stock = stock_after_sale(db, conv.id, sale.marketplace, sale.listing_id) if conv else {"status": "unknown", "remaining": None}
    return {
        "stockStatus": stock["status"], "remainingQuantity": stock["remaining"],
        "id": sale.id,
        "marketplace": sale.marketplace,
        "orderId": sale.order_id,
        "listingId": sale.listing_id,
        "quantity": sale.quantity,
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
    return {"sales": [_sale_out(db, s, convs.get(s.conversation_id), listings.get(s.conversation_id, {})) for s in sales]}


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
