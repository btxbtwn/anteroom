"""The Marketplaces page on the web: eBay and Etsy workspaces, offers, imports and sale alerts.

Every handler is the desktop one, running in the signed-in seller's context with
their own eBay and Etsy tokens. eBay account changes keep the desktop's review
then confirm steps, and offers keep their preview and approval steps; the web
offer timer (``hosted_offer_scheduler``) checks approved automatic offers.
Nothing here lists, delists or relists.
"""
from fastapi import APIRouter

from vendoo_studio.routes import ebay, ebay_offers, etsy, marketplace_accounts

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
    ('/api/ebay-offers', ebay_offers.status, ['GET']),
    ('/api/ebay-offers/preview', ebay_offers.preview, ['POST']),
    ('/api/ebay-offers/send', ebay_offers.send, ['POST']),
    ('/api/ebay-offers/enable', ebay_offers.enable, ['POST']),
    ('/api/ebay-offers/pause', ebay_offers.pause_all, ['POST']),
    ('/api/ebay-offers/enable-all', ebay_offers.enable_all, ['POST']),
    ('/api/ebay-offers/rules/{rule_id}/pause', ebay_offers.pause, ['POST']),
    ('/api/ebay-offers/attempts/{attempt_id}/checked', ebay_offers.resolve, ['POST']),
):
    router.add_api_route(path, handler, methods=methods)
