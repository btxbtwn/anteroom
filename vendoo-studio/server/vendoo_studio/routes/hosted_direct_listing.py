"""List, update and delist from the web: eBay and Etsy through their APIs, and
Poshmark, Mercari, Depop, Vinted and Sellwild through the seller's own Chrome.

These are the desktop handlers, run in the signed-in seller's context with
their own tokens. Every action is one the seller starts: List and Update carry
``confirm``, Delist names its marketplaces and carries ``confirm``, and nothing
here runs on a timer or a retry. Chrome actions go through the seller's
signed-in Anteroom tab (``services/hosted_browser_relay.py``), and so do the
reads of Vinted's own categories and brands, of the seller's Sellwild account
choices, and of a linked Facebook listing's page.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.routes import marketplace_listings, native_marketplaces
from vendoo_studio.routes.vendoo_api import leaf_form
from vendoo_studio.services.native_marketplaces import CheckRequest, ListRequest

# The rail's actions, as on the Mac. Facebook has no List: the seller links an item already on
# Facebook, checks it in Chrome, and deletes it on Facebook themselves.
ACTION_MARKETPLACES = ('ebay', 'etsy', 'poshmark', 'mercari', 'depop', 'vinted', 'sellwild', 'facebook')
Direct = Literal['ebay', 'etsy', 'poshmark', 'mercari', 'depop', 'vinted', 'sellwild']
PREFIX = '/api/conversations/{conv_id}'

router = APIRouter()
for path, handler, methods in (
    ('/native-marketplaces', native_marketplaces.status, ['GET']),
    ('/marketplace-listings', marketplace_listings.listings, ['GET']),
    ('/marketplace-listings/{marketplace}/mark', marketplace_listings.mark, ['POST']),
    ('/marketplace-listings/sold', marketplace_listings.record_sale, ['POST']),
    ('/marketplace-listings/facebook/check', marketplace_listings.check_facebook, ['POST']),
    ('/native-marketplaces/vinted/categories', native_marketplaces.vinted_categories, ['GET']),
    ('/native-marketplaces/vinted/brands', native_marketplaces.vinted_brands, ['GET']),
    ('/native-marketplaces/sellwild/options', native_marketplaces.sellwild_options, ['GET']),
):
    router.add_api_route(PREFIX + path, handler, methods=methods)


@router.get(PREFIX + '/native-marketplaces/preflight')
def preflight(conv_id: str, sellwild_category_id: int | None = Query(None, ge=1),
              sellwild_condition_id: int | None = Query(None, ge=1), db: Session = Depends(get_db)):
    return native_marketplaces.preflight(conv_id, sellwild_category_id, sellwild_condition_id, db)


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
def reset(conv_id: str, marketplace: Direct, body: native_marketplaces.ResetRequest, db: Session = Depends(get_db)):
    return native_marketplaces.reset(conv_id, marketplace, body, db)


router.add_api_route(PREFIX + '/marketplace-listings/delist', marketplace_listings.delist, methods=['POST'])


@router.get(PREFIX + '/vendoo-api/fields')
async def listing_fields(conv_id: str, db: Session = Depends(get_db)):
    """The Etsy form: the category's saved fields and Etsy's own properties, built
    as on the Mac. The web never asks Chrome for a schema, so an unread leaf shows
    Etsy's live properties and the listing's own values."""
    from vendoo_studio.models.listing_values import marketplace_dropdown_forms
    from vendoo_studio.services.category_fields import listing_category_ids, load_fields

    if not ConversationRepo(db).get(conv_id):
        raise HTTPException(404, 'Conversation not found')
    revisions = ListingRepo(db).get_revisions(conv_id)
    listing = revisions[0].listing_json if revisions else {}
    db.commit()
    category_id = listing_category_ids(listing).get('etsy')
    if not category_id:
        return {'ok': True, 'forms': []}
    static = marketplace_dropdown_forms().get('etsy') or {}
    return {'ok': True, 'forms': [await leaf_form(listing, 'etsy', category_id, load_fields('etsy', category_id) or {}, static)]}
