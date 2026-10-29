"""Seller commands for a listing: price, one field, Depop measurements, department."""

from __future__ import annotations

import copy
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo

router = APIRouter(tags=["conversations"])

_PUSHABLE = ("ebay", "poshmark", "etsy", "depop", "mercari", "vinted", "sellwild")


class SetPriceBody(BaseModel):
    price: float
    all_marketplaces: bool = False
    marketplace: str | None = None
    marketplace_price: float | None = None
    push: bool = False
    confirmed: bool = False


class FieldBody(BaseModel):
    path: str
    value: Any = None
    clear: bool = False


class MeasurementRow(BaseModel):
    id: str = ""
    label: str = ""
    value: str = ""
    unit: str = "in"


class DepopMeasurementsBody(BaseModel):
    unit: str = "in"
    values: list[MeasurementRow] = Field(default_factory=list)
    clear: bool = False


class DepartmentBody(BaseModel):
    apply: bool = False


def _listing(db: Session, conv_id: str) -> tuple[dict, str]:
    if not ConversationRepo(db).get(conv_id):
        raise HTTPException(404, "Conversation not found")
    current = ListingRepo(db).get_current(conv_id)
    revision = ListingRepo(db).get_revision(current.current_revision_id) if current and current.current_revision_id else None
    if not revision or not isinstance(revision.listing_json, dict):
        raise HTTPException(404, "Generate a listing before editing it.")
    return copy.deepcopy(revision.listing_json), revision.id


def _save(conv_id: str, listing: dict, revision_id: str, db: Session) -> dict:
    from vendoo_studio.repositories.queries import RevisionConflict
    from vendoo_studio.services.listing_editor import save_listing_edit

    try:
        return save_listing_edit(conv_id, listing, db, expected_revision_id=revision_id)
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/conversations/{conv_id}/set-price")
async def set_price(conv_id: str, body: SetPriceBody, db: Session = Depends(get_db)):
    """Set the item price. Unlocked marketplace prices follow it. Push writes a live listing."""
    from vendoo_studio.services.seller_overrides import CANONICAL_PRICE, locked_paths, price_number, set_path

    if body.price <= 0:
        raise HTTPException(400, "Enter a price greater than zero.")
    if body.push and not body.confirmed:
        raise HTTPException(400, "Pushing a price onto a live listing needs confirmed=true.")
    listing, revision_id = _listing(db, conv_id)
    listing["price"] = price_number(body.price)
    locks = locked_paths(listing)
    if body.all_marketplaces:
        locks = {path for path in locks if path.split(".")[-1].casefold() not in {"price", "poshmarkprice", "buyitnowprice"} and path != "price"}
    market = str(body.marketplace or "").strip().lower()
    if market:
        if market not in CANONICAL_PRICE:
            raise HTTPException(400, f"Unknown marketplace {market}.")
        amount = body.marketplace_price if body.marketplace_price is not None else body.price
        set_path(listing, CANONICAL_PRICE[market], price_number(amount))
        if price_number(amount) != price_number(body.price):
            locks.add(CANONICAL_PRICE[market])
        else:
            locks.discard(CANONICAL_PRICE[market])
    listing["seller_fields"] = sorted(locks)
    saved = _save(conv_id, listing, revision_id, db)
    pushed: list[str] = []
    waiting: list[str] = []
    if body.push:
        from vendoo_studio.routes.native_marketplaces import ListRequest
        from vendoo_studio.services.marketplace_delist import listings as live_listings
        from vendoo_studio.services.native_marketplaces import NativeMarketplaceError, update_live

        current = ListingRepo(db).get_current(conv_id)
        live = live_listings(db, conv_id)
        targets = [market] if market else [name for name in _PUSHABLE if (live.get(name) or {}).get("state") == "live"]
        for name in targets:
            entry = live.get(name) or {}
            if entry.get("state") != "live" or not entry.get("listing_id"):
                continue
            try:
                await update_live(conv_id, name, ListRequest(revision_id=current.current_revision_id, confirm=True), db)
                pushed.append(name)
                break
            except NativeMarketplaceError as exc:
                waiting.append(f"{name}: {exc}")
        waiting.extend(name for name in targets if name not in pushed and not any(name in item for item in waiting))
    return {"ok": True, "revision_id": saved.get("revision_id"), "price": listing["price"], "pushed": pushed, "waiting": waiting}


@router.post("/api/conversations/{conv_id}/fields")
def set_field(conv_id: str, body: FieldBody, db: Session = Depends(get_db)):
    """Set or clear one listing path. The value is locked so a later cleanup does not put the old one back."""
    from vendoo_studio.services.seller_overrides import apply_field_updates

    if not str(body.path or "").strip():
        raise HTTPException(400, "Name the field path to change.")
    listing, revision_id = _listing(db, conv_id)
    apply_field_updates(listing, [{"path": body.path, "value": body.value, "clear": body.clear}])
    saved = _save(conv_id, listing, revision_id, db)
    return {"ok": True, "revision_id": saved.get("revision_id"), "path": body.path}


@router.put("/api/conversations/{conv_id}/depop-measurements")
def set_depop_measurements(conv_id: str, body: DepopMeasurementsBody, db: Session = Depends(get_db)):
    """Set Depop's measurements, or clear them so the stored garment measurements are used again."""
    from vendoo_studio.services.depop_measurements import sync_depop_measurements
    from vendoo_studio.services.seller_overrides import locked_paths
    from vendoo_studio.services.vendoo_import import parse_notes

    listing, revision_id = _listing(db, conv_id)
    locks = locked_paths(listing)
    depop = dict(listing.get("depop_specifics") or {})
    if body.clear or not body.values:
        locks.discard("depop_specifics.measurements")
        listing["seller_fields"] = sorted(locks)
        listing["depop_specifics"] = depop
        depop.pop("measurements", None)
        sync_depop_measurements(listing, parse_notes(ConversationRepo(db).get(conv_id).notes), force=True)
    else:
        depop["measurements"] = {
            "unit": body.unit or "in",
            "values": [row.model_dump() for row in body.values if str(row.value).strip()],
        }
        listing["depop_specifics"] = depop
        locks.add("depop_specifics.measurements")
        listing["seller_fields"] = sorted(locks)
    saved = _save(conv_id, listing, revision_id, db)
    return {"ok": True, "revision_id": saved.get("revision_id"), "measurements": listing.get("depop_specifics", {}).get("measurements")}


@router.post("/api/conversations/{conv_id}/department/review")
async def department_review(conv_id: str, body: DepartmentBody, db: Session = Depends(get_db)):
    """Recommend men's, women's, or unisex from the photos. apply writes it onto the draft."""
    from vendoo_studio.services.department_review import review_department

    result = await review_department(db, conv_id, apply=body.apply)
    if not result.get("ok") and result.get("error") == "Conversation not found":
        raise HTTPException(404, result["error"])
    return result
