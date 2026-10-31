# Marketplace API references

Checked 2026-10-31. Start with the marketplace page below before changing an API call.
Downloaded OpenAPI contracts live in `specs/`; `sources.json` records their original
URLs, retrieval times, upstream versions, and SHA-256 checksums. Depop's downloaded
credential examples are replaced with explicit placeholders; its manifest entry
also retains the upstream checksum and describes that transformation. They are reference
snapshots, not runtime dependencies. Upstream documentation can change after retrieval.

| Marketplace | Local reference | Official contract snapshot | Current Anteroom integration |
| --- | --- | --- | --- |
| eBay | [eBay](ebay.md) | Inventory, Account, Taxonomy, Metadata, Fulfillment, Media | Official seller APIs |
| Depop | [Depop](depop.md) | Partner Selling API | Signed-in web endpoints |
| Poshmark | [Poshmark](poshmark.md) | No public seller contract located | Signed-in web endpoints |
| Etsy | [Etsy](etsy.md) | Open API v3 | Official Open API through the relay |
| Mercari | [Mercari](mercari.md) | No US consumer seller contract located | Signed-in US web endpoints |
| Vinted | [Vinted](vinted.md) | Pro Integrations | Signed-in consumer web endpoints |
| Sellwild | [Sellwild](sellwild.md) | No public listing contract located | Signed-in web endpoints |

The private endpoint notes describe this repository's implementation. They are not
marketplace guarantees. Depop partner access and Vinted Pro access do not imply that
this seller or app has been approved. Mercari Shops Japan is a separate product from
Mercari US. No providers or authentication flows are changed by storing these docs.

## Refreshing a contract

Use the exact `url` in `sources.json` to download the upstream file into a temporary
directory. Parse it as JSON or YAML and require `openapi`, `info`, and `paths` before
replacing the matching snapshot. Review endpoint, authentication, and schema changes;
replace credential-like examples with explicit placeholders, then update the retrieval
time, upstream version, and local SHA-256 in the manifest in the same change. Record
the upstream checksum and any transformations for modified snapshots. Preserve
upstream notices and terms; these files remain upstream material.
Do not copy cookies, access tokens, HAR captures, or seller data into this directory.

For example, the Etsy draft bug was a valid write followed by an invalid read:
`GET /v3/application/listings/{listing_id}/properties` does not exist in the
contract. The documented collection read includes `shops/{shop_id}`. Check method
and full path in a stored contract, not only the request payload.

Repository policy still applies: generation and unattended automation stop at a
saved draft. Publishing and delisting require explicit seller approval for named
marketplaces. Upstream examples do not authorize live writes.
