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
