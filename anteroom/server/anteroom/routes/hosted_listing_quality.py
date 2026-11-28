"""Seller consent for hosted listing quality measurement."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, StrictBool

from anteroom.services import listing_quality
from anteroom.services.hosted_database import SellerDatabaseSession, get_seller_db
from anteroom.services.hosted_limits import bounded_route_class

router = APIRouter(
    prefix="/api/listing-quality",
    route_class=bounded_route_class(lambda: 1024, too_large_detail="Request body is too large"),
)


class Consent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool


@router.get("/consent")
def get_consent(db: SellerDatabaseSession = Depends(get_seller_db, scope="function")) -> Consent:
    return Consent(enabled=listing_quality.consent_enabled(db))


@router.put("/consent")
def put_consent(body: Consent,
                db: SellerDatabaseSession = Depends(get_seller_db, scope="function")) -> Consent:
    listing_quality.set_consent(db, body.enabled)
    return body
