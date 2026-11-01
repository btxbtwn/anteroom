"""The Marketplaces page on the web: eBay and Etsy workspaces, imports and sale alerts.

Every handler is the desktop one, running in the signed-in seller's context with
their own eBay and Etsy tokens. eBay account changes keep the desktop's review
then confirm steps. Nothing here lists, delists or relists, and automatic eBay
offers are not scheduled on the web yet.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.routes import ebay, etsy, marketplace_accounts
from vendoo_studio.services import ebay_offers

router = APIRouter()
for path, handler, methods in (
    ('/api/ebay/read/{section}', ebay.read, ['GET']),
    ('/api/ebay/listing/{listing_id}', ebay.listing, ['GET']),
    ('/api/ebay/import/active', ebay.import_candidates, ['GET']),
    ('/api/ebay/import', ebay.import_item, ['POST']),
    ('/api/ebay/research/image', ebay.image_search, ['POST']),
    ('/api/ebay/actions/preview', ebay.preview, ['POST']),
    ('/api/ebay/actions/confirm', ebay.confirm, ['POST']),
    ('/api/ebay/actions/{action_id}/checked', ebay.checked, ['POST']),
    ('/api/etsy/shop', etsy.get_shop, ['GET']),
    ('/api/etsy/orders', etsy.get_orders, ['GET']),
    ('/api/etsy/listings', etsy.get_listings, ['GET']),
    ('/api/etsy/import/active', etsy.import_candidates, ['GET']),
    ('/api/etsy/import', etsy.import_item, ['POST']),
    ('/api/etsy/not-allowed', etsy.get_not_allowed, ['GET']),
    ('/api/marketplace-sales', marketplace_accounts.list_sales, ['GET']),
    ('/api/marketplace-sales/recover', marketplace_accounts.recover_orders, ['POST']),
    ('/api/marketplace-sales/reconcile', marketplace_accounts.reconcile_sales, ['POST']),
    ('/api/marketplace-sales/{sale_id}/dismiss', marketplace_accounts.dismiss_sale, ['POST']),
):
    router.add_api_route(path, handler, methods=methods)


@router.get('/api/ebay-offers')
def offer_listings(db: Session = Depends(get_db)):
    """The seller's eBay listings for the markdown picker. The automatic-offer
    timer's last check belongs to the Mac process, so the web leaves it out."""
    return {**ebay_offers.status(db), 'lastCheck': None, 'lastError': None}
