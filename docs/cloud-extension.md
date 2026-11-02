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


## Which Bridge may run marketplace actions

The Bridge reports what it can run when it connects, both to the Mac app
(`capabilities` in `extension.ready`) and to the web page (`supportedActions`).
List, Update, Save draft and Delist need `marketplace.actions`. Any copy that
declares it may run them, whichever build it is: the app's managed copy, an
older one, or a standalone ZIP. Chrome does not need to be updated every time
Anteroom is.

Connect Chrome still loads the app's newest copy, and Settings → Connections
says when an earlier one is running. It warns only when that copy cannot run
marketplace actions.

When a change needs something an older Bridge cannot do (a new command, or a
message it would misread), give it a new capability name. Add the name to
`BRIDGE_CAPABILITIES` in `background.js` and require it in Anteroom, as
`MARKETPLACE_ACTIONS` in `extension_connection.py` does. Never change what an
existing name means.

Keep only one copy enabled in `chrome://extensions`. Two copies take turns on
the connection.

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
most 30 JPEG/PNG/WebP photos. The relay sends only the contract's fields
(`type`, `job_id`, `message_id`, `payload`), never the socket envelope's
`version` or `sent_at`, and gives each cancel its own request id; a test runs
the Bridge's own check on what the relay sends. They check the approved connection, document and
account exactly as for form reads, then run the same handlers the Mac's socket
uses, which still require `confirm` and claim the shared operation lock. The
Bridge's reply goes back to the hosted API, which records what the marketplace
did. Supabase tokens never reach the extension. The Bridge advertises
`marketplace.actions`; an older Bridge leaves these actions refused with an
update message.

## Marketplace reads from the web (Bridge 0.2.42)

Some marketplace forms need choices only the seller's own account can read.
The Mac reads Vinted's categories and brands in its private account window; the
web reads them in the seller's Chrome. The hosted API queues a named read
(`marketplace.vinted_categories`, `marketplace.vinted_brands`) on the same
relay, with its own capability, `vinted.choices`, so an older Bridge is
asked to update instead of being sent a command it cannot run. Each later read
gets its own capability name (`NEEDS` in `hosted_browser_relay.py`). A read carries
no job and only the inputs listed for it in `docs/json-contract.md`; the
content script and worker reject any other field, URL or command. The Bridge
answers with the choices or one fixed error code, never the marketplace's own
response, headers or cookies. Reads are GETs on the marketplace's own sell-page
endpoints and do not claim the listing-action lock.
