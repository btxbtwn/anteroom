from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.services.etsy_audit import etsy_not_allowed_live
from vendoo_studio.services import etsy_listings, etsy_workspace, marketplace_accounts, user_settings
from vendoo_studio.services.marketplace_accounts import SignInRequired
from vendoo_studio.services.relay_client import RelayError

router = APIRouter(prefix="/api/etsy", tags=["etsy"])


async def _read_workspace(operation, response: Response):
    response.headers["Cache-Control"] = "no-store"
    try:
        return await operation(etsy_workspace.EtsyClient())
    except etsy_workspace.EtsyFailure as exc:
        raise HTTPException(exc.status, str(exc)) from None
    except SignInRequired:
        raise HTTPException(403, "Connect Etsy or sign in again to view your shop.") from None
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in {401, 403}:
            raise HTTPException(403, "Etsy denied access. Reconnect your shop to grant permission.") from None
        if status == 429:
            raise HTTPException(429, "Etsy's request limit was reached. Wait a moment, then refresh.") from None
        raise HTTPException(502, "Etsy could not load this view. Try again.") from None
    except Exception:
        # Upstream errors may contain authorization headers or buyer details.
        raise HTTPException(502, "Could not reach Etsy. Check your connection and try again.") from None


@router.get("/shop")
async def get_shop(response: Response):
    return await _read_workspace(etsy_workspace.shop, response)


@router.get("/orders")
async def get_orders(response: Response, start: date | None = None, end: date | None = None,
                     offset: int = Query(0, ge=0, le=100000),
                     status: Literal["all", "paid", "unshipped"] = "all"):
    today = datetime.now(UTC).date()
    return await _read_workspace(
        lambda client: etsy_workspace.orders(client, start or today - timedelta(days=29), end or today, offset, status),
        response,
    )


@router.get("/listings")
async def get_listings(response: Response, offset: int = Query(0, ge=0, le=100000),
                       state: Literal["active", "draft", "inactive", "sold_out", "expired", "removed"] = "active"):
    return await _read_workspace(lambda client: etsy_workspace.listings(client, state, offset), response)


@router.get("/listing-profiles")
async def get_listing_profiles():
    """Only profile IDs and labels; never return shipping origins or addresses."""
    try:
        return await etsy_listings.listing_profiles()
    except (ValueError, marketplace_accounts.SignInRequired) as exc:
        raise HTTPException(409, str(exc)) from exc
    except (httpx.HTTPError, RelayError) as exc:
        raise HTTPException(502, "Could not load Etsy listing profiles. Check the Etsy connection.") from exc


class EtsyListingDefaults(BaseModel):
    shipping_profile_id: str = Field(pattern=r"^[1-9]\d*$")
    readiness_state_id: str = Field(pattern=r"^[1-9]\d*$")


@router.get("/listing-defaults")
def get_listing_defaults():
    return user_settings.get_etsy_listing_defaults()


@router.put("/listing-defaults")
async def put_listing_defaults(body: EtsyListingDefaults):
    """Saved once in Settings; only profiles from the connected shop are accepted."""
    try:
        profiles = await etsy_listings.listing_profiles()
    except (ValueError, marketplace_accounts.SignInRequired) as exc:
        raise HTTPException(409, str(exc)) from exc
    except (httpx.HTTPError, RelayError) as exc:
        raise HTTPException(502, "Could not load Etsy listing profiles. Check the Etsy connection.") from exc
    if body.shipping_profile_id not in {p["id"] for p in profiles["shipping"]} or body.readiness_state_id not in {p["id"] for p in profiles["processing"]}:
        raise HTTPException(400, "Choose profiles from the connected Etsy shop.")
    return user_settings.set_etsy_listing_defaults(shipping_profile_id=body.shipping_profile_id, readiness_state_id=body.readiness_state_id)


class EtsyNotAllowedItem(BaseModel):
    conversation_id: str
    title: str
    when_made: str
    who_made: str
    listed_at: str | None = None
    etsy_url: str | None = None
    title_says_vintage: bool = False


class EtsyNotAllowedResponse(BaseModel):
    items: list[EtsyNotAllowedItem]


@router.get("/not-allowed", response_model=EtsyNotAllowedResponse)
def get_not_allowed(db: Session = Depends(get_db)):
    """Read-only: items live on Etsy that Etsy does not allow. Nothing here delists."""
    return EtsyNotAllowedResponse(items=[
        EtsyNotAllowedItem.model_validate(row) for row in etsy_not_allowed_live(db)
    ])
