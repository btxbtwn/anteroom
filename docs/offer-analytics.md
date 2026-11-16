# Offer activity: first version

Research date: 2026-11-16. Scope: read-only analytics on Automations, on web,
Mac web UI and native iPhone. No offer eligibility or automation changes.

## Sources and decisions

- [eBay Negotiation guide](https://www.developer.ebay.com/api-docs/sell/static/marketing/offers-to-buyers.html):
  one request can produce offers to multiple eligible buyers. Count recipient
  receipts, not requests or eligible listings. The current adapter validates
  `offers[].offerId`, then retains only the array length (`ebay_api.send_offer`).
  It does not persist offer IDs. No new claim of unique buyers is possible.
- [eBay Fulfillment contract](https://developer.ebay.com/api-docs/sell/fulfillment/resources/order/methods/getOrders):
  separate order/line identities. The documentation URL currently redirects to
  the Fulfillment overview; detailed field evidence here comes from our order
  adapter and `MarketplaceSale`, not from assuming a documented offer join.
- [Poshmark Offers to Likers](https://support.poshmark.com/s/article/759242802)
  and [offer help](https://poshmark.com/offers_help): private offers have their
  own acceptance/expiry lifecycle. Our captured submit reply proves only request
  acceptance, not a recipient count or accepted sale.
- [Mercari promotional tools](https://www.mercari.com/us/help_center/topics/listing/articles/how-does-the-promote-button-work/):
  offers to likers are distinct from public price promotion. Our captured
  recipient-creation reply supplies the count; preflight likes do not.
- [Depop Send Offer](https://depophelp.zendesk.com/hc/en-gb/articles/15495796917777-Send-Offer):
  sending offers and purchasing are separate actions. Our bridge confirms a
  bulk job reached SUCCESS, with no reliable recipient count.
- [Shopify marketing reports](https://help.shopify.com/en/manual/reports-and-analytics/shopify-reports/report-types/default-reports/marketing-reports):
  attribution depends on recorded interactions and an explicit model. We apply
  the distinction between evidence and attribution; we do not borrow a default
  attribution window or divide listing sales by recipient offers.
- [Polaris data tables](https://shopify.dev/docs/apps/build/app-home/migrate-from-polaris-react/data-table):
  preserve numeric values and make narrow layouts readable with labels. Applied
  as shared Anteroom panels with labelled per-marketplace rows; no new table
  framework or dependency. Native uses the app's grouped SwiftUI list convention.
- [Supabase RLS guidance](https://supabase.com/docs/guides/database/postgres/row-level-security):
  retain existing seller RLS, explicit owner predicates, and matching indexes.
  Changelog checked; no Supabase client/API/auth changes are involved.

## Persisted evidence and counting contract

`ListingPromotion` is one action on one listing, with state, confirmation,
recipient_count, sent_at, finished_at and automation_id. `sent_at` is the durable
send reservation, not the automation tick or preview. The report groups by this
timestamp only after interpreting the final state. Manual and automatic offers
are both included, shares/previews/skips/cooldown checks are excluded.

- eBay `sent` + positive recipient_count: confirmed recipient offers and sends.
- Mercari `done` + `recipients_created`: confirmed recipient offers and sends.
- Poshmark `done` + `request_accepted`: accepted requests only.
- Depop `done` + `job_completed`: completed bulk jobs only.
- Other done evidence: unverified. Unknown and checked remain unknown; checked
  means the seller acknowledged uncertainty, not that delivery was confirmed.
- Failed and pending reservations are shown separately. Preflight failures
  without a send reservation are excluded, since this is send activity.

Each journal row contributes once; rereading or checking it cannot append
analytics observations. Multiple real sends to the same listing/person count
again. Counts are not unique buyers. Batch IDs are not recipient identities.
The first version relies on existing adapters' receipt validation and send-once
journal guarantees; it does not rewrite or resend marketplace actions.

Historical eBay attempts had preview creation dates only. The new nullable
send timestamp is set during reservation; old rows remain undated and excluded,
never assigned manufactured dates. Current journal retention is partial: deleting
an eBay rule cascades attempts, account deletion purges seller data, and offers
sent outside Anteroom are absent. Promotion journals survive item deletion.
No first row is described as the start of complete tracking. Recipient values
without positive evidence are null, not zero. All figures describe recorded
activity only; no all-marketplaces total combines incompatible units.

Today, Monday-to-today week, calendar-month-to-today and custom inclusive dates
use the device IANA time zone. The server converts each local midnight separately
to UTC, including DST, and queries a half-open interval. Custom ranges are capped
at 366 days. Send-start time determines the day; reconciliation can update a
previous day's state. No history hydration, polling loop or network call is
introduced. Two server-side aggregate queries use seller/time indexes; response
shape is four rows. Existing signed-in query-cache cleanup remains in effect.

## Conversion limits

`MarketplaceSale` retains seller/marketplace/order_id/line_id/listing_id and
order_status (paid, partially_refunded, cancellation_pending, cancelled,
refunded). Neither offer journal has the recipient/offer/order identity bridge
needed to prove a converted sale. A matching listing and later timestamp is
correlation, even if the sale price matches. We therefore expose null confirmed
conversions and display “Not available”; no inferred conversions, rate, revenue,
or denominator. Cancelled/refunded sales never become conversion claims.

A future conversion feature needs durable marketplace offer IDs and a verified
order-line join, deduplication by seller/marketplace/offer/order-line, explicit
attribution windows and refund/cancellation reconciliation. No buyer identity
collection or speculative conversion schema is added now. Other marketplaces
have no supported outgoing-offer ledger and are not displayed.

## Verification and remaining gaps

See the PR for exact commands/results. Targeted regression tests cover ledger
states, recipient-vs-request units, seller filtering, repeated reads, bounded
query count, send-date boundaries, DST, range validation and previous-schema
upgrade. Shared generated contract covers desktop and hosted responses.

Library materialization/prepare_materialize tools and a Library skill are not
available in this session. IMG_1345.jpeg was not downloaded or inspected; no
storage URL was guessed. Design is based on repository components and contracts.
Full/browser suites are reserved by other active workers; desktop/phone,
light/dark screenshots and native runtime verification remain outstanding.
Postgres migration/RLS execution and concurrent multi-seller generation latency
(p50/p95, errors, load), payload benchmarks and input-feedback measurements are
also outstanding. Query-count checks are not production performance evidence.
