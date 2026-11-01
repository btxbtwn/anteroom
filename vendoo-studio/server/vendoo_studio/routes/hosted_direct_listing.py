"""List, update and delist on eBay from the web, through its official API.

These are the desktop handlers, run in the signed-in seller's context with
their own tokens. Every action is one the seller starts: List and Update carry
``confirm``, Delist names its marketplaces and carries ``confirm``, and nothing
here runs on a timer or a retry. Etsy follows once its form is on the web.
Marketplaces reached only through Chrome (Poshmark, Mercari, Depop, Sellwild,
Vinted) cannot list, update or delist here; Poshmark, Mercari and Depop save
drafts through the seller's Chrome (``hosted_browser_operations.py``).
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.routes import marketplace_listings, native_marketplaces
from vendoo_studio.services.native_marketplaces import NAMES, CheckRequest, ListRequest

ACTION_MARKETPLACES = ('ebay',)
Direct = Literal['ebay']
# A web Save draft on these runs in the seller's Chrome; "I checked it myself" settles one left unconfirmed.
Resettable = Literal['ebay', 'poshmark', 'mercari', 'depop']
PREFIX = '/api/conversations/{conv_id}'

router = APIRouter()
for path, handler, methods in (
    ('/native-marketplaces', native_marketplaces.status, ['GET']),
    ('/marketplace-listings', marketplace_listings.listings, ['GET']),
    ('/marketplace-listings/{marketplace}/mark', marketplace_listings.mark, ['POST']),
    ('/marketplace-listings/sold', marketplace_listings.record_sale, ['POST']),
):
    router.add_api_route(PREFIX + path, handler, methods=methods)


@router.get(PREFIX + '/native-marketplaces/preflight')
async def preflight(conv_id: str, sellwild_category_id: int | None = Query(None, ge=1),
                    sellwild_condition_id: int | None = Query(None, ge=1), db: Session = Depends(get_db)):
    return await native_marketplaces.preflight(conv_id, sellwild_category_id, sellwild_condition_id, db)


@router.post(PREFIX + '/native-marketplaces/{marketplace}/list')
async def list_now(conv_id: str, marketplace: Direct, body: ListRequest, db: Session = Depends(get_db)):
    return await native_marketplaces.list_now(conv_id, marketplace, body, db)


@router.post(PREFIX + '/native-marketplaces/{marketplace}/update')
async def update_live(conv_id: str, marketplace: Direct, body: ListRequest, db: Session = Depends(get_db)):
    return await native_marketplaces.update_live(conv_id, marketplace, body, db)


@router.post(PREFIX + '/native-marketplaces/{marketplace}/draft')
async def save_draft(conv_id: str, marketplace: Direct, body: native_marketplaces.SaveDraftRequest, db: Session = Depends(get_db)):
    return await native_marketplaces.save_draft(conv_id, marketplace, body, db)


@router.post(PREFIX + '/native-marketplaces/{marketplace}/cancel')
async def cancel(conv_id: str, marketplace: Direct, db: Session = Depends(get_db)):
    return await native_marketplaces.cancel(conv_id, marketplace, db)


@router.post(PREFIX + '/native-marketplaces/{marketplace}/check')
async def check_result(conv_id: str, marketplace: Direct, body: CheckRequest | None = None, db: Session = Depends(get_db)):
    return await native_marketplaces.check_result(conv_id, marketplace, body, db)


@router.post(PREFIX + '/native-marketplaces/{marketplace}/reset')
def reset(conv_id: str, marketplace: Resettable, body: native_marketplaces.ResetRequest, db: Session = Depends(get_db)):
    return native_marketplaces.reset(conv_id, marketplace, body, db)


@router.post(PREFIX + '/marketplace-listings/delist')
async def delist(conv_id: str, body: marketplace_listings.DelistRequest, db: Session = Depends(get_db)):
    browser_only = [name for name in body.marketplaces if name not in ACTION_MARKETPLACES]
    if browser_only:
        names = ', '.join(NAMES.get(name, name) for name in browser_only)
        raise HTTPException(409, f'Delist from {names} in the Mac app for now. The web app delists from eBay.')
    return await marketplace_listings.delist(conv_id, body, db)
