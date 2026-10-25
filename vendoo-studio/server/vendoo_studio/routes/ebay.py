"""eBay workspace: direct reads and separately reviewed account changes."""
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.models.ebay_action import EbayAction
from vendoo_studio.services import ebay_actions, ebay_listings, ebay_workspace, user_settings
from vendoo_studio.services.ebay_client import EbayClient, EbayFailure, identity
from vendoo_studio.services.marketplace_accounts import SignInRequired

router = APIRouter(prefix="/api/ebay", tags=["ebay"])
Section = Literal["traffic", "finances", "payouts", "inbox", "messages", "feedback", "orders", "order", "disputes", "dispute",
                  "campaigns", "promotions", "promotion-reports", "ad-reports", "ad-report", "emails", "audiences", "inventory", "offers", "policies", "locations", "categories", "aspects", "research", "activity"]


def failure(exc: Exception) -> HTTPException:
    if isinstance(exc, EbayFailure):
        return HTTPException(exc.status if 400 <= exc.status < 500 else 502, str(exc))
    if isinstance(exc, SignInRequired):
        return HTTPException(403, str(exc))
    if isinstance(exc, ValidationError):
        return HTTPException(422, "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors(include_input=False)))
    if isinstance(exc, ValueError):
        return HTTPException(422, "Check the amounts and dates, then try again.")
    return HTTPException(502, "eBay could not complete this request. Check your connection and permissions.")


@router.get("/read/{section}")
async def read(section: Section, offset: int = Query(0, ge=0, le=100000), target: str = Query("", max_length=200),
               q: str = Query("", max_length=150), start: date | None = None, end: date | None = None,
               db: Session = Depends(get_db)):
    try:
        if section == "activity":
            return ebay_actions.activity(db)
        client = await EbayClient.connect()
        today = datetime.now(UTC).date()
        if section == "traffic":
            return await ebay_workspace.traffic(client, db, start or today - timedelta(days=29), end or today, offset)
        if section == "finances":
            return await ebay_workspace.finances(client, db, start or today - timedelta(days=29), end or today, offset)
        if section in ("research", "categories") and not q.strip():
            raise EbayFailure("Enter search words first.", 422)
        return await ebay_workspace.read(client, section, offset, target, q)
    except (EbayFailure, SignInRequired, httpx.HTTPError, ValueError) as exc:
        raise failure(exc) from exc


@router.get("/listing/{listing_id}")
async def listing(listing_id: str):
    try:
        return await ebay_actions.owned_listing(await EbayClient.connect(), listing_id)
    except (EbayFailure, SignInRequired, httpx.HTTPError, ValueError) as exc:
        raise failure(exc) from exc


class EbayListingDefaults(BaseModel):
    fulfillment_policy_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{1,64}$")
    payment_policy_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{1,64}$")
    return_policy_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{1,64}$")
    merchant_location_key: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{1,64}$")


@router.get("/listing-profiles")
async def listing_profiles():
    """Business policy and location names and IDs only."""
    try:
        return await ebay_listings.listing_profiles()
    except (EbayFailure, SignInRequired, httpx.HTTPError) as exc:
        raise failure(exc) from exc


@router.get("/listing-defaults")
def get_listing_defaults():
    return user_settings.get_ebay_listing_defaults()


@router.put("/listing-defaults")
async def put_listing_defaults(body: EbayListingDefaults):
    """Chosen once in Settings, for accounts with several policies or locations. Only the account's own are kept."""
    try:
        profiles = await ebay_listings.listing_profiles()
    except (EbayFailure, SignInRequired, httpx.HTTPError) as exc:
        raise failure(exc) from exc
    for name, kind in (("fulfillment_policy_id", "fulfillment"), ("payment_policy_id", "payment"),
                       ("return_policy_id", "return"), ("merchant_location_key", "locations")):
        value = getattr(body, name)
        if value and value not in {option["id"] for option in profiles[kind]}:
            raise HTTPException(400, "Choose policies and a location from the connected eBay account.")
    return user_settings.set_ebay_listing_defaults(body.model_dump())


@router.post("/research/image")
async def image_search(body: ebay_actions.ImageUpload, offset: int = Query(0, ge=0, le=10000)):
    try:
        ebay_actions.image_bytes(body.model_dump())
        client = await EbayClient.connect()
        result = await client.request("POST", "/buy/browse/v1/item_summary/search_by_image", app=True,
                                      body={"image": body.data}, params={"limit": 25, "offset": offset,
                                      "filter": "buyingOptions:{FIXED_PRICE},deliveryCountry:US"})
        return ebay_workspace.research_page(result, offset)
    except (EbayFailure, SignInRequired, httpx.HTTPError, ValueError) as exc:
        raise failure(exc) from exc


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(max_length=30)
    payload: dict


class ConfirmAction(ActionRequest):
    id: str = Field(max_length=50)
    confirm: Literal[True]


@router.post("/actions/preview")
async def preview(body: ActionRequest, db: Session = Depends(get_db)):
    try:
        return await ebay_actions.review(db, body.kind, body.payload)
    except (EbayFailure, SignInRequired, httpx.HTTPError, ValueError) as exc:
        raise failure(exc) from exc


@router.post("/actions/confirm")
async def confirm(body: ConfirmAction, db: Session = Depends(get_db)):
    try:
        return await ebay_actions.execute(db, body.id, body.kind, body.payload)
    except (EbayFailure, SignInRequired, httpx.HTTPError, ValueError) as exc:
        raise failure(exc) from exc


class Checked(BaseModel):
    confirm: Literal[True]


@router.post("/actions/{action_id}/checked")
def checked(action_id: str, body: Checked, db: Session = Depends(get_db)):
    action = db.get(EbayAction, action_id)
    try:
        if not action or action.account_id != identity()[0] or action.state != "unknown":
            raise EbayFailure("This action does not need a result check.", 409)
        action.state = "checked"
        db.commit()
        return {"ok": True}
    except (EbayFailure, SignInRequired) as exc:
        raise failure(exc) from exc
