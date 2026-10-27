# Vendoo feature parity

What the seller keeps, and what they lose, if they cancel Vendoo today and run
everything from Anteroom. Audited 2026-10-26 against `origin/main` at
`95d9ecb2` (Anteroom 0.1.416). Every Anteroom claim below was checked in code,
not only in docs. Every Vendoo claim cites a Vendoo page.

**Status key:** **Have**: Anteroom does it today, with evidence. **Partial**:
some of it, with what is missing. **Missing**: Anteroom does not do it.
**N/A**: not needed, with the reason.

The seller lists on eBay, Poshmark, Mercari, Depop, Etsy, Vinted and Sellwild
(`settings.json → marketplaces`, the **Where you list** setting). Those are the
seven marketplaces Anteroom connects directly
(`vendoo-studio/server/vendoo_studio/services/native_marketplaces.py`,
`Marketplace = Literal[...]`).

## Summary

Anteroom already covers Vendoo's core: listing on every marketplace the
seller uses, delisting and relisting (single and bulk), sale detection with
optional auto-delist, templates, bulk edit, bulk copy, labels, multi-quantity,
CSV exports, eBay offers and AI listing generation, which goes further than
Vendoo's. The losses on cancelling today are:

1. ~~**Inventory status and Analytics still read Vendoo's data.**~~ Fixed in
   0.1.422: a sale Anteroom records turns the item Sold (or "1 of 3 sold") and
   counts in Analytics.
2. **23 items are still live on Facebook Marketplace through Vendoo.**
   Anteroom can neither detect a sale there nor delist them.
3. **Poshmark sharing, Depop refresh and offers to likers** on Poshmark,
   Mercari and Depop.
4. **Photo editing beyond rotate**: background removal, crop and adjustments.

