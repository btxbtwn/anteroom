# Direct listing form parity

Parity means that a seller can review and change each applicable native listing
field, choose a value the marketplace currently accepts, clear optional values,
and have the saved result match the form. A successful request alone does not
prove parity. Publishing remains a separate, explicitly confirmed action.

This audit covers eBay, Poshmark, Mercari, Depop and Sellwild. **Full parity is
not yet verified.** Category catalogs do not by themselves establish that every
listing control is present.

The scope includes every native listing capability available to the connected
seller, including videos, auctions, variations and inventory with several
sizes. Account eligibility and category restrictions must come from the
marketplace. A control is complete only after its editing, clearing, validation,
serialization and saved result have been checked; displaying it is insufficient.

## Verified controls and remaining work

| Marketplace | Verified behavior | Remaining audit or implementation |
| --- | --- | --- |
| Poshmark | Native categories, size groups, sizes, conditions, colors and style tags. Private Save Draft creates a `user_draft`, reuses it, checks ownership and reads fields back. Quantity, original price, SKU, cost cents, shipping discount, availability, private notes and Smart Pricing off were exercised in a real private draft. Installed 0.1.409 saved standard XL and a custom size with separate quantities and SKUs, then removed the custom row. The candidate video picker uploaded a three-second clip through the file chooser; the private draft returned its video as ready/completed and the native editor played it. Clearing the video saved an empty video list with all six photos retained. Installed 0.1.412 with the normal bridge: a three-second clip selected on the private test item saved through Save Draft; Check passed, Poshmark's post read back `videos` with `status = ready` and `processing_status = completed`, still `user_draft`, six photos and XL quantity 2, and the native editor loaded the 3.0-second clip. Clearing it and saving again read back `videos: []` with six photos. | Updates after some inventory has sold; scheduled availability if offered, and any other controls revealed by the native editor. Video publication and live updates have request-level checks only. Selection and Save Draft in the installed check went through the same local routes the form uses, not clicks in the form. |
| Mercari | Native categories, size groups, brands, conditions and package-specific prepaid labels. Buyer/seller payer, Smart Pricing and floor read-back. Ship on your own uses the native zero shipping-class sentinel, seller payer and no prepaid-label requirement; the native private draft showed SOYO selected. Installed 0.1.409 recovered the private No Brand draft using the full sent snapshot. | Inspect category-dependent fields outside clothing, including device identifiers. Audit the current form for any offer controls before exposing one. No manual tag control was observed on the clothing draft. |
| Depop | Native category attributes, category sizes, brands, conditions, colors, age, source, style and parcel choices. Private draft photo count, SKU, quantity, style edits and clears were checked. Worldwide shipping saved $15.25, saved free shipping as $0.00, then omitted the fee when switched off; the item stayed $12. | Video, remaining category-specific behavior, and installed-release verification of worldwide shipping. |
| eBay | Native category aspects, option IDs, dependent choices and conditions. Offer amounts and explicit offer disablement are carried by the direct API path. The form exposes the offer switch and editable condition description; clearing offer thresholds no longer restores calculated amounts. Trading revise sends DeletedField for cleared offer thresholds and sends or clears condition notes. Format, duration and auctions (0.1.413): Fixed price (Good 'Til Cancelled) or Auction, with auction lengths, starting bid, optional reserve and Buy It Now; the auction choice and lengths come from eBay's listing-type policy for the leaf category. Auctions omit `availableQuantity` and Best Offer and require quantity 1. Save Draft writes the inventory item and an unpublished offer, never publishes, and reads the offer back; List publishes that same offer. Installed 0.1.417 on the private test item: the form read the category's real auction lengths (3, 5, 7 and 10 days), hid offers for an auction, and the pre-flight blocked a missing starting bid. Save Draft saved an unpublished Auction offer (5 days, $8 starting bid, $12 Buy It Now, no reserve, quantity 1), and eBay's offer read back the same; switching to Fixed price saved a separate unpublished Fixed price, Good 'Til Cancelled offer with quantity 2, as eBay keeps one offer per SKU and format. Check confirmed each without writing; neither has a listing ID. (0.1.414 failed after writing by recording a field the listing record does not store; fixed in 0.1.417.) Variations (0.1.420): an item's eBay form lists the aspects eBay enables for variations in its leaf (Size, Color…) and one row per variation with its own quantity, optional SKU and optional price, plus up to 12 photos for each value of one aspect. Save Draft writes one inventory item per variation, the inventory item group and one unpublished fixed-price offer per variation, never publishes, and reads the group and every offer back; List publishes that group once (one listing ID). Update replaces the items, group and offers of the live listing; Delist withdraws the whole group; Sync reads stock per variation. These have request-level tests with an eBay fake. | List of an auction (publication) is unverified, since nothing may be published. Real private Save Draft of a listing with variations; its List, Update and Delist are unverified. Adding a variation to a live listing (refused for now), importing an existing variation listing from eBay (still refused), and the Vendoo send path for variations. Policy and store-category selectors, optional merchandising fields, media and any conditional controls. The Vendoo send path still writes fixed price only. Trading changes have request-level tests; their saved results need a safe real validation path. |
| Sellwild | Native category/condition IDs, vehicle VIN/mileage branch, package validation and explicit activation boundary. Its form exposes category, condition, optional description and the 12 native USPS packaging presets. Preset dimensions and request serialization have tests. | Remaining visible sell-page controls and installed UI verification. Sellwild has no remote draft operation; local editing must not activate a listing. |

Photo selection/order and the marketplace's media limits also need to be checked
per marketplace. A shared General field or account setting must be identified
as such; it must not silently override an explicit marketplace selection.

## Native evidence

- Poshmark's current signed-in web client (2026-10-26),
  `listingEditor.1b6576bf6744b9c84f86.js`,
  `32.67e564255faffef307bd.js` and `app.e95611750f89b1732f92.js`:
  `saveDraft` saves post fields, then `publishAsDraft` sends
  `PUT /vm-rest/posts/{id}/status/user_draft`. The editable link is
  `/edit-listing/{id}`. The shipping-discount choices come from
  `/vm-rest/users/{id}/seller_shipping_discounts/post`.
  The native inventory editor uses `inventory.size_quantities`, including
  `seller_inventory_private_info.sku` per row. Custom sizes have the seller's
  text as their ID and empty `size_set_tags`.
  The same client creates `/vm-rest/posts/{id}/videos`, uploads multipart media
  to `/api/posts/{id}/videos/{videoId}/media`, and submits processing at
  `/vm-rest/posts/{id}/videos/{videoId}` with `input.status = submitted`.
  Its web picker accepts one video strictly shorter than 15 seconds. The post
  stores `videos: [{id}]`; `status = ready` and
  `media.processing_status = completed` were observed on the private test.
- Mercari's current native selling client: SOYO replaces shipping class IDs
  with `[0]` and removes the prepaid shipping dimension/weight units. The draft
  query represents it without a positive prepaid class ID.
- eBay's Inventory API (OpenAPI 1.18.5, read 2026-10-26): offer `format` is
  `FIXED_PRICE` or `AUCTION`; fixed price must use `listingDuration = GTC`;
  an auction offer does not carry `availableQuantity`; the starting bid is
  `pricingSummary.auctionStartPrice`, the optional reserve
  `auctionReservePrice` (must exceed the starting bid, and eBay charges for
  it), and `pricingSummary.price` on an auction is Buy It Now. Best Offer is
  unavailable for multi-variation listings. eBay's
  [Listing creation guide](https://developer.ebay.com/develop/guides-v2/listing-creation)
  says multi-variation offers must be `FIXED_PRICE` and `GTC`.
  The Metadata API's `get_listing_type_policies` (OpenAPI 1.12.1) lists each
  leaf category's listing types and durations; a type it leaves out is not
  accepted there. eBay's
  [Buy It Now guide](https://developer.ebay.com/api-docs/user-guides/static/trading-user-guide/buy-it-now.html)
  asks for Buy It Now at least 30% above the starting bid in most categories;
  Anteroom states that and leaves the per-category rule to eBay.
- eBay variations (Inventory API OpenAPI 1.18.5, Metadata API 1.12.1, Taxonomy
  API 1.1.1, read 2026-10-26). `createOrReplaceInventoryItemGroup` takes the
  listing's `title`, `description`, `imageUrls` and shared `aspects`, the member
  `variantSKUs`, and `variesBy.specifications` (each aspect with two or more
  values) and `variesBy.aspectsImageVariesBy`, which the spec marks required
  before publishing; it is a full replace, and on a live group it updates the
  listing, removing a variation whose SKU is left out unless it has sales.
  Member items should omit `title`, and their offers `listingDescription`,
  because those would override the group's. A group may use up to 12 pictures
  per item. `publishOfferByInventoryItemGroup` turns every unpublished offer of
  the group into one multi-variation listing and returns its `listingId`;
  `withdrawOfferByInventoryItemGroup` ends that listing and leaves the offers
  unpublished, so List can publish them again. `getListingFees` prices up to
  250 unpublished offers and is not called yet. `ListingPolicies.bestOfferTerms`:
  Best Offer is unavailable for multi-variation listings. Metadata
  `getListingStructurePolicies` returns `variationsSupported` per category, and
  Taxonomy `getItemAspectsForCategory` returns `aspectEnabledForVariations` per
  aspect; Anteroom checks both before writing. eBay's
  [inventory item group reference](https://developer.ebay.com/api-docs/sell/inventory/resources/inventory_item/group/methods/createOrReplaceInventoryItemGroup),
  [publish by group](https://developer.ebay.com/api-docs/sell/inventory/resources/offer/methods/publishOfferByInventoryItemGroup),
  [withdraw by group](https://developer.ebay.com/api-docs/sell/inventory/resources/offer/methods/withdrawOfferByInventoryItemGroup),
  [listing structure policies](https://developer.ebay.com/api-docs/sell/metadata/resources/marketplace/methods/getListingStructurePolicies)
  and [item aspects](https://developer.ebay.com/api-docs/commerce/taxonomy/resources/category_tree/methods/getItemAspectsForCategory).
  A sale notice carries only the order and listing IDs, so a variation sale is
  never read as the whole item selling out: Sync reads each variation's
  `Quantity` minus `QuantitySold` from `GetItem` and stores the per-SKU stock.
  Importing a variation listing from eBay is still refused, and Trading API
  revise cannot change a listing made through the Inventory API, so a group is
  only updated through its group, items and offers.
- eBay documents the condition description's applicability and limit in
  [Item conditions](https://developer.ebay.com/api-docs/user-guides/static/trading-user-guide/item-conditions.html).

Private listing identifiers, photos, account responses and diagnostic captures
stay outside the repository. The audit must not publish or delist test items.

## Sharing, offers and refreshing

Seller tools on live listings, checked 2026-10-26. Each action is started by the
seller from a selection (sidebar → **Share / offers**), names the marketplace
and the action, is reviewed per item and confirmed once. Items then run one at
a time holding the automation slot. Every send is journaled in Anteroom
(`listing_promotions`) and in the extension before it is made; a lost reply is
**unknown** and is never repeated, and **Check result** only reads the
extension's journal. Nothing here is scheduled, and nothing lists or delists.

| Marketplace | Tool | Status | Source |
| --- | --- | --- | --- |
| Poshmark | Share to followers (one or selected listings) | Implemented, unit-tested, not yet verified on a real listing | Web client `app.e95611750f89b1732f92.js`: `sharePost` sends `PUT /vm-rest/users/self/shared_posts/{post}` and handles `SharePostRequestLimitExceededError`, `ValidationError` and a `share_post` captcha. Anteroom spaces shares 4–7 seconds apart and stops the batch at the first limit or check. |
| Poshmark | Offer to likers, with a required floor per listing | Implemented, unit-tested, not yet verified on a real listing | Same client and chunk `53.2a6596cdc30f9542d899.js` (offer modal): `makeSellerOffer` sends `POST /vm-rest/posts/{post}/likes/users/offers` with `offer_amount {val, currency_code}`, `seller_shipping_discount {id}` and `offer_api_version: 3`. Maximum offer is `floor(price × (100 − minimum_seller_offer_discount_percent) / 100)` in whole dollars; the public `/vm-rest/meta/feature_settings?client_app=web_feature_settings` `offer_to_likers__us` reads 10% minimum, 79% maximum (Anteroom takes the strictest enabled variant). Shipping discounts come from `/vm-rest/users/{id}/seller_shipping_discounts/offer?price_amount=…&object_id={post}`; the seller chooses one or none per offer. Anteroom also refuses an offer on a listing with no likers and allows one offer per listing per 24 hours. |
| Mercari | Promote (price drop of at least 5% that notifies likers, once per 24 hours) and Offer to likers (at least 10% off, up to 50 recent likers, 24 hours) | Exists on Mercari; not implemented | [How does the Promote button work?](https://www.mercari.com/us/help_center/article/347/). The web client's persisted GraphQL operations for these could not be read: mercari.com answers a plain fetch with a Cloudflare check, and this work had no browser session. Capture the operation names and hashes from the signed-in web client before adding them. No separate refresh exists. |
| Depop | Refresh / bump | Not supported by Depop | Depop's help centre has no free bump; it recommends refreshing photos, description and price, and sells [Boosted Listings](https://depophelp.zendesk.com/hc/en-gb/articles/10110299060753-Boosted-Listings) (paid, fee on the sale). [Repop](https://depophelp.zendesk.com/hc/en-gb/articles/12803766422289-Repop) relists a sold item. Re-saving a listing to fake a bump is not added. |
| Depop | Send offer to likers | Exists on Depop; not implemented | [Send Offer](https://depophelp.zendesk.com/hc/en-gb/articles/15495796917777-Send-Offer): from notifications or My Depop, to one or all likers in the seller's country, one active offer per buyer, not for multi-size listings, 14 days to respond, shipping not included. depop.com answered 403 to a plain fetch, so the web request could not be read here. |
| Etsy | Offers to favoriters | Not available through Etsy's API | Set in Shop Manager → Marketing → Sales and discounts ([help](https://help.etsy.com/hc/en-us/articles/115014260108-How-to-Set-Up-Sales-and-Discounts-for-Your-Shop)); Etsy sends them automatically. The Open API v3 spec (`/openapi/generated/oas/3.0.0.json`, read 2026-10-26) has no offer, coupon or manual renew endpoint, only `should_auto_renew` on a listing. |
| Vinted | Bump | Paid, not added | [What is an item Bump?](https://www.vinted.com/help/340-what-is-item-bump) and [Buying a Bump](https://www.vinted.com/help/450-buying-a-bump): a paid 3 or 7 day boost bought at checkout. Anteroom does not make purchases. |
| Sellwild | Share, bump, offers to likers | None found | Sellwild's seller guides list [discounts](https://sellwild.com/guides/seller/my-discounts) (timed price drops with a floor), [coupons](https://sellwild.com/guides/seller/coupons) and BOGO, not sharing or bumping. |
| eBay | Offers to interested buyers | Implemented earlier (Offers tab) | `services/ebay_offers.py`, eBay Negotiation API. |

## Completion checks

1. Compare visible native controls and conditional states with Anteroom.
2. Confirm option labels, IDs, selection limits, free-text support and clearing.
3. Verify the final saved fields and media, then reopen the native draft where
   the marketplace provides one.
4. Check refusal, interruption and recovery without repeating publication.
5. Repeat on the installed release with the normal bridge before marking a
   marketplace complete.
