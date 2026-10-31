# Etsy

Official reference: https://developer.etsy.com/documentation/reference/

Local contract: [Open API v3](specs/etsy.json). API paths below are relative to
`/v3/application`. Authentication uses the application key and seller OAuth; the
repository keeps shared app credentials in the Cloudflare relay.

| Purpose | Method | Path |
| --- | --- | --- |
| Create private draft | POST | `/shops/{shop_id}/listings` |
| Read listing | GET | `/listings/{listing_id}` |
| Update fields or activate | PATCH | `/shops/{shop_id}/listings/{listing_id}` |
| Write category property | PUT | `/shops/{shop_id}/listings/{listing_id}/properties/{property_id}` |
| Read all category properties | GET | `/shops/{shop_id}/listings/{listing_id}/properties` |
| Read inventory | GET | `/listings/{listing_id}/inventory` |
| Update inventory and price | PUT | `/listings/{listing_id}/inventory` |
| Upload image | POST | `/shops/{shop_id}/listings/{listing_id}/images` |

The collection property read requires the shop ID even though the single-property
read uses `/listings/{listing_id}/properties/{property_id}`. Keep those distinct.
Saving a draft writes properties, verifies them, uploads media, then verifies the
draft. Activation is a separate approved action. Resume a retained draft URL rather
than create another draft after a partial save.

Implementation: [listing service](../../vendoo-studio/server/vendoo_studio/services/etsy_listings.py),
[account transport](../../vendoo-studio/server/vendoo_studio/services/marketplace_accounts.py),
[relay](../../webhook-relay/src/etsy.js).
Regression coverage: [listing tests](../../vendoo-studio/server/tests/test_etsy_listings.py).