The full prioritized list is in [Gaps if Vendoo is cancelled today](#gaps-if-vendoo-is-cancelled-today).

## Vendoo plans (reference)

| Plan | Price (monthly / yearly per month) | What it adds | Source |
| --- | --- | --- | --- |
| Starter | $14.99 / $12.49 | Unlimited items, all marketplaces, sale detection and auto-delist, templates, import, delist/relist, analytics, mobile app, 0 background removals | [Pricing](https://www.vendoo.co/pricing) |
| Growth | $29.99 / $24.99 | AI Listing Enhancement, bulk actions up to 240 listings, 300 PhotoRoom removals | [Pricing](https://www.vendoo.co/pricing) |
| Pro | $59.99 / $49.99 | Auto offers on 6 marketplaces, Marketplace Sharing (Poshmark, Depop, Grailed), 1,500 removals, listing videos (Poshmark, eBay) | [Pricing](https://www.vendoo.co/pricing) |
| Enterprise | Custom | 1,000+ new listings a month | [Pricing](https://www.vendoo.co/pricing) |
| Add-ons | — | Importing, Delist/Relist, All Marketplaces (3 marketplaces without it); Marketplace Sharing $9.99/mo; Shopify $9.99/mo from 2025-10-15; 20 extra removals $3.19 | [Add-ons](https://help.vendoo.co/en/articles/6260301-what-is-the-purpose-of-each-add-on-feature), [Sharing](https://help.vendoo.co/en/articles/11003940-marketplace-sharing-tool-for-poshmark-depop-and-grailed), [Home](https://www.vendoo.co/), [Photo tools](https://help.vendoo.co/en/articles/8011114-photo-editing-tools-background-removal-by-photoroom) |

Vendoo's 2026 redesign moved Labels, Export CSV, Import, Template Manager,
Analytics and Duplicate Finder under **Crosslist**, and renamed Pro Tools to
**Automations** (Send Offers, Auto Offers, Marketplace Sharing, Stale Listing
Warning). Functionality is unchanged
([What changed in Vendoo? 2026](https://help.vendoo.co/en/articles/16091202-what-changed-in-vendoo-2026)).

## Marketplaces

Vendoo lists eBay, Etsy, Poshmark, Mercari, Depop, Grailed, Facebook
Marketplace, Shopify, Vestiaire Collective and Whatnot (beta). Facebook and
Shopify are desktop only
([Which marketplaces](https://help.vendoo.co/en/articles/6260300-which-marketplaces-does-vendoo-support)).
Sellwild appears in its logos ([Home](https://www.vendoo.co/)), Kidizen on a
marketing page ([Kidizen](https://vendoo.co/marketplaces/reselling-on-kidizen)),
and Vinted only on UK and US Enterprise accounts
([Selling on Vinted](https://marketing.vendoo.co/selling-on-vinted-with-vendoo),
[EU help](https://help.vendoo.co/eu/en/articles/8856209-which-marketplaces-does-vendoo-support)).

| Marketplace | Vendoo | Anteroom | Evidence / reason |
| --- | --- | --- | --- |
| eBay | List, import, delist, relist, sale detection, offers | **Have** | Official API: `services/ebay_listings.py`, `routes/ebay.py` (`/import`), `services/marketplace_delist.py` (Trading `EndFixedPriceItem`), relay sale push `services/relay_sales.py` |
| Etsy | List, import, delist, sale detection | **Have** | Official API: `services/etsy_listings.py`, `routes/etsy.py` (`/import`), relay `order.paid` |
| Poshmark | List, delist, relist, sale detection, sharing, offers | **Partial** | List, draft, update, delist, sale detection: `background/poshmark-native.js`, `services/chrome_sales.py`. No sharing or offers (see [Automations](#automations-offers-sharing-refresh)) |
| Mercari | List, delist, relist, sale detection, offers | **Partial** | Same as Poshmark, `background/mercari-native.js`. No offers |
| Depop | List, delist, relist, sale detection, refresh, offers | **Partial** | `background/depop-native.js`. No refresh or offers |
| Vinted | UK/Enterprise only | **Have** | `background/vinted-native.js`, `services/vinted_listing.py`; Anteroom covers more than the seller's Vendoo plan does |
| Sellwild | Logo only | **Have** | `background/sellwild-native.js`, `services/sellwild_listing.py` |
| Facebook Marketplace | List, delist, sale detection | **Missing** | Not in `marketplace_delist.MARKETPLACES`. Not in the seller's current list, but **23 active items** are recorded as live on Facebook through Vendoo (`notes.vendooMarketplaces`, live database, read-only check). See gap 2 |
| Grailed, Shopify, Whatnot, Vestiaire, Kidizen | Supported | **N/A** | Not in the seller's list, and no item in the database was ever listed there through Vendoo |

## Listing and crosslisting

| Vendoo feature | Source | Anteroom | Evidence |
| --- | --- | --- | --- |
| Main form plus per-marketplace forms, values carried over, **Update All** / **Reset** | [Vendoo form](https://help.vendoo.co/en/articles/6260272-how-to-list-the-vendoo-form), [Update All](https://help.vendoo.co/en/articles/9128864-how-do-the-update-all-and-reset-buttons-work) | **Have** | General form plus native marketplace sections: `services/native_form.py`, `services/form_schema.py`; per-marketplace prices follow the item price unless overridden (CHANGELOG 0.1.403) |
| Native category and field coverage per marketplace | [Vendoo form](https://help.vendoo.co/en/articles/6260272-how-to-list-the-vendoo-form) | **Partial** | Native catalogs: `services/marketplace_schemas.py` (eBay/Etsy APIs), `services/chrome_catalog.py` (Poshmark/Depop). Remaining work per marketplace is in [direct-form-parity.md](direct-form-parity.md) |
| Drafts | [Vendoo form](https://help.vendoo.co/en/articles/6260272-how-to-list-the-vendoo-form) | **Have** | Local drafts, plus private marketplace drafts on eBay, Mercari, Depop, Etsy and Poshmark: `routes/native_marketplaces.py` (`/{marketplace}/draft`) |
| Edit a live listing | [Edit a listing](https://help.vendoo.co/en/articles/9299456-how-do-i-edit-a-listing-in-vendoo) | **Partial** | `/{marketplace}/update` for eBay, Etsy, Poshmark and Depop. Mercari, Vinted and Sellwild need delist and relist |
| Delist / relist one item | [Delist & Relist](https://help.vendoo.co/en/articles/6260258-vendoo-s-bulk-delist-relist-feature) | **Have** | `routes/marketplace_listings.py` (`/delist`), List from `routes/native_marketplaces.py`; covers items Vendoo listed through `vendooListingUrls` |
| Multi-quantity | [Multi-quantity sales](https://help.vendoo.co/en/articles/6626694-how-to-handle-sold-multi-quantity-items) | **Have** | Quantity on every form, Poshmark per-size quantities and SKUs (0.1.408), partial-sale handling `services/sale_inventory.py` |
| Multi-variation | [Variations](https://help.vendoo.co/en/articles/6260275-how-do-i-handle-multi-variation-listings-in-vendoo) | **N/A** | Vendoo does not support it either. Anteroom has Poshmark size inventory; eBay variations are open in direct-form-parity.md |
| Different photos per marketplace (Photo Manager) | [Photos per marketplace](https://help.vendoo.co/en/articles/6619124-can-i-use-different-photos-on-different-marketplaces) | **Missing** | Direct List sends every item photo: `native_marketplaces.py` sets `photo_ids` from all of the item's photos |
| Listing videos (Poshmark, eBay; 5–15 s) | [Crosslisting app](https://www.vendoo.co/cross-listing-app) | **Partial** | Poshmark only: `services/listing_videos.py`, `native_marketplaces.py:579` (`if marketplace == "poshmark"`). No eBay video |
| eBay business policies; Etsy shipping and return profiles | [eBay policies](https://help.vendoo.co/en/articles/6260282-how-to-use-ebay-s-business-policies-in-vendoo), [Etsy profiles](https://help.vendoo.co/en/articles/6260281-how-to-use-etsy-shipping-profiles-and-return-policies-in-vendoo) | **Have** | Policy access on connect (0.1.392); Etsy shop shipping and processing profiles in `services/etsy_listings.py` |
| eBay promotions, international shipping | [eBay promotions](https://help.vendoo.co/en/articles/6812458-how-do-i-opt-into-ebay-promotions), [Global shipping](https://help.vendoo.co/en/articles/6812485-how-do-i-opt-into-global-shipping-on-ebay) | **Have** | eBay marketing controls under Marketplaces → eBay (`services/ebay_workspace.py`); shipping comes from the eBay policy |
| Listing templates | [Templates](https://help.vendoo.co/en/articles/6260273-how-to-create-listing-templates-in-vendoo) | **Have** | `routes/listing_templates.py` (create, apply with review, new draft from a template) |
| "Save as default" on fields | [Custom settings](https://help.vendoo.co/en/articles/6260256-vendoo-custom-settings) | **Partial** | Seller defaults live in Settings (ship-from ZIP, eBay location, marketplace defaults in `services/vendoo_marketplace_fields.py`) and templates; there is no per-field "save as default" button |
| AI Listing Enhancement (description, title, brand, category, size) | [AI Listing Enhancement](https://help.vendoo.co/en/articles/12578441-ai-listing-enhancement) | **Have** | Full generation from photos with the `skills/list-this` rules: `services/listing_generation.py`, per-item chat, measurements from ruler photos (`services/measure_read.py`) |
| Copy an item | [Copies](https://help.vendoo.co/en/articles/6260274-how-do-i-create-copies-of-items) | **Have** | `routes/listing_copies.py`, `services/listing_copy.py` |
| Copy-and-paste onto the marketplace's own form | [Copy-and-paste](https://help.vendoo.co/en/articles/6778756-how-to-list-with-the-copy-and-paste-method) | **N/A** | A workaround for Vendoo's form; then link the listing with **Mark as listed** and a URL (`marketplace_delist.mark`) |

## Bulk actions

Vendoo: up to 240 items, from the inventory page
([Bulk actions](https://help.vendoo.co/en/articles/9777622-vendoo-bulk-actions),
[Bulk actions page](https://www.vendoo.co/bulk-actions-feature)).

| Vendoo bulk action | Anteroom | Evidence |
| --- | --- | --- |
| Bulk Edit: title, description, SKU, price, brand, condition, color, tags, category, size; **Save and relist** ([Bulk edit](https://help.vendoo.co/en/articles/10438710-bulk-edit-edit-your-listings-in-bulk)) | **Partial** | `routes/bulk_edit.py`, `services/bulk_edit.py`: title, description, brand, size, SKU, price, cost, quantity and labels for up to 300 items. No condition, color, tags or category, and no bulk push of edits to live listings (bulk actions are List, Delist and Relist only) |
| Bulk Edit Labels | **Have** | `services/bulk_edit.py` `labels_mode` add / remove / replace |
| Bulk Copy | **Have** | **Select → Copy items**, up to 300 (`components/sidebar/BulkSelection.tsx`) |
| Bulk List, Bulk Delist, Bulk Delist & Relist | **Have** | `routes/bulk_marketplace.py` (`/preview`, `/perform`), up to 100 items, one confirmation, named marketplaces |
| Bulk Delete (from Vendoo only) | **Missing** | Delete is one item at a time: `routes/conversations.py` `DELETE /{conv_id}` |
| Bulk Mark as Not Listed | **Partial** | Per item and marketplace: **Mark as not listed** (`MarketplacesSection.tsx`, `marketplace_delist.MARKS`); no bulk |
| Completion sound | **N/A** | Anteroom shows per-item results in the dialog |

## Inventory management

| Vendoo feature | Source | Anteroom | Evidence |
| --- | --- | --- | --- |
| Drafted / listed / sold columns | [Inventory tools](https://help.vendoo.co/en/articles/6260262-vendoo-inventory-management-tools) | **Have** | `services/item_status.py` derives Active / Sold / partly sold from Anteroom's own listings and the sales ledger, over Vendoo's imported label; `reconcile_job_statuses` applies it (0.1.422) |
| Search, sort, filter (title, SKU, price, platforms listed / not listed, dates, labels) | [Inventory tools](https://help.vendoo.co/en/articles/6260262-vendoo-inventory-management-tools) | **Have** | Inventory search by title, SKU and label, status filters (README, **Inside Anteroom**) |
| Custom labels, color-coded | [Labels](https://help.vendoo.co/en/articles/6260284-vendoo-custom-labels-for-inventory-management) | **Partial** | Local labels with bulk edit; no label colors. Migrated labels that are Vendoo IDs are shown through `services/vendoo_label_catalog.py` |
| Stale listing warning (15–120 days, configurable) | [Stale inventory](https://help.vendoo.co/en/articles/6260291-vendoo-s-stale-inventory-system), [Custom settings](https://help.vendoo.co/en/articles/6260256-vendoo-custom-settings) | **Partial** | Fixed 60 days in Suggestions (`services/suggestions.py` `STALE_MIN_DAYS`) and Analytics (`inventory_analytics.STALE_DAYS`); Automations can act after any number of listed days (CHANGELOG 0.1.402). No configurable warning threshold. Listed dates also come from Vendoo notes for migrated items |
| Duplicate Finder | [What changed 2026](https://help.vendoo.co/en/articles/16091202-what-changed-in-vendoo-2026) | **Partial** | Duplicate Vendoo links (`routes/conversations.py` `/vendoo-link-duplicates`); linking a marketplace URL already on another item is rejected (`marketplace_delist.mark`). No finder for similar items |
| CSV inventory and sales export | [CSV export](https://help.vendoo.co/en/articles/6260286-how-to-download-a-csv-inventory-spreadsheet-and-sales-report) | **Have** | `routes/sales_ledger.py` `/export/sales.csv`, `/export/inventory.csv`. Columns are fixed, not customizable |
| Import active listings from each marketplace | [Import](https://help.vendoo.co/en/articles/6260276-how-to-import-your-items-in-vendoo) | **Have** | eBay and Etsy import whole listings (`routes/ebay.py`, `routes/etsy.py` `/import`). Poshmark, Mercari, Depop, Vinted and Sellwild import active listings through the Chrome bridge (`routes/chrome_import.py`, `services/chrome_import.py`, `background/marketplace-import.js`), with exact link matching first and a suggested match the seller reviews. Multi-size Poshmark and Depop listings are not imported yet. Vendoo import (`routes/imports.py`) is the migration path |
| Import sold items | [Import sold](https://help.vendoo.co/en/articles/6760021-can-i-import-sold-items) | **Partial** | eBay and Etsy order recovery (`services/order_recovery.py`); Vendoo's sold history came in with the Vendoo import |
| Multiple accounts per marketplace | [Multiple accounts](https://help.vendoo.co/en/articles/6260283-how-do-i-connect-vendoo-to-multiple-accounts-on-the-same-marketplace) | **N/A** | Vendoo supports one account per marketplace too |
| Team members / sub-accounts | No Vendoo documentation found | **N/A** | Vendoo does not advertise seats |

## Sales

| Vendoo feature | Source | Anteroom | Evidence |
| --- | --- | --- | --- |
| Sale detection (every ~10 min; computer on) | [Sale detection](https://help.vendoo.co/en/articles/8047348-sale-detection-auto-delist) | **Have** | eBay and Etsy push through the relay (`services/relay_sales.py`); Poshmark, Mercari, Depop, Sellwild and Vinted checked every 4–12 minutes (`services/chrome_sales.py`, `background/marketplace-sales.js`) |
| Auto-delist on sale; skip while units remain | [Sale detection](https://help.vendoo.co/en/articles/8047348-sale-detection-auto-delist) | **Have** | Opt-in `sale_autodelist` (on for this seller): `marketplace_accounts.autodelist_for_sales` delists the other live marketplaces once, never the one that sold, and skips when stock remains |
| Mark as sold, with sale details, delisting the others | [Mark as sold](https://help.vendoo.co/en/articles/6260270-how-to-mark-items-as-sold-on-vendoo) | **Partial** | **Mark as sold** per marketplace (`marketplace_delist.mark`). It creates no sale record, so there is nowhere to enter price, fees or shipping for it, and it does not delist the others |
| Sales made elsewhere (in person, Instagram, other) | [Sold elsewhere](https://help.vendoo.co/en/articles/6762747-i-sold-an-item-on-a-different-marketplace-that-vendoo-doesn-t-have-how-do-i-handle-it-in-vendoo) | **Have** | Mark as sold and **Record a sale** record a seller sale with amounts, including **Other / in person** (`sales_ledger.record_seller_sale`, 0.1.422) |
| Edit or remove sale details | [Fix sales details](https://help.vendoo.co/en/articles/6763012-how-do-i-fix-sales-details-or-remove-a-sale-record-for-an-item) | **Have** for detected sales | **Record amounts** on order lines (`routes/sales_ledger.py` `PUT /{sale_id}/financials`), dismiss (`/api/marketplace-sales/{sale_id}/dismiss`) |
| Bundles | [Bundles](https://help.vendoo.co/en/articles/6763426-how-do-i-handle-bundles-in-vendoo) | **Partial** | eBay and Etsy orders keep one line per item; Chrome marketplaces report listing IDs per sale |
| Shipping labels | Not a Vendoo feature | **N/A** | — |

## Analytics

Vendoo: revenue, profit, sold and listed counts, average sale price, each
compared with the previous period; per marketplace; top categories,
subcategories and brands; per custom label; any date range; includes sales
off integrated marketplaces
([Business analytics](https://help.vendoo.co/en/articles/6260264-vendoo-business-analytics),
[Analytics page](https://www.vendoo.co/reselling-analytics)).

| Vendoo feature | Anteroom | Evidence |
| --- | --- | --- |
| Revenue, profit, ASP, counts, previous period, any date range | **Have** | `routes/analytics.py` `AnalyticsResponse`; `services/inventory_analytics.py` reads the sales ledger (`marketplace_sales`, recorded amounts) with the imported Vendoo sale, one row per order (0.1.422). Ledger lines not linked to an item are not counted |
| By marketplace, category, brand | **Partial** | `marketplaces`, `categories`, `brands` groups; same data source |
| By custom label | **Missing** | No label grouping in `AnalyticsResponse` |
| eBay earnings and fees | **Have** | `services/ebay_finances.py` (0.1.397) |
| Expenses / ad spend | **Have** (beyond Vendoo) | `routes/ad_spend.py`, sourcing-box cost allocation |

## Automations: offers, sharing, refresh

| Vendoo feature | Source | Anteroom | Evidence |
| --- | --- | --- | --- |
| Offers to likers/watchers, manual bulk and automatic rules (price bands, exclusions, activity log) | [Send offers](https://help.vendoo.co/en/articles/10043166-send-offers-to-likers-automatically-or-manually-in-bulk) | **Partial** | eBay only: `routes/ebay_offers.py` (preview, send, rules, pause, attempts), `services/ebay_offers.py`. **Missing** for Poshmark, Mercari and Depop. Grailed and Vestiaire are N/A |
| Poshmark sharing (scheduled rounds, order, speed, up to 6,000/day, log) | [Marketplace Sharing](https://help.vendoo.co/en/articles/11003940-marketplace-sharing-tool-for-poshmark-depop-and-grailed) | **Missing** | No share code in the server or extension |
| Depop refresh | same | **Missing** | — |
| Grailed bump | same | **N/A** | Not a seller marketplace |
| Scheduled delist / relist / price drops | Not in Vendoo (relist is started by hand) | **Have** (beyond Vendoo) | `routes/automations.py`, `services/automation_actions.py`; every run waits for the seller's review per item and marketplace |

## Photos

| Vendoo feature | Source | Anteroom | Evidence |
| --- | --- | --- | --- |
| Upload, reorder | [Vendoo form](https://help.vendoo.co/en/articles/6260272-how-to-list-the-vendoo-form) | **Have** | `routes/photos.py` (upload, `PATCH /photos/order`) |
| Rotate | [Photo tools](https://help.vendoo.co/en/articles/8011114-photo-editing-tools-background-removal-by-photoroom) | **Have** | `POST /photos/{photo_id}/rotate` |
| Crop, resize, brightness, contrast, saturation, filters | [Photo tools](https://help.vendoo.co/en/articles/8011114-photo-editing-tools-background-removal-by-photoroom), [Mobile app](https://www.vendoo.co/mobile-app) | **Missing** | No crop or adjustment route or UI |
| Background removal to white (PhotoRoom) | [Photo tools](https://help.vendoo.co/en/articles/8011114-photo-editing-tools-background-removal-by-photoroom) | **Missing** | No background removal in the server or UI |

## Mobile

| Vendoo feature | Source | Anteroom | Evidence |
| --- | --- | --- | --- |
| iOS and Android app: list, edit, delist, relist, mark sold, labels, analytics, photo editing | [Mobile app](https://www.vendoo.co/mobile-app), [Phone or tablet](https://help.vendoo.co/en/articles/9299090-can-i-use-vendoo-from-my-phone-or-tablet) | **Partial** | Phone layout over private Tailscale Serve (Settings → Connections, `services/tailscale_serve.py`). No native app; the Mac must be on, which Vendoo's sale detection also requires |

## Residual Vendoo dependencies

What in the code still calls Vendoo, needs a Vendoo account, or reads data that
only Vendoo writes. **Still needed**: removing it today loses a feature with no
direct replacement. **Remove**: safe to delete once the seller stops using
Send to Vendoo. **Naming**: Vendoo in the name only; the code serves direct
listing.

| Component | What it does | Class |
| --- | --- | --- |
| `services/vendoo_watch.py`, `services/vendoo_label_sync.py` | Pull Vendoo's draft / active / sold status, dates and sale into `notes.vendooStatus`, `vendooDates`, `vendooSale` | **Still needed** until the inventory status (`repositories/queries.py:345-379`) and Analytics (`services/inventory_analytics.py`) read Anteroom's own listings and sales |
| `services/vendoo_label_catalog.py`, `vendoo_import.split_vendoo_labels` | Vendoo label ID → name | **Still needed** while migrated items carry Vendoo label IDs; convert them to names once, then remove |
| `services/vendoo_import.py`, `services/vendoo_bulk_import.py`, `routes/imports.py` (`/vendoo`, `/vendoo/bulk`, `/vendoo/labels`) | Migration from Vendoo | **Still needed** for one final full import before cancelling; then **Remove** |
| Facebook Marketplace listings (23 active items) | Only Vendoo can delist them or see their sales | **Still needed** until those items are delisted on Facebook |
| `services/category_tree.py` (trees "used by Vendoo's forms") read by `direct_preflight.seeded_leaf` / `seeded_subcategories` | Stored category trees used by direct List checks | **Still needed** as stored data; refreshing them needs Vendoo. Replace with the native catalogs before removing |
| `services/category_fields.py`, `services/category_learn.py` (fetch `/api/category/specifics` through Vendoo) | Vendoo field schemas | **Remove** the Vendoo fetch with Send; direct forms use `marketplace_schemas.py` and `chrome_catalog.py` |
| `routes/vendoo_api.py`, `services/vendoo_create.py`, `vendoo_item_update.py`, `vendoo_send.py`, `vendoo_encodings.py`, `vendoo_api_log.py`, `send_queue.py`, `send_review.py`, `browser_fill.py`, `routes/jobs.py` (`run_ops`) | Send to Vendoo / Update Vendoo, and its troubleshooting log | **Remove** (direct List / Update replaces them) |
| `vendoo-extension/content-scripts/vendoo.js`, `update-all-buttons.js`, `background/vendoo-api.js`, `background/vendoo-item.js`; `manifest.json` Vendoo hosts (`app.`, `web.`, `api.web.`, `us.vendoo.co`, the Vendoo Cloud Functions host) | Vendoo session, item API and form filling in Chrome | **Remove** with Send |
| Frontend: **Send to Vendoo**, **Update Vendoo**, **Sync from Vendoo**, **Vendoo in Chrome**, **Vendoo API**, `VendooImportButton.tsx`; README install step 5 ("Sign in to Vendoo") and "You need … a Vendoo account" | Vendoo-facing UI and docs | **Remove** with Send; keep the import button until the final import |
| `services/vendoo_item_build.py` (`_general_details`, `_listing_section`), `services/vendoo_item_shape.py`, `services/vendoo_marketplace_fields.py`, `services/vendoo_categories.py`, `services/vendoo_specifics.py` (pure helpers) | Build the marketplace sections and validations that direct List sends (`native_form.py`, `ebay_listings.py`, `etsy_listings.py`, `form_schema.py`) | **Naming**; move or rename before deleting the Send path, or direct List breaks |
| `vendoo_import.parse_notes`, `merge_notes`, `vendoo_binding` (28+ callers), `notes.vendooListingUrls` | Generic notes helpers; stored Vendoo URLs still identify listings for delist and sale matching (`relay_sales.matching_listings`) | **Naming** for the helpers; the stored URLs stay as data |
| Package `vendoo_studio`, folders `vendoo-studio/`, `vendoo-extension/`, database `vendoo_studio.db` | Names | **Naming** |

## Gaps if Vendoo is cancelled today

Most important first.

1. ~~**New sales never mark items Sold or reach Analytics.**~~ Fixed in 0.1.422. Inventory status
   and every Analytics total read Vendoo's synced data. A sale Anteroom
   detects is recorded under Sales records and can trigger auto-delist, but
   the item stays Active and its revenue is not counted.
   *Fix:* derive item status from Anteroom's own marketplace listings and
   sales, and have Analytics read `marketplace_sales` (with recorded amounts)
   alongside the imported Vendoo history.
2. **23 Facebook Marketplace listings stay up with nobody watching them.**
   *Fix:* delist them in Vendoo (or on Facebook) before cancelling, or add
   Facebook to the direct marketplaces.
3. **No Poshmark sharing or Depop refresh.** *Fix:* add a seller-scheduled
   share/refresh job through the existing Chrome bridge, with Vendoo's
   controls (count, order, speed, daily cap, activity log).
4. **Offers to likers only on eBay.** *Fix:* extend the `ebay_offers` rules,
   preview and attempt log to Poshmark, Mercari and Depop.
5. **No background removal, crop or photo adjustments.** *Fix:* add crop
   and adjustments beside rotate, and background removal to white (a local
   model or a provider API).
6. ~~**No manual or off-platform sale record.**~~ Fixed in 0.1.422. Mark as sold stores no
   price, fees or shipping, and in-person sales have nowhere to go.
   *Fix:* let Mark as sold create a sale record with amounts, with an
   "Other / in person" marketplace, and offer to delist the others.
7. **Final Vendoo import and label conversion.** *Fix:* run one full Vendoo
   import, convert Vendoo label IDs to names, and only then cancel.
8. ~~**Poshmark, Mercari, Depop, Vinted and Sellwild listings made outside
   Anteroom cannot be imported.**~~ Done: import through the Chrome bridge
   (unit-tested; real-account verification pending). Multi-size listings remain.
9. **No eBay listing video.** *Fix:* add eBay video upload through the
   Media API next to the Poshmark video.
10. **Bulk edit lacks condition, color, tags and category, and there is no
    bulk Update of live listings.** *Fix:* add those fields and an Update
    action in the bulk marketplace dialog.
11. **Smaller gaps:** different photos per marketplace, bulk delete, bulk
    mark as not listed, configurable stale threshold, label colors and label
    analytics, a finder for similar items, live Update on Mercari, Vinted and
    Sellwild.

Before deleting the Send path, move the shared section builders out of the
`vendoo_*` modules (see **Naming** above).
