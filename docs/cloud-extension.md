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
tokens, API keys, photos and marketplace mutation commands never cross this
connection transport. The optional Poshmark reader is described below. There is no arbitrary fetch, script execution, tab navigation, cookie
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
credential. Future marketplace actions need explicit contracts, approval,
concurrency checks and owner verification before the supported-actions list can
grow. Bridge 0.2.39 adds only [Poshmark form reads](cloud-marketplace-forms.md); listing mutations are not exposed.

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

## Marketplace actions from the web (Bridge 0.2.40)

A connected tab relays the seller's own approved Poshmark, Mercari and Depop
List, Update, Save draft and Delist actions to the Bridge. The hosted API starts
each action only after the seller confirms it, then queues one of four commands
for that seller's tab (`marketplace.execute`, `marketplace.result`,
`marketplace.delete`, `job.cancel`; see `services/hosted_browser_relay.py`). The
tab asks for the next command, reads the job's approved photos with its own
session, and posts the command with those photo bytes to the content script.

The content script and worker accept only that contract
(`CloudMarketplaceProtocol.command` and `.photos`): a fixed command type, a
32-hex request id echoed in the payload, a Chrome marketplace, no video, and at
most 30 JPEG/PNG/WebP photos. They check the approved connection, document and
account exactly as for form reads, then run the same handlers the Mac's socket
uses, which still require `confirm` and claim the shared operation lock. The
Bridge's reply goes back to the hosted API, which records what the marketplace
did. Supabase tokens never reach the extension. The Bridge advertises
`marketplace.actions`; an older Bridge leaves these actions refused with an
update message.
