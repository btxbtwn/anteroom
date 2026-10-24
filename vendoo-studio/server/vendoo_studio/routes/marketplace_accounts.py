"""Direct marketplace connections and the sales they report.

Delisting stays seller-triggered: ``/delist`` needs the marketplaces named and
``confirm``, and goes through the same Vendoo path as the listing editor.
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
from vendoo_studio.services.vendoo_import import parse_notes

router = APIRouter(tags=["marketplace-accounts"])


def _marketplace(marketplace: str) -> str:
    if marketplace not in marketplace_accounts.MARKETPLACES:
        raise HTTPException(404, "Unknown marketplace")
    return marketplace


@router.get("/api/marketplace-accounts")
def get_status():
    return marketplace_accounts.status()


@router.post("/api/marketplace-accounts/{marketplace}/connect")
async def connect(marketplace: str):
    try:
        return await marketplace_accounts.start_sign_in(_marketplace(marketplace))
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
        "delistedFrom": sale.delisted_from,
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


class DelistRequest(BaseModel):
    """The seller names where to delist and confirms, in the same call."""
    marketplaces: list[str] = Field(min_length=1, max_length=15)
    confirm: bool = False


@router.post("/api/marketplace-sales/{sale_id}/delist")
async def delist_elsewhere(sale_id: str, body: DelistRequest, db: Session = Depends(get_db)):
    from vendoo_studio.routes.vendoo_api import ListRequest, _list_or_delist

    sale = _get_sale(db, sale_id)
    if not sale.conversation_id:
        raise HTTPException(409, "This sale isn't matched to a listing, so there is nothing to delist from here.")
    if any(mp.strip().lower() == sale.marketplace for mp in body.marketplaces):
        raise HTTPException(400, f"It sold on {marketplace_accounts.NAMES[sale.marketplace]}; delist it from the others.")
    reply = await _list_or_delist(
        sale.conversation_id,
        ListRequest(marketplaces=body.marketplaces, confirm=body.confirm),
        db,
        delist=True,
    )
    sale.state = "delisted"
    sale.delisted_from = {"marketplaces": reply["marketplaces"], "result": reply.get("result")}
    sale.resolved_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()
    return {"ok": True, "result": reply.get("result"), "marketplaces": reply["marketplaces"]}
