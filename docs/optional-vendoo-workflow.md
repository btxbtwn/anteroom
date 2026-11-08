# Optional Vendoo workflow

Settings → Experimental → **Use Vendoo** selects one workflow.
New installations use direct marketplaces. The obsolete `vendoo_enabled` flag
does not enable Vendoo.

- **Off:** direct marketplace forms and actions; no Vendoo session reads or sends.
- **On:** edit the listing, press **Send to Vendoo**, then **Review in Vendoo**.
  This creates a private Vendoo draft with its marketplace sections and photos.
  Review, manual corrections, listing and delisting happen in Vendoo's own UI.
  **Save changes to Vendoo** sends later Anteroom edits to the same item.
  Direct List, Update, Save draft, Delist, shares and offers are disabled.

The Bridge must be version 0.2.55 or newer, connected in the Chrome profile
where the seller is signed in to Vendoo. The web relays commands through the
seller's connected Chrome; the Mac sends them through its local Bridge.
Neither path needs an AI provider to send a manually completed draft.

## Draft safety

The server reserves the remote item ID before writing. An interrupted send is
not replayed: **Check saved draft** reads that ID. A missing item confirmed by
Vendoo releases the reservation for a new send. A draft with different fields
stays available for review rather than being silently overwritten or duplicated.

Updates write only Anteroom's changed fields with a Firestore version
precondition. Other Vendoo edits and listing states remain owned by Vendoo.
Readback checks the fields written. No route publishes, delists or deletes a
Vendoo item. The settings switch cannot change during an active listing job.

Photos are captured from the approved revision, delivered through the existing
job-scoped photo endpoint, and uploaded before creating the item. Database
connections and account claim locks are released during browser and storage
waits. Firebase credentials stay in extension memory; they never enter server
responses, job events, logs or Anteroom's extension storage.

The transport follows the October 23 private draft workflow, adapted to the
current seller isolation, photo approval and browser relay. It does not restore
the old unbounded item polling or full network-payload logging.

## Verification

- `server/tests/test_optional_vendoo.py`: default mode, all seven direct List
  gates, active-job switching, upload/create/readback order and reserved-ID recovery.
- `server/tests/test_hosted_vendoo_mode.py`: authenticated, account-scoped settings
  and inventory access through the actual hosted routes.
- `vendoo-extension/tests/vendoo-api.test.js`: dormant transport while off,
  credential handling, photo sequencing, account matching and write retries.
- `e2e/optionalVendoo.e2e.ts`: Settings toggle, manual edits, draft review controls,
  reload and switching back on desktop and phone. Vendoo responses are intercepted;
  this test does not establish live Vendoo API compatibility.

A signed-in Chrome run must separately create and read back a private QA draft
before the workflow is considered verified against Vendoo's live API.
