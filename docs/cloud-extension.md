# Hosted web app and Anteroom Bridge

The hosted app can connect to the extension in the seller's current desktop
Chrome tab. This is a browser-local site permission, separate from the Mac API's
loopback WebSocket pairing. It does not register a worker with the server or
start a marketplace job.

## Connect

1. If you use the local app, update Anteroom and use its **Connect Chrome** to
   install the matching managed Bridge. Standalone web-test ZIPs are for
   web-only testing; they lack the local app’s build stamp and are rejected
   by local marketplace actions. The app and Bridge must be updated together.
   For web-only use, install/load Bridge 0.2.38 or later in desktop Chrome. For an
   unpacked installation, reload the extension in `chrome://extensions` after
   updating its folder, then reload the hosted page. Keep only one Bridge copy
   enabled; disable the older copy if you load a separately extracted folder.
2. Sign in at `https://myanteroom.com`.
3. Choose **Connect Chrome** and approve **Connect browser** in the extension's
   confirmation window. The web page then shows **Chrome connected**.
4. **Disconnect** removes that tab's connection. Signing out or changing
   accounts also clears it. Closing the tab, navigating away from the hosted
   origin, reloading/updating the extension or restarting Chrome disconnects it.
   Refreshing the same signed-in hosted page preserves an approved connection.

The extension has no toolbar popup. The new extension-owned window exists only
to confirm a requested connection. Chrome on iPhone cannot run this extension.

## Protocol and trust boundary

A manifest content script runs only on the exact hosted HTTPS origin, in the
isolated world and top frame. The page uses origin-targeted `window.postMessage`;
the content script forwards explicit connection requests to
the extension worker. Both ends reject extra fields. Supabase access/refresh
tokens, API keys and photos never cross this connection transport. The only
marketplace write is a server-signed Save draft, described below, with the
optional Poshmark reader. There is no arbitrary fetch, script execution, tab navigation, cookie
or automation proxy exposed to the page.

The worker independently checks Chrome's sender extension ID, frame ID, document
ID, sender origin/URL and tab URL. It keeps per-tab account labels and pending
approval in `chrome.storage.session`, which is not exposed to content scripts by
default. A serialized mutation chain prevents concurrent tabs from losing each
other's records. Status responses contain only connection state, extension
version and an explicit supported-actions list, never another account's identity.

Only the extension's own `cloud-connect.html` page can approve or deny a request.
Requests expire after two minutes. Approval probes the exact document that asked
for it and confirms its account label is still current. A closed/reloaded page,
sign-out, a changed account or a closed confirmation window invalidates pending
approval. Delayed sign-out for an old account cannot clear a replacement account's
connection.

The account ID/email are labels supplied by the signed-in web app. This local
permission and its UI status are **not server authentication or authorization**.
The hosted API still verifies Supabase sessions and enforces seller-scoped
Postgres/Storage access. No data API accepts a browser connection record as a
credential. Marketplace actions need explicit contracts, approval,
concurrency checks and owner verification before the supported-actions list can
grow. Bridge 0.2.39 adds [Poshmark form reads](cloud-marketplace-forms.md);
Bridge 0.2.40 adds Save draft on Poshmark, Mercari and Depop, which the server
authorizes by signing each command (below). List, Update and Delist are not exposed.

