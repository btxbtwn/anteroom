# Anteroom Bridge

The extension runs in the background and connects Chrome to Anteroom. It lets Anteroom save drafts, list, update and delist on Poshmark, Mercari, Depop with the seller's own Chrome sign-ins, read sales and likes, and import existing listings. Review, approval and results live in Anteroom. The extension has no toolbar popup, on-page panel, or field highlighting. Hosted connections use a separate approval window.

## Installation

1. Use **Connect Chrome** in Anteroom to open everyday Chrome.
2. Open `chrome://extensions/` and enable **Developer mode**.
3. Click **Load unpacked** and select the repo's `vendoo-extension/` folder. Zip users select `~/Library/Application Support/Anteroom/vendoo-extension`.
4. Keep Anteroom running. The extension pairs automatically with Anteroom on `127.0.0.1:4318`.

## Hosted web connection

**Local-app users:** keep the Bridge installed by the local app’s Connect Chrome. Update the app and its bundled Bridge together. A standalone web-test ZIP has no matching app build stamp, so local marketplace actions reject it. To restore the local app, disable the standalone copy and reload `~/Library/Application Support/Anteroom/vendoo-extension`.

Bridge 0.2.38 adds **Connect Chrome** at the hosted Anteroom site. Reload the updated extension and web page, sign in, choose Connect Chrome, and approve the extension window. No sign-in tokens are passed to the extension. Bridge 0.2.39 also reads Poshmark’s native form for the hosted draft preview. Hosted listing mutations are not enabled yet. See [the connection protocol and live test](../docs/cloud-extension.md).

## Usage

1. Create or import a listing in Anteroom and review it there.
2. Choose **Save draft** or **List** on a marketplace tab in Anteroom. Listing, updating and delisting run only when the seller confirms them.
3. Follow the result in Anteroom.

Chrome still shows the extension in its extensions menu for browser-managed permissions and removal. Clicking its icon does not open an extension interface.

After updating the extension, reload it in `chrome://extensions/`.

## Companion Skills

`skills/list-this/SKILL.md` is the canonical source for listing rules and the JSON payload shared by Anteroom and the extension. Its references live in top-level `skills/list-this/references/`. The payload is documented in `docs/json-contract.md`.

## Permissions

Every permission in `manifest.json` has a caller. Remove a permission when its last caller goes away.

| Permission | Why |
|---|---|
| `alarms` | Wakes the worker to reconnect to Anteroom. |
| `storage` | Keeps the Anteroom pairing token and the journal of marketplace actions, so a lost reply is read back instead of repeated. The worker limits it to itself and the extension's own page (`TRUSTED_CONTEXTS`), so content scripts cannot read it. |
| `cookies` | Checks whether the seller is signed in to a marketplace. Cookie values stay in Chrome. |
| `declarativeNetRequestWithHostAccess` | Sets the headers marketplace requests need. |
| `scripting` | Runs marketplace requests inside the seller's signed-in marketplace tab. |
| `tabs` | Finds and opens marketplace tabs. |
| `system.display` | Places a new Chrome window inside a real display work area. |

Host permissions cover eBay, Poshmark, Mercari, Depop, Etsy, Facebook, the Amazon S3 buckets their presigned photo uploads go to (`*.s3.amazonaws.com`, `*.s3-accelerate.amazonaws.com`), and Anteroom on `127.0.0.1:4318`. Direct marketplace requests go through `background/marketplace-fetch.js`, which bounds each request by the operation's deadline, retries only reads and photo uploads, and logs one `[POSHMARK]`/`[MERCARI]`/`[DEPOP]` line per step (status, error code, latency; never cookies, tokens or listing text). eBay and Etsy listings go through their APIs from Anteroom, not through the extension. The Chrome sign-in check for Depop, Poshmark, Mercari reports only whether the session is present. Cookie values and tokens stay in Chrome.

## Development

Run the worker unit tests from the repo root:

```bash
node --test 'vendoo-extension/tests/*.test.js'
```

They load `background.js` and its `background/` modules into one Node VM with a stubbed `chrome` API.

For live pages:
1. Reload the extension in `chrome://extensions/`.
2. Save a draft from Anteroom and check its result. Never list while testing.
3. Open the service worker's DevTools from `chrome://extensions/` and filter by the marketplace prefix (`[POSHMARK]`, `[MERCARI]`, ...).
4. When a marketplace may have changed its sell page, run its form sweep in `tools/form-sweep/` (see the root `AGENTS.md`, **Verification**).
5. To learn the shape of a marketplace's own requests, use the Network panel of Chrome's DevTools on its page while you act by hand, and keep only operation names, paths and field names. The Bridge does not ask for the `debugger` permission, so it cannot record them itself.
