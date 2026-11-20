# Anteroom Bridge

The extension runs in the background and connects Chrome to Anteroom. It lets Anteroom save drafts, list, update and delist on Poshmark, Mercari, Depop with the seller's own Chrome sign-ins, read sales and likes, and import existing listings. Review, approval and results live in Anteroom. The extension has no toolbar popup, on-page panel, or field highlighting. Hosted connections use a separate approval window.

## Installation

1. Use **Connect Chrome** in Anteroom to open everyday Chrome.
2. Open `chrome://extensions/` and enable **Developer mode**.
3. Click **Load unpacked** and select the repo's `anteroom-extension/` folder. Zip users select `~/Library/Application Support/Anteroom/anteroom-extension`.
4. Keep Anteroom running. The extension pairs automatically with Anteroom on `127.0.0.1:4318`.

## Hosted web connection

**Local-app users:** keep the Bridge installed by the local app’s Connect Chrome. Update the app and its bundled Bridge together. A standalone web-test ZIP has no matching app build stamp, so local marketplace actions reject it. To restore the local app, disable the standalone copy and reload `~/Library/Application Support/Anteroom/anteroom-extension`.

Bridge connects to the hosted Anteroom site (`https://myanteroom.com`) via **Connect Chrome** with explicit approval in the extension's `cloud-connect.html` window. No Supabase sign-in tokens are passed to the extension. While an Anteroom tab is open, the hosted API relays ES256-signed marketplace reads, confirmed listing mutations/delists, imports, and promotions for Poshmark, Mercari, and Depop through the Bridge. See [the connection protocol and signed approvals](../docs/cloud-extension.md) and [relayed commands](../docs/json-contract.md).

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

Host permissions cover eBay, Poshmark, Mercari, Depop (`www.depop.com` and `webapi.depop.com`), Etsy, Vendoo (and its Firebase/Cloud Functions endpoints used for one-way Vendoo import), the Amazon S3 buckets presigned photo uploads go to (`*.s3.amazonaws.com`, `*.s3-accelerate.amazonaws.com`), and Anteroom loopback (`http://127.0.0.1:4318/*`, `ws://127.0.0.1:4318/*`). Direct marketplace requests go through `background/marketplace-fetch.js`, which bounds each request by the operation's deadline, retries only reads and photo uploads, and logs one `[POSHMARK]`/`[MERCARI]`/`[DEPOP]` line per step (status, error code, latency; never cookies, tokens or listing text). eBay and Etsy listings go through their APIs from Anteroom, not through the extension. The Chrome sign-in check for Depop, Poshmark, and Mercari reports only whether the session is present. Cookie values and tokens stay in Chrome.

## Development

Run the lint and worker unit tests from the repo root:

```bash
anteroom/node_modules/.bin/oxlint -c anteroom-extension/.oxlintrc.json --deny-warnings anteroom-extension
node --test 'anteroom-extension/tests/*.test.js'
```

The unit tests load `background.js` and its `background/` modules into one Node VM with a stubbed `chrome` API.

For live pages:
1. Reload the extension in `chrome://extensions/`.
2. Save a draft from Anteroom and check its result. Never list while testing.
3. Open the service worker's DevTools from `chrome://extensions/` and filter by the marketplace prefix (`[POSHMARK]`, `[MERCARI]`, ...).
4. When a marketplace may have changed its sell page, run its form sweep in `tools/form-sweep/` (see the root `AGENTS.md`, **Verification**).
5. To learn the shape of a marketplace's own requests, use the Network panel of Chrome's DevTools on its page while you act by hand, and keep only operation names, paths and field names. The Bridge does not ask for the `debugger` permission, so it cannot record them itself.

## Chrome Web Store

Anteroom Bridge is store item `empinkdlhndpfoigoipbiiofaeepcheo`. After push CI succeeds on an exact commit of `main` (CI requires a manifest version bump for every Bridge code change), the **Chrome Web Store** workflow (`.github/workflows/chrome-web-store.yml`) packages the files Chrome loads, the same ones the Mac app copies, and submits them for review through the Chrome Web Store API (v2). It submits only a version newer than every one the store has published or is reviewing, so unchanged versions and reruns never resubmit a build, and it cancels an older submission still in review first. Google reviews each version before sellers get it, under the visibility set in the developer dashboard. The zip is kept as a run artifact for 30 days. To build it locally: `python3 .github/scripts/chrome-web-store.py package --out bridge.zip`.

One-time setup:
1. In a Google Cloud project, enable the **Chrome Web Store API**, create a service account, and download a JSON key for it.
2. In the [developer dashboard](https://chrome.google.com/webstore/devconsole), open **Account**, add the service account's email, and copy the **Publisher ID**.
3. Add both to the `production` environment's secrets in GitHub: `CHROME_WEB_STORE_SERVICE_ACCOUNT` (the whole JSON key) and `CHROME_WEB_STORE_PUBLISHER_ID`.
4. Run the workflow once from the Actions tab (**Run workflow**, branch **main**) to submit the current version. Manual runs also require successful push CI for that exact main commit; other branches cannot submit. Rejected, cancelled, missing or unknown submission results fail the workflow instead of reporting success.