This follows Chrome's documented [content-script isolation](https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts),
[message passing and sender checks](https://developer.chrome.com/docs/extensions/develop/concepts/messaging),
and [session-storage lifecycle](https://developer.chrome.com/docs/extensions/reference/api/storage#storage_areas).

## Verification

Node VM tests cover the content-script and worker boundary: exact origins/frames,
no credential forwarding, popup-only approval, stale document/account requests,
expiry, simultaneous tabs, worker reconstruction, navigation and sign-out.
Frontend transport tests cover request correlation, rejected foreign replies,
identity-only payloads, timeout and unsupported origins. Run the complete extension
and frontend suites before shipping.

A live test still needs desktop Chrome with the updated unpacked extension:
connect and approve; refresh and check it stays connected; disconnect and reconnect;
sign out and verify it no longer reports connected for a new account. Approving an
old request after signing out or reloading must fail. This test does not perform
marketplace actions or touch listings.

Bridge 0.2.39 adds a strictly bounded read-only General-field payload and native
form response for Poshmark previews. The original connection protocol remains
unchanged; no cloud credential is added. See [form previews](cloud-marketplace-forms.md).

## Save draft from the web app (Bridge 0.2.40)

On Poshmark, Mercari and Depop, the web app offers **Save draft** when the
current Chrome tab is connected and the Bridge advertises
`marketplace.save_draft`. Otherwise the marketplace's pane says **Connect Chrome
to save drafts** (or asks for a Bridge update; in the Mac app's window, to open
the account in desktop Chrome). The outcome is the Mac's: a private draft on the
marketplace, read back and checked, and recorded on the listing. It never lists,
updates, delists or publishes; List stays in the Mac app.

1. **The server records and signs.** `POST /api/conversations/{id}/native-marketplaces/{mp}/chrome-draft`
   (`mp` is `poshmark`, `mercari` or `depop`; the body is only `revision_id`)
   runs the Mac's checks against the current revision, minus the local Bridge
   check, under the same per-marketplace busy rules (one action per marketplace,
   nothing beside a delist or inspection). It copies the approved photos, creates
   the `jobs` row (`native_{mp}_save_draft`) and the `marketplace_listings` record
   (`saving`), and returns `{record, command}`. The command is the Mac's
   `marketplace.execute` payload plus `user_id`, `expires_at` (5 minutes) and a
   random operation token, signed with ECDSA P-256/SHA-256 (see
   [json-contract.md](json-contract.md#web-save-draft-command)). Only the token's
   SHA-256 is stored, on the job, with a 15-minute lifetime. Video is not sent.
2. **The page relays.** The page posts `marketplace_save_draft` with the command
   as `{body, signature}`. The content script accepts it only from the top frame
   of `https://myanteroom.com`, for the account the tab is connected as, with
   exactly those keys and a base64url body and 64-byte signature.
3. **The Bridge verifies, then saves.** The worker verifies the signature with the
   public key it ships with (`cloud-command-key.js`), and requires `kind:
   marketplace.save_draft`, `user_id` equal to the connected account, an
   unexpired `expires_at`, `action: save_draft`, `confirm: false`, a supported
   marketplace and no video. It then checks the tab's approved connection and
   document, as for form reads. The save runs through the Mac's journaled native
   path, claiming that marketplace's scope; the action is forced to `save_draft`.
   Photos come from `GET https://myanteroom.com/api/operations/{id}/photos/{photo_id}`
   with `Authorization: Bearer <token>`: only that running operation's approved
   copies, up to 20 MB each.
4. **The Bridge reports.** The worker posts the result to
   `POST https://myanteroom.com/api/operations/{id}/result` with the token
   (retrying network failures), so closing the tab does not lose it, and answers
   the page with only `ok`, `recorded` and an error code. The server applies the
   result with the Mac's rules and clears the token: a second report, or a photo
   read, is refused. A verified command whose tab is not connected is reported as
   a refusal so the item is free again at once.

A save Chrome never reports stays `saving` until the token expires. The web job
watchdog then fails the job and leaves the record **Unconfirmed**; nothing
re-runs it, and Chrome is never asked again. The seller checks the marketplace and
chooses **I checked it myself…** (or Mark as listed). A replayed command never
saves twice: the Bridge's journal returns the first result.

Those two operation routes are the only hosted routes reached without a seller
session. The session middleware lets exactly their paths through; the routes find
the owner with `anteroom_private.browser_operation_owner(token_hash)` (a
`SECURITY DEFINER` function the trusted process login may call), then work in
that seller's row-secured context and read Storage server-side.

### Signing key

The hosted API reads `ANTEROOM_COMMAND_SIGNING_KEY` (base64 PKCS#8 DER, or PEM).
`vendoo-studio/scripts/command-signing-key.py` creates a key pair, writes the
public key into `vendoo-extension/cloud-command-key.js` and prints the private
key as one secret line for Fly:

```bash
cd vendoo-studio && .venv/bin/python scripts/command-signing-key.py | fly secrets import --stage -a peaceful-meadowstone-6944
```

Commit the updated `cloud-command-key.js`; the Bridge offers Save draft only
when it has a key, and `/api/runtime` lists `draft_marketplaces` only when the
server has one and its job watchdog runs. A new key replaces the old one, so
the seller must update the Bridge with it. The hosted sandbox makes a
throwaway key; tests make their own.

### Live check

Needs a signed-in desktop Chrome with Bridge 0.2.40 (reload it in
`chrome://extensions`, then reload the page) and a Poshmark, Mercari or Depop
login. Connect Chrome, open a listing with photos, choose **Save draft** on
Poshmark, then confirm the row shows **Draft** with **View draft**, the draft
is private on Poshmark with the listing's photos, and nothing was published.
Close the tab while a save runs: the listing still shows the result.


## Restore the local app after loading a standalone web-test ZIP

Disable the downloaded Bridge in `chrome://extensions`. Choose **Connect Chrome**
in the local Anteroom app. In Chrome's extensions page, enable Developer mode and
load the managed folder `~/Library/Application Support/Anteroom/vendoo-extension`
with **Load unpacked**, or re-enable/reload that managed copy if it is already
listed. Keep only the managed copy enabled. Local Connect Chrome installs a
worker with the exact build stamp expected by that app; a standalone ZIP has no
such stamp. Repeatedly reloading the standalone folder cannot repair that mismatch.

Do not copy a stamp from one build onto another or relax the marketplace build
check. New cloud features become available to local-app users when the app's
matching bundled Bridge includes them. A web-only test ZIP is not a substitute
for an app-and-extension update.
