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
| Poshmark | Native categories, size groups, sizes, conditions, colors and style tags. Private Save Draft creates a `user_draft`, reuses it, checks ownership and reads fields back. Quantity, original price, SKU, cost cents, shipping discount, availability, private notes and Smart Pricing off were exercised in a real private draft. | Custom sizes, inventory with several sizes, video, scheduled availability, and any other controls revealed by the native editor. Confirm the installed release after merging. |
| Mercari | Native categories, size groups, brands, conditions and package-specific prepaid labels. Buyer/seller payer, Smart Pricing and floor read-back. Ship on your own uses the native zero shipping-class sentinel, seller payer and no prepaid-label requirement; the native private draft showed SOYO selected. | Inspect category-dependent fields outside clothing, including device identifiers. Audit the current form for any offer controls before exposing one. No manual tag control was observed on the clothing draft. |
| Depop | Native category attributes, category sizes, brands, conditions, colors, age, source, style and parcel choices. Private draft photo count, SKU, quantity, style edits and clears were checked. Worldwide shipping saved $15.25, saved free shipping as $0.00, then omitted the fee when switched off; the item stayed $12. | Video, remaining category-specific behavior, and installed-release verification of worldwide shipping. |
| eBay | Native category aspects, option IDs, dependent choices and conditions. Offer amounts and explicit offer disablement are carried by the direct API path. The form exposes the offer switch and editable condition description; clearing offer thresholds no longer restores calculated amounts. Trading revise sends DeletedField for cleared offer thresholds and sends or clears condition notes. | Compare the complete native editor, including policy and store-category selectors, format/duration, variations, optional merchandising fields, media and any conditional controls. Trading changes have request-level tests; their saved results need a safe real validation path. |
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
- Mercari's current native selling client: SOYO replaces shipping class IDs
  with `[0]` and removes the prepaid shipping dimension/weight units. The draft
  query represents it without a positive prepaid class ID.
- eBay documents the condition description's applicability and limit in
  [Item conditions](https://developer.ebay.com/api-docs/user-guides/static/trading-user-guide/item-conditions.html).

Private listing identifiers, photos, account responses and diagnostic captures
stay outside the repository. The audit must not publish or delist test items.

## Completion checks

1. Compare visible native controls and conditional states with Anteroom.
2. Confirm option labels, IDs, selection limits, free-text support and clearing.
3. Verify the final saved fields and media, then reopen the native draft where
   the marketplace provides one.
4. Check refusal, interruption and recovery without repeating publication.
5. Repeat on the installed release with the normal bridge before marking a
   marketplace complete.
