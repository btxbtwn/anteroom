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

## Review correction (2026-11-16)

Independent review found that classifying an entire state/evidence group from
its recipient sum could verify rows with no receipt, or allow a negative count
to erase valid receipts. Regressions reproduced both failures before the fix.
SQL now groups by a per-row positive-receipt `CASE` as well as state/evidence,
and sums only positive receipt values. Missing, zero and negative receipt rows
remain unverified even alongside valid receipts. The eBay column is NOT NULL;
its missing-receipt representation is zero, while Mercari can also store null.
No schema or send-path change is needed for this correction. Two bounded
seller/time aggregate queries and the four-row response are preserved.

This follows PostgreSQL's documented [CASE semantics](https://www.postgresql.org/docs/16/functions-conditional.html)
and [aggregate semantics](https://www.postgresql.org/docs/16/functions-aggregate.html),
which do not make a group's row count conditional on a positive sum. Native
API day strings now use `en_US_POSIX` and a Gregorian calendar per
[Apple QA1480](https://developer.apple.com/library/archive/qa/qa1480/_index.html).
A standalone Foundation regression covers Arabic, Persian, Thai and English
calendar locales and checks ASCII dates on both sides of a local-day boundary;
it is included in `tools/test-models.sh`. This Linux host has neither Swift nor
xcrun, so this native regression has been authored but not executed here.

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
Full local suites remain reserved by other active workers. The short slot was
used for three real Postgres regressions and four desktop/phone light/dark
browser cases; all passed, and the four screenshots were inspected. Final-head
native reruns and CI remain outstanding. Concurrent multi-seller generation
latency (p50/p95, errors, load), payload benchmarks and input-feedback
measurements remain gaps. Query-count checks are not production performance
evidence.


## Bounded Poshmark acceptance feasibility check (2026-11-16)

Read-only code inspection only; no mailbox ingestion or live marketplace read.
`vendoo-extension/background/marketplace-order-history.js:167` projects
`order.id`, `line_items[].id`, `line_items[].product_id`, line `price_amount`,
`state`, `cancelled_on`, `inventory_booked_at`, total/fee/commission/shipping
amounts and the summary's `display_status`. Non-cancelled orders become paid;
`Order Complete` controls finality. Neither state is an offer-acceptance flag.
`alt_ids` is explicitly empty. The captured fixture uses
`seller_confirm_initiated` with order/line/product IDs; it provides no stable
offer ID or explicit offer-acceptance indicator.

`services/order_history.py:61` / `:70` parse only line identity, listing identity,
amounts and order status/finality/date/currency. `save_orders` persists those
into `MarketplaceSale` and financial totals. There is no accepted-offer flag
or offer-ID field. Any additional upstream indicator, **if supplied**, would
be discarded by the current projection; these code paths/fixtures cannot prove
whether Poshmark actually supplies one in the raw order payload.
`marketplace-promotions.js:152` also discards the outgoing POST result and
returns only `request_accepted`, without a stable offer/recipient identifier.

An explicit marketplace acceptance notice can establish an offer-based sale,
but cannot identify which offer or prove Anteroom attribution. Titles, buyers,
prices and nearby timestamps do not supply that missing identity bridge. This
check does not change the first version's unavailable conversion metric and
does not authorize mail ingestion or speculative attribution implementation.


## Full-diff review and readiness follow-up

Independent full review of `63eca866` identified two further correctness gaps:
concurrent-index partial failure was not retryable, and same-day native
pull-to-refresh did not refetch offer activity. The migration correction
reuses valid completed indexes and drops/rebuilds invalid leftovers in an
autocommit block before retrying concurrent construction. This follows
[PostgreSQL's concurrent-index recovery guidance](https://www.postgresql.org/docs/16/sql-createindex.html).
The nullable expansion supports old worker inserts during rollout; such rows
remain undated, and must not be counted using their preview dates.

New Postgres-only regressions use a private migrated Postgres database, exercise
real partially committed and invalid indexes, run the production aggregate
queries under `anteroom_seller`, and test RLS even with a deliberately wrong
explicit owner predicate. They skip on SQLite; existing tests that explicitly
create SQLite engines are not Postgres coverage. The original migration was
restored temporarily for a before/fix comparison: two migration regressions
failed with DuplicateTable while the RLS/query case passed (11.84 seconds).
After restoring the correction, all three passed on a new private Postgres
cluster (10.46 seconds). The invalid-index test also proves old-column inserts
by a running older worker still succeed and leave sent_at null.

The native refresh correction passes a new refresh token to the section's task
without resetting its selected period/custom dates. A focused XCUITest records
custom dates across an actual pull; API access logs must separately establish
same-period refetch. An accessibility layout test at `63eca866` failed because
20 small drags did not reach a lazy unmaterialized row below the new section.
The Mac's inspected frame/hierarchy showed vertical scrolling rather than
clipping. Its reveal helper now moves farther only while the target is absent,
then uses small drags and retains the full-viewport assertion.

At `63eca866`, GitHub CI43809405742 passed all required jobs, including backend
Postgres and existing hosted/sandbox browser suites. Mac validation reported
23 standalone Swift programs (including non-Latin dates), 69 unit tests,
simulator build and default offer UI passing; accessibility test failed as
above. Those are exact-head results, not validation of this follow-up.

The focused Playwright regression now passes four cases (41.5 seconds): desktop
1440x900 and iPhone-15-sized Chromium, each light/dark. It seeds terminal receipts
only in the throwaway sandbox, exercises Today/week/month/custom periods and
invalid-date recovery against the actual endpoint, verifies no horizontal
page overflow, and saves four screenshots. It cleans up its journal rows/rule
and restores the sandbox's theme. Browser QA exposed raw period IDs in the
selected control; a regression failed on `today` versus `Today` before the
control was corrected to display labels. Initial harness runs also exposed an
older dependency symlink requesting absent Chromium1248, an incorrect mobile
navigation locator, and a theme override that server preferences superseded.
Reusing already-installed locked-version dependencies, using Open navigation,
and setting/restoring the sandbox theme resolved those harness failures; no
install or product theme/navigation change was made.

Screenshot filenames are `offer-analytics-{desktop|phone}-{light|dark}.png` in
the test output. CI's always-uploaded `studio-browser-report-sandbox` artifact
retains them. The short resource slot was explicitly returned to the next
worker after both private Postgres and browser/server processes stopped.
No duplicate full suite was started. Native refresh/accessibility checks must
still be rerun on the correction commit before readiness.


A second independent readiness review found no remaining migration/aggregation/
refresh blockers, but identified that Playwright teardown runs even after a
setup safety assertion fails. The fixture now returns from teardown unless
both sandbox verification and successful fixture seeding completed; it never
writes preferences or cleans rows on a server rejected as non-sandbox. This
final guard was added after the four passing local browser cases; corrected-head
CI must validate the final test source. Native refetch still needs API-log
verification in addition to the preserved-filter UI assertion.

## Full local verification and native evidence, 2026-11-16

The frozen product/test head `e1fdd35da790a565ddd71884f463bcd565bb5384` has
[successful exact-head GitHub CI43817341707](https://github.com/btxbtwn/anteroom/actions/runs/43817341707).
It remains a draft; CI success does not waive the separate required local gate.
One full `./check.sh` was run in the explicitly reserved resource slot, with
four pytest workers, owned disposable data and all configured browser projects.
Only local browser ports/output paths and the installed WebKit executable were
overridden; no tests or assertions were filtered out and no validation flags
were disabled.

- SQLite: 4,411 passed, 221 skipped, one xfailed, 1,559 subtests passed.
- Postgres: 4,576 passed, 55 skipped, one xfailed, 1,559 subtests passed;
  **one failure** at `test_error_email_budget.py:167`: API-role SELECT denied
  on operator storage. The seller-denial assertion passed. The separate shipping
  worker's role-order reproduction identifies test bootstrap ordering: the API
  role can appear after a database's migrations have already conditionally
  granted access. No grants, production permissions or assertions were changed
  to make this pass. The independently owned fixture correction and a green
  subsequent local gate remain outstanding.
- Full sandbox browser suite: 175 passed, 23 skipped, including all six offer
  cases on desktop Chromium, phone Chromium and phone WebKit. Desktop/phone
  light/dark screenshots were inspected. Full hosted suite: 63 passed, nine
  skipped. Ruff, layers, dead code, design, locked-baseline types, changelog and
  all three gitleaks scans passed.
- The initial frontend/build stages failed because an external node_modules
  symlink's target had been removed. An owned `npm ci --offline` restored locked
  dependencies without source/lock changes. The exact frontend commands then
  passed: lint, knip, 178 Vitest files / 1,197 tests, desktop build and hosted
  build. These are explicit stage reruns, not an uninterrupted green full check.
  The original `./check.sh` exit remains 1.

Mac evidence is also kept separate by source head. At product head `48204cdc`,
23 standalone Swift programs (including Gregorian/POSIX dates under ar_SA,
fa_IR, th_TH and en_US), arm64+x86_64 simulator build, 69 unit tests, default
offer UI and accessibility offer UI passed. `3b90f149` passed the refresh UI
regression: custom dates were preserved and API access logs showed the identical
period/timezone GET200 both before and after the actual collection-view pull.
Only UITest helpers differ between that product head and `e1fdd35d`.
The exact-e1 iPad split-view/saved-and-unsaved recovery test passed on its one
whole-test retry. Its first run dropped the New listing tap and made no create
request; that failure was retained and no duplicate creation tap was introduced.
Other startup/tab input flakiness is recorded in the Mac report. This was
simulator execution; physical-device and native latency measurements were not
performed. The Mac's report is
`/tmp/anteroom-pr1560-native-e1fdd35d/validation-report.txt` on that executor.

The required bounded concurrent-work measurements and their target misses are
recorded in [responsiveness.md](responsiveness.md#offer-activity-under-concurrent-work-2026-11-16).
Local full-check log: `/tmp/offer-full-check-e1fdd35d.log`; frontend recovery log:
`/tmp/offer-frontend-rerun-e1fdd35d.log`. The resource slot was explicitly released
after complete cleanup, first to shipping fixture validation, then reserved for
the photo repro and retrospective. No production or marketplace settings were
changed, no live offers/auth refresh/sales tests were performed, and no merge or
draft-ready action was taken. The original Library screenshot remains unavailable
on this executor; these visual checks use actual local sandbox captures.
