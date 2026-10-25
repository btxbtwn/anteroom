"""eBay's and Etsy's own category fields, as the shared field shape."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException

from vendoo_studio.services import marketplace_schemas
from vendoo_studio.services.ebay_client import EbayFailure
from vendoo_studio.services.marketplace_accounts import SignInRequired
from vendoo_studio.services.marketplace_schemas import SchemaMarketplace
from vendoo_studio.services.relay_client import RelayError

router = APIRouter(prefix="/api/marketplace-schemas", tags=["marketplace-schemas"])


@router.get("/{marketplace}/{category_id}")
async def category_fields(marketplace: SchemaMarketplace, category_id: str):
    try:
        return {"marketplace": marketplace, "category_id": category_id,
                "fields": await marketplace_schemas.fields(marketplace, category_id)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except SignInRequired as exc:
        raise HTTPException(409, f"Connect {'eBay' if marketplace == 'ebay' else 'Etsy'} in Settings → Connections first.") from exc
    except (EbayFailure, RelayError, httpx.HTTPError) as exc:
        raise HTTPException(502, "Could not read the marketplace's category fields. Check the connection and try again.") from exc
