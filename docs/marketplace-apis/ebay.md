# eBay

Official API index: https://developer.ebay.com/develop/apis
Listing workflow: https://developer.ebay.com/api-docs/sell/inventory/overview.html
Authentication: https://developer.ebay.com/api-docs/static/oauth-tokens.html

Local contracts in `specs/`: `ebay-inventory.json`, `ebay-account.json`,
`ebay-taxonomy.json`, `ebay-metadata.json`, `ebay-fulfillment.json`, and
`ebay-media.json`. These cover the core listing, policy, category, order, and media
interfaces, not the entire eBay API portfolio. Each contract carries its own server
URL, operations, schemas, and OAuth requirements.

Inventory items and offers are separate resources. Saving inventory or creating an
offer does not authorize publishing it. Preserve the existing SKU and offer IDs
during recovery. A published listing may require different handling depending on
whether it originated in Inventory or Trading APIs; do not assume every imported
listing is an Inventory API offer.

Additional official references used by other features:

- Trading: https://developer.ebay.com/devzone/xml/docs/reference/ebay/index.html
- Marketing: https://developer.ebay.com/api-docs/sell/marketing/overview.html
- Negotiation: https://developer.ebay.com/api-docs/sell/negotiation/overview.html
- Finances: https://developer.ebay.com/api-docs/sell/finances/overview.html

Implementation: [client](../../vendoo-studio/server/vendoo_studio/services/ebay_client.py),
[listing lifecycle](../../vendoo-studio/server/vendoo_studio/services/ebay_listings.py),
[variations](../../vendoo-studio/server/vendoo_studio/services/ebay_variations.py).
