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
4. Approval is remembered for that account in this Chrome profile for 30
   days. Closing a tab, refreshing the page, restarting Chrome or updating the
   Bridge does not require approval again. After 30 days, **Connect Chrome**
   opens the same confirmation window again. The signed-in page reconnects automatically when the
   Bridge returns. Keep an Anteroom tab open for marketplace reads and actions;
   remembered permission does not make a closed or sleeping browser available.
5. **Disconnect**, signing out or changing accounts revokes the old account's
   remembered approval and disconnects its open tabs. Another account needs its
   own approval. Pending requests still expire after two minutes.

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
ID, sender origin/URL and tab URL. It keeps pending approval and active tab
bindings in `chrome.storage.session`. Approved account IDs and revocation
generations live in `chrome.storage.local`, without email addresses or cloud
credentials. A serialized mutation chain prevents concurrent tabs from losing
each other's records. Every read/action verifies both the remembered approval
and the current document/account. Disconnect invalidates all tab bindings and
in-flight form reads for that approval. Status responses contain only connection state, extension
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

`node vendoo-studio/scripts/check-bridge-connection.mjs` uses real Chromium, the
actual extension popup and a disposable profile. Network access is blocked and
the trusted site is served from memory. It verifies approval survives tab/page
reloads, browser restarts and a Bridge version update across restart, and
Disconnect revokes every tab. Hosted
desktop/phone browser tests also exercise recovery after a temporarily missing
Bridge and wait for sign-out revocation before navigating. Pending-approval and
account-isolation cases remain covered by the worker tests. These checks perform
no marketplace actions and use no real accounts.

An installed-build check still needs the seller's desktop Chrome: connect once,
close/reopen the signed-in site, restart Chrome, then disconnect/sign out and
verify approval is required again. Chrome on iPhone is not an extension host;
the native iPhone app reads the same shared records after desktop Chrome saves them.

Bridge 0.2.39 adds a strictly bounded read-only General-field payload and native
form response for Poshmark previews. The original connection protocol remains
unchanged; no cloud credential is added. See [form previews](cloud-marketplace-forms.md).

## Signed approvals

Any script that runs on the Anteroom page can post a command to the content
script, so the page's `confirm: true` does not show that the seller confirmed
anything. Since Bridge 0.2.68, every command the hosted API relays carries
`payload.approval`: an ES256 compact JWS signed by the API
(`services/bridge_approvals.py`) when the page takes the command. Its claims
bind the seller's account (`sub`), the command (`cmd`), marketplace (`mkt`),
action (`act`), listing (`lid`), job and message ids, a SHA-256 of the whole
command in sorted-key JSON (`sha`), the photos the page may relay with it
(`pho`), a single-use id (`jti`) and a five-minute expiry.

The page reads a List's or Vendoo draft's photos itself and hands their bytes to
the Bridge beside the command, outside the hashed command. So `pho` lists
`{id, sha256}` for each photo approved for the job: the hex SHA-256 recorded
when the photo was copied for that job (`approved_photos`), which is what the
relay's photo route serves. Other commands get an empty list.

The worker (`background/cloud-approvals.js`) checks the signature with the
public key it ships with, then the expiry (one minute of clock leeway), each
bound field against the command and the page's approved account, and the hash.
It hashes each relayed photo and refuses the command (`approval: "photos"`) if
any is missing from `pho` or has other bytes; that refusal does not use the
approval up, so the page can still send the approved bytes. It remembers each used `jti` in `chrome.storage.local` until it expires and
refuses a second use. It strips the approval before running the command. A
refusal returns `error_code: "rejected"` with the reason, and nothing reaches
the marketplace.

