"""Direct marketplace connections and the sales they report.

A reported sale only tells the seller where the item is still listed. There is
no delist here: Vendoo's server-side delist does not reach Poshmark, Mercari or
Depop (see ``vendoo_api.delist_item``), so the seller delists in Vendoo.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.services import marketplace_accounts
from vendoo_studio.services.relay_client import RelayError
from vendoo_studio.services.vendoo_import import parse_notes, vendoo_binding

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


def _other_marketplaces(conv: Conversation | None, sold_on: str) -> list[str]:
    """Where else Vendoo says this item is listed: the delist candidates."""
    if not conv:
        return []
    urls = parse_notes(conv.notes).get("vendooListingUrls") or {}
    if not isinstance(urls, dict):
        return []
    return sorted(mp for mp, url in urls.items() if url and mp.lower() != sold_on)


def _sale_out(sale: MarketplaceSale, conv: Conversation | None) -> dict:
    return {
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
        "listedElsewhere": _other_marketplaces(conv, sale.marketplace),
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
    return {"sales": [_sale_out(s, convs.get(s.conversation_id)) for s in sales]}


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
