"""eBay and Etsy sign-in on the web, through Anteroom's relay.

Each seller registers as their own relay device; tokens live in that seller's
Vault through the same keychain boundary the Mac uses. The seller's browser
opens the sign-in page. Sales are not streamed here yet, and nothing here
lists, delists or changes a marketplace listing.
"""
from fastapi import APIRouter

from vendoo_studio.routes import ebay, etsy, marketplace_accounts

router = APIRouter()
for path, handler, methods in (
    ('/api/marketplace-accounts', marketplace_accounts.get_status, ['GET']),
    ('/api/marketplace-accounts/{marketplace}/connect', marketplace_accounts.connect, ['POST']),
    ('/api/marketplace-accounts/{marketplace}/connect', marketplace_accounts.cancel_connect, ['DELETE']),
    ('/api/marketplace-accounts/{marketplace}', marketplace_accounts.disconnect, ['DELETE']),
    ('/api/ebay/listing-profiles', ebay.listing_profiles, ['GET']),
    ('/api/ebay/listing-defaults', ebay.get_listing_defaults, ['GET']),
    ('/api/ebay/listing-defaults', ebay.put_listing_defaults, ['PUT']),
    ('/api/etsy/listing-profiles', etsy.get_listing_profiles, ['GET']),
    ('/api/etsy/listing-defaults', etsy.get_listing_defaults, ['GET']),
    ('/api/etsy/listing-defaults', etsy.put_listing_defaults, ['PUT']),
):
    router.add_api_route(path, handler, methods=methods)