Signed: every relayed command, reads (sales history, imports, sign-in status,
likes, promotion checks and results) and `job.cancel` included, so a script on
the page can neither read the seller's marketplace data through the Bridge nor
stop a running List. A Bridge declares `marketplace.signed_commands`, and the
API signs every command, with `pho`, only for one that does: an older Bridge
would refuse the extra field on reads. A Bridge whose
`BRIDGE_APPROVAL_PUBLIC_KEY` is empty refuses every relayed command. The key and
rollout order are in [the operating guide](hosted-production.md#6-bridge-approval-key).

Transition: Bridges 0.2.56 to 0.2.67 declare `marketplace.signed_approvals` instead.
For them the API still signs only the writes (`marketplace.execute`,
`marketplace.result`, `marketplace.delete`, `marketplace.promote`,
`job.vendoo_api`), without `pho`. Remove that path (`SIGNED_APPROVALS` and
`SIGNED_COMMANDS` in `bridge_approvals.py`) after 2027-02-01.

The Mac app's loopback socket does not use these approvals. Its pairing token
comes from `/api/extension/pairing-token` on `127.0.0.1`, which only a program
on the same computer can reach. Writes there carry `confirm: true` only.

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
web reads them in the seller's Chrome. The hosted API queues a named read
asked to update instead of being sent a command it cannot run. Each later read
gets its own capability name (`NEEDS` in `hosted_browser_relay.py`). A read carries
no job and only the inputs listed for it in `docs/json-contract.md`; the
content script and worker reject any other field, URL or command. The Bridge
answers with the choices or one fixed error code, never the marketplace's own
response, headers or cookies. Reads are GETs on the marketplace's own sell-page
endpoints and do not claim the listing-action lock.

discounts and the profile's package, returned as those three values only.

Bridge 0.2.44 adds `marketplace.sessions` and `marketplace.connect` under the
capability `marketplace.sessions`, for Settings → Connections on the web. The
first returns each Chrome marketplace's sign-in state, expiry and account label,
as the Mac's Connections shows them. The second runs only when the seller
chooses Sign in: it takes one marketplace from a fixed list and opens that
marketplace's own sign-in page in Chrome, exactly as the Mac's Sign in does, and
waits while a listing action is running.

Bridge 0.2.46 removes Facebook: the `marketplace.listing_state` command and the
`facebook.listing` capability that 0.2.45 added are gone, and neither name is
reused.

Bridge 0.2.47 adds `marketplace.sales_history`. The seller's connected tab can
command accepts one supported marketplace and at most 2,000 settled order IDs;
it returns normalized order lines without buyer details or credentials.
Reads preserve seller corrections and frozen Vendoo history. A partial or
unreadable history cannot clear older listing-only reports.

Bridge 0.2.53 adds `marketplace.import.page` and `marketplace.import.listing`
under `marketplace.import`, for the Chrome inventory import on a marketplace's
page on the web. They are the reads the Mac's import makes: a page of the
seller's own active listings, or one listing checked against the signed-in
only a cursor or a listing id. A refusal carries the Bridge's own sentence and
code, as the Mac shows it, never the marketplace's response. Nothing on the
marketplace changes.

Bridge 0.2.52 adds `marketplace.promotions`: bounded reads of a listing's share
or offer eligibility, recent Poshmark/Depop likes, and an action's stored
result; and a seller-approved share or offer on one Poshmark, Mercari or Depop
listing. Shares are Poshmark-only. The four commands are
`marketplace.promotion_check`, `marketplace.like_events`,
`marketplace.promotion_result` and `marketplace.promote`. The write requires
`confirm: true`, a named marketplace/action/listing, an action ID and reviewed
prices and minimums. The Bridge checks the same connection, document and
account as other relayed commands, rejects extra fields and uses the desktop
operation lock and durable journal. A repeated action ID never sends again.
These commands cannot list, publish, update or delist a listing.

The hosted promotion timer uses each connected seller's row-secured context.
Rules are saved off; enabling one requires the seller's confirmation. Existing
minimums, daily caps, cooldowns and stop conditions apply. The first timer pass
waits a full tick; interrupted or unconfirmed sends stay unknown and pause their
rule. Disconnecting Chrome suspends checks until a capable tab reconnects.
