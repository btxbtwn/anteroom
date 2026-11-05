"""List, update and delist from the web: eBay and Etsy through their APIs, and
Poshmark, Mercari and Depop through the seller's own Chrome.

These are the desktop handlers, run in the signed-in seller's context with
their own tokens. Every action is one the seller starts: List and Update carry
``confirm``, Delist names its marketplaces and carries ``confirm``, and nothing
here runs on a timer or a retry. Chrome actions go through the seller's
signed-in Anteroom tab (``services/hosted_browser_relay.py``).
"""
import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from vendoo_studio.database import get_db
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.routes import marketplace_listings, native_marketplaces
from vendoo_studio.routes.vendoo_api import leaf_form
from vendoo_studio.services.native_marketplaces import CheckRequest, ListRequest

# The rail's actions, as on the Mac.
ACTION_MARKETPLACES = ('ebay', 'etsy', 'poshmark', 'mercari', 'depop')
Direct = Literal['ebay', 'etsy', 'poshmark', 'mercari', 'depop']
PREFIX = '/api/conversations/{conv_id}'

router = APIRouter()
for path, handler, methods in (
    ('/native-marketplaces', native_marketplaces.status, ['GET']),
    ('/marketplace-listings', marketplace_listings.listings, ['GET']),
    ('/marketplace-listings/{marketplace}/mark', marketplace_listings.mark, ['POST']),
    ('/marketplace-listings/sold', marketplace_listings.record_sale, ['POST']),
    ('/marketplace-listings/check-sales', marketplace_listings.check_sales, ['POST']),
):
    router.add_api_route(PREFIX + path, handler, methods=methods)


@router.get(PREFIX + '/native-marketplaces/preflight')
def preflight(conv_id: str, db: Session = Depends(get_db, scope="function")):
    return native_marketplaces.preflight(conv_id, db)


@router.post(PREFIX + '/native-marketplaces/{marketplace}/list')
async def list_now(conv_id: str, marketplace: Direct, body: ListRequest, db: Session = Depends(get_db, scope="function")):
    return await native_marketplaces.list_now(conv_id, marketplace, body, db)


@router.post(PREFIX + '/native-marketplaces/{marketplace}/update')
async def update_live(conv_id: str, marketplace: Direct, body: ListRequest, db: Session = Depends(get_db, scope="function")):
    return await native_marketplaces.update_live(conv_id, marketplace, body, db)


@router.post(PREFIX + '/native-marketplaces/{marketplace}/draft')
async def save_draft(conv_id: str, marketplace: Direct, body: native_marketplaces.SaveDraftRequest, db: Session = Depends(get_db, scope="function")):
    return await native_marketplaces.save_draft(conv_id, marketplace, body, db)


@router.post(PREFIX + '/native-marketplaces/{marketplace}/cancel')
async def cancel(conv_id: str, marketplace: Direct, db: Session = Depends(get_db, scope="function")):
    return await native_marketplaces.cancel(conv_id, marketplace, db)


@router.post(PREFIX + '/native-marketplaces/{marketplace}/check')
async def check_result(conv_id: str, marketplace: Direct, body: CheckRequest | None = None, db: Session = Depends(get_db, scope="function")):
    return await native_marketplaces.check_result(conv_id, marketplace, body, db)


@router.post(PREFIX + '/native-marketplaces/{marketplace}/reset')
def reset(conv_id: str, marketplace: Direct, body: native_marketplaces.ResetRequest, db: Session = Depends(get_db, scope="function")):
    return native_marketplaces.reset(conv_id, marketplace, body, db)


router.add_api_route(PREFIX + '/marketplace-listings/delist', marketplace_listings.delist, methods=['POST'])


@router.get(PREFIX + '/vendoo-api/fields')
async def listing_fields(conv_id: str, db: Session = Depends(get_db, scope="function")):
    """The Etsy form: the category's saved fields and Etsy's own properties, built
    as on the Mac. The web never asks Chrome for a schema, so an unread leaf shows
    Etsy's live properties and the listing's own values."""
    from vendoo_studio.services import listing_mode
    if listing_mode.read(db) == "vendoo":
        from vendoo_studio.routes.vendoo_api import listing_fields as vendoo_fields
        return await vendoo_fields(conv_id, db)
    from vendoo_studio.models.listing_values import marketplace_dropdown_forms
    from vendoo_studio.services.category_fields import listing_category_ids, load_fields

    def load():
        # Database reads and Etsy's token lookup block; keep them off the request loop.
        if not ConversationRepo(db).get(conv_id):
            raise HTTPException(404, 'Conversation not found')
        # The newest version only: older ones can each be hundreds of KB.
        latest = ListingRepo(db).latest_revision(conv_id)
        listing = latest.listing_json if latest else {}
        db.commit()
        category_id = listing_category_ids(listing).get('etsy')
        if not category_id:
            return {'ok': True, 'forms': []}
        static = marketplace_dropdown_forms().get('etsy') or {}
        return {'ok': True, 'forms': [asyncio.run(leaf_form(listing, 'etsy', category_id, load_fields('etsy', category_id) or {}, static))]}
    return await run_in_threadpool(load)
