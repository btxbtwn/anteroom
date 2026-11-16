# Conditional production test readiness

Status: **NOT READY; no production operation authorized for execution yet.**

Cristian's later intent is preserved: “I’m fine with you listing a test item so long as you delist it and confirm it’s delisted.” Execution is conditional on a sandbox pass and verified relevant deployment. The parent must resolve material scope gaps first. Nothing in this report permits a production request now.

## Proposed scope for the parent to resolve

- **Item: unresolved.** Every item used in this audit is a disposable fixture. No actual genuine item has been identified or selected. Ask Cristian to designate one owned, available item he is willing to sell, with accurate photos, condition, price, shipping and quantity. Do not post the fake sandbox shirt, a nonexistent test product, or arbitrary real inventory. A listing can sell immediately; the item must be fulfillable.
- **Candidate marketplace: Poshmark, subject to Cristian's choice and a working connection.** Its production adapter supports one named List action and a confirmed delete operation. This exercises the Chrome/Bridge path used by the isolated workflow test. Poshmark says listing is free, but a sale incurs seller fees; do not assume a zero-cost completed sale or authorize boosts, sharing, offers or promotions. Confirm the actual fee preview and accepted exposure with Cristian. [Current fee policy](https://poshmark.com/fee_policy), consulted 2026-11-16.
- **Alternative: eBay fixed-price only**, after eBay connection recovery is deployed and checked. Insertion/optional fees depend on category, account allowance and options; no fee budget was specified. eBay permits ending fixed-price listings, while auctions have restrictions and can incur fees. Do not choose an auction for this test. [Selling fees](https://www.ebay.com/help/selling/fees-credits-invoices/fees-credits-invoices?id=4822), [ending listings](https://www.ebay.com/help/selling/managing-active-listings/ending-listing-early?id=4146), consulted 2026-11-16.

## Supported operation and evidence required

1. Verify the relevant PRs actually merged, required CI passed, and the hosted or Mac release and installed Bridge actually contain their commits. Record API version/build, frontend build identity, extension version and deployment outcome. Draft PRs and queued deployments are insufficient. No deployment has been verified in this audit.
2. Confirm the designated item, marketplace/account, genuine for-sale terms, possible fees, and rollback route. Ensure the seller can manually remove the item if connection recovery fails. Do not schedule or retry an external write without fresh explicit scope.
3. Save and validate the draft. Confirm **List on Poshmark** (or the chosen single marketplace) against the saved revision. `POST /api/conversations/{id}/native-marketplaces/{marketplace}/list` requires `confirm: true`; never use an all-marketplace default.
4. Wait for the completed job and a marketplace ID/URL. Read the exact item through the marketplace adapter and independently open its public/seller listing page. Verify actual availability and key fields. A draft URL, button click, successful enqueue, or Anteroom's local status is not publication evidence.
5. Immediately invoke the separately confirmed, named `POST /api/conversations/{id}/marketplace-listings/delist` with only that marketplace. The domain service (`services/marketplace_delist.py`) uses `marketplace.delete` for Poshmark; eBay uses `EndFixedPriceItem`. A local **Mark as not listed** changes only the record and is never a substitute.
6. Wait for a completed successful result, then independently read the exact item again and reload its public/seller page. Confirm it is removed/unavailable and cannot be purchased. Save timestamps, safe item ID/URL, adapter readbacks, job states and screenshots for listing and delisting. A local deleted record or adapter acknowledgement alone does not fulfill Cristian's criterion. If any readback is ambiguous, leave the test unresolved and escalate; never claim delisted.
7. If it sells before removal, stop the test and contact Cristian. Do not cancel a real order, refund, message a buyer or pretend the item never existed. Those actions have separate consequences and are outside this scope.

## Current sandbox checkpoint

- Real browser local/hosted drafts: photo upload, fake generation, edit, persistence/reload observed. Hosted phone draft `d65d5f36c9f4` retained its title and one uploaded photo. No live listing was created.
- Confirmed browser/API sandbox list and delist remain refused (403). Browser List on disconnected hosted Chrome opens the account connection panel; it cannot demonstrate successful publication.
- Isolated test-only adapter journey uses real create/photo/generate/edit/validate/list routes and persistence, fake provider and fake bridge, and forbids sockets. Simulated rejection → explicit retry → simulated published state survives a fresh API client. Cancellation coverage proves no publish follows a cancelled draft-save. **This is simulated state, not a real marketplace listing.**
- Two targeted workflow/cancellation cases passed. Broader native, live-service, form-sweep, concurrent-load and production tests remain outstanding. No full sandbox sign-off or deployment sign-off is claimed.

## Historical false-positive safeguard

The 2026-09-14 lead does not establish a current defect, but reinforces the evidence rule: missing navigation, missing live-listing state, a blank form during load, unknown status, or stale cached status is **unverified**, never a successful draft/list/delist. An explicit draft/not-listed result is distinct from an unknown result. Eventual production success requires the marketplace's own current state for the exact item after listing and independently again after delisting; clicks, queued jobs, and local marks are insufficient. No live test may run before the existing prerequisites are resolved.


## Historical Depop cleanup lead (2026-10-25; current live state unverified)

User source: https://app.t3.codes/threads/b5e12513-0433-4698-aa8e-ab05715755e3/b27ecec3-d827-4858-a3d8-c9ad0aa7ee49 . Thread exists; only introductory context was read here, not the entire historical audit. User reports that direct Depop publication then lacked a link and Studio could not delist it. Treat this as a historical lead, not a proven present marketplace failure.

Current source inspection: depop-native.js attempts status/draft/shop lookup for a product URL, but its completed publish responses still include listing_url only when available (around1700/1721). native_marketplaces._outcome permits Depop publication without that URL. marketplace_identity derives Depop identity from a validated product slug URL; a syntactically valid URL does not verify ownership or live state. marketplace_delist._open_delist refuses409 when no listing_id is known. Existing tests cover slug recovery/manual marks, but were not rerun for this lead and do not establish this audit's complete publish→persistent identifier→delist journey.

Before choosing ANY production item/path: prove in isolated test-only adapters that publish returns a usable durable marketplace identifier, save it, read it through a fresh client/restart, explicitly delist the same identifier with confirmation, and independently read marketplace-own absent/delisted state. Also test missing identifier refusal and recovery without duplicate publication. Do not count local marks, queued jobs or clicks as verification. No live publish may be used to discover whether cleanup works. Depop is not a production-ready candidate on this evidence alone; marketplace/item/account/fees and verified deployment remain unresolved.
