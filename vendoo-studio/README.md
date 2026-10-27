# Anteroom

Local Mac app for generating Vendoo marketplace listings with ChatGPT or Xiaomi MiMo, then filling a draft in Chrome. It never publishes.

## Share a Mac app

Build a self-contained `.app`. Recipients do not need Python, Node, or this git repo.

```bash
cd vendoo-studio
./scripts/setup.sh
source .venv/bin/activate
pip install -e ".[package]"
./scripts/package-macos-app.sh
```

That writes `vendoo-studio/release/Anteroom.app` and `vendoo-studio/release/Anteroom-macos.zip`. The zip also includes **How to Open.txt**.

Publish the zip as the rolling GitHub release (`studio-macos`):

```bash
./scripts/publish-macos-release.sh
```

Pushes to `main` that touch Anteroom, the extension, or listing skills also build and replace that release through `.github/workflows/studio-macos.yml`.

The packaged app does not git-pull. **Check for updates** compares the stamped build SHA against the GitHub release and, if newer, downloads the zip, replaces the `.app`, and relaunches. All Anteroom data stays in `~/Library/Application Support/Anteroom` (listings, photos, drafts, hidden fields, settings, fill logs, logs). API keys remain in macOS Keychain. Settings → Data shows the folder path.

Release builds are signed with the certificate in the GitHub secrets `MACOS_SIGNING_CERT_P12_BASE64` and `MACOS_SIGNING_CERT_PASSWORD`; packaging fails rather than fall back to ad-hoc signing when they are missing, and the updater refuses any download that is ad-hoc signed or fails `codesign --verify --deep --strict`. Keychain recognises later builds by their Apple Team ID, which only an Apple-issued certificate carries. Signed with a self-signed certificate, each update gets a new `cdhash` in the Keychain item's partition list, so macOS asks for the Mac password once per update (the release job warns about it). Signed with a **Developer ID Application** certificate, it asks once more and then never again. To switch, follow the steps at the top of `scripts/setup-signing.sh` and run it with the exported `.p12`. Credentials are updated in place and are never rewritten at startup.

The recipient:

1. Unzips the archive and moves **Anteroom** into Applications.
2. Control-clicks the app and chooses **Open** the first time (not notarized).
3. Signs in with ChatGPT or enters a Xiaomi MiMo API key in Settings.
4. Clicks **Connect Chrome**. Anteroom opens Vendoo in everyday Chrome and copies the listing extension into an Anteroom-managed folder. Load that unpacked folder once from Settings → Connections (`chrome://extensions/` → Developer mode → Load unpacked).

They need macOS 13+ and Google Chrome.

## Quick start (this repo)

From the repository root:

```bash
./setup.sh
./start.sh
```

Or from this directory:

```bash
./scripts/setup.sh
./scripts/dev.sh
```

`setup.sh` creates `.venv`, installs Python and Node packages, and builds the frontend. `dev.sh` starts the API at http://127.0.0.1:4318 and the Vite app at http://127.0.0.1:5173. `./scripts/doctor.sh` checks that the machine is ready.

Requirements: Python 3.12+, Node.js 20+, Google Chrome.

Then:

1. Open **Settings**.
2. Sign in with ChatGPT, or save a Xiaomi MiMo API key and test it.
3. Click **Connect Chrome**, then load unpacked from the folder shown in Settings → Connections so Chrome stays on Anteroom's copy.
4. Sign in to Vendoo. The status bar shows **Extension connected**.

## Command line

`anteroom` is a terminal client for the same HTTP API the app uses. Install it with the backend (the `setup.sh` editable install picks up the `anteroom` script):

```bash
cd vendoo-studio
source .venv/bin/activate
pip install -e .
anteroom version
```

The app does not check a bearer token. It listens on `127.0.0.1:4318`. A saved token is sent as `Authorization: Bearer` only when a proxy in front of another URL requires one. Tailscale Serve, turned on in Settings → Connections, is what protects a remote URL.

```bash
# This Mac. Saved as the current environment.
anteroom auth login --alias local --base-url http://127.0.0.1:4318

# Another Mac on your tailnet. Optional --token if a proxy demands one.
anteroom auth login --alias studio --base-url https://mac.tailnet.ts.net --token "$ANTEROOM_TOKEN"
anteroom auth use studio
anteroom auth list
anteroom auth whoami
```

`ANTEROOM_BASE_URL`, `ANTEROOM_TOKEN`, and `ANTEROOM_ENV` override the saved environment for one shell. Config lives in `~/.config/anteroom/config.json` (mode 0600). `auth list` shows whether a token is stored and never prints it.

Commands are generated from the FastAPI routes, so a new endpoint shows up after the next `anteroom` run (the catalog is cached under `~/.cache/anteroom` and rebuilt when the server code changes):

```bash
anteroom commands                 # one line per route
anteroom commands --json          # machine-readable catalog
anteroom health get
anteroom health status --json
anteroom conversations list --json
anteroom conversations get --conv-id CONV
anteroom boxes create --store Goodwill --title "Monday coats" --price 40
```

`--json` on any command prints one object on stdout: `ok`, `status`, `data`, and `error` when the call failed. Exit codes are `0` success, `1` the server returned an error, `2` bad usage, `3` could not connect, `4` the command is destructive and `--confirm` was missing.

Delete, publish, delist, price changes, live-listing updates, and anything else the API treats as undoable or seller-confirmed are refused until you pass `--confirm`. Publishing still requires the marketplaces by name. The CLI does not fill them in, and it does not publish on its own:

```bash
anteroom vendoo-api conversations-list --conv-id CONV --marketplaces ebay --confirm
anteroom boxes delete --box-id BOX --confirm
```

A path that does not have a generated command yet:

```bash
anteroom api request GET /api/changelog --json
anteroom api schema
```

WebSocket routes (the extension socket and the Claude sign-in terminal) are listed in `anteroom commands` and are not opened by the CLI. The webhook relay is a separate Cloudflare worker; Anteroom calls it outbound, and this CLI does not.

### Hooking up an agent

Settings → Agents shows whether `anteroom` is installed, the address to use, copy-ready setup for Cursor, Claude Desktop, and Claude Code, and a connection test. The same setup is below.

Point the agent at the catalog, or at the MCP server. Both expose the same routes.

```bash
anteroom commands --json
anteroom mcp serve
```

Cursor or Claude can connect with a stdio MCP server:

```json
{
  "mcpServers": {
    "anteroom": {
      "command": "anteroom",
      "args": ["mcp", "serve"]
    }
  }
}
```

Each HTTP route is a tool. Destructive tools require `"confirm": true` and still name marketplaces themselves. `anteroom_commands` returns the catalog, and `anteroom_api_request` calls any path.

## Workflow

1. **Create a listing**.
2. **Upload photos** by dragging them into the photo tray.
3. Optionally add notes (cost, flaws, measurements).
4. Chat to analyze the photos and generate a listing.
5. Review the listing in the right panel.
6. Click **Send to Vendoo**. Anteroom opens a background Vendoo tab in everyday Chrome, fills and saves a draft, closes the tab, and stops before publishing.
7. Open the Vendoo draft to review and publish manually.

### Importing directly from eBay

Connect eBay, then open **Marketplaces → eBay → Listings → Import from eBay**.
Select listings from a page of your active inventory or enter an eBay listing ID.
Imports run one at a time and can stop after the current listing. **Open item**
opens the saved item in Anteroom.

The first version supports active US fixed-price listings in USD without
variations. It verifies the connected seller owns each listing, saves the
description, available quantity (total minus sold), price, SKU, category and
specifics, and downloads its photos. Failed photos are reported so they can be
added before crosslisting. Existing items matched by listing ID are reused;
their edits and photos are never replaced. Conflicting duplicate inventory is
reported instead of guessing an owner.

Imports use eBay's read APIs and local storage only. They do not publish, revise,
delist, migrate eBay's listing format, or contact Vendoo. No eBay credentials or
raw account responses are stored with the imported listing.

### Direct eBay earnings and performance

Open **Marketplaces → eBay → Dashboard** for recorded net order proceeds,
original selling fees, local item costs and profit after those costs. Direct
imports, seller-added links and migrated links all identify the local item.
Costs use each purchased quantity; repeated copies of the same order line do
not double the cost, and distinct lines still count their own quantities.

The selling-fee amount is shown separately when eBay verifies one original
sale transaction. Net proceeds already reflect recorded credits and debits,
including refunds and order charges, so those fees are not deducted again.
Funding movements are excluded. Missing costs, ambiguous inventory links,
unverified quantities and conflicting order lines leave profit unavailable
and explain what needs review. Missing fee detail remains unavailable while
verified net proceeds can still be shown.

Listing performance also includes direct and seller-linked eBay listings.
An identity shared by several local items keeps its marketplace ID as the
title. These reports read eBay and local inventory; no live listings are changed.

### Importing directly from Etsy

Connect Etsy, then open **Marketplaces → Etsy → Listings → Import from Etsy**.
Select listings from your active inventory or enter a listing ID. Imports run
one at a time; you can stop after the current item and open each saved item.

The first version supports active physical listings in USD without variations.
It verifies shop ownership and reads the exact inventory offering, SKU, available
quantity, price, category, item properties, tags, materials and photos. Listings
that have never used Etsy's inventory tools use Etsy's explicit legacy quantity
and price. Multiple products or offerings are rejected instead of being combined.

Existing linked items keep their edits and photos. Conflicting identities are
reported, and missing photos are shown for follow-up. Importing reads Etsy and
saves local inventory; it never changes a live listing or contacts Vendoo.

### Checking linked marketplace inventory

Open an item and click **Sync** to check its linked eBay and Etsy listings
directly. **Marketplace details** shows the listing status, USD price and
available quantity, alongside any differences from the current local item.
**Marketplace differs** flags changed values or a listing that is no longer
active. **Partly checked** means the status was read but price or quantity
could not be verified, such as a variation listing. Failed checks identify the
marketplace and let you retry without hiding other successful checks.

These checks read only. They do not replace local edits, mark an item sold,
publish, update, or delist anything. Ended, expired and inactive listings do
not establish a sale. Existing sale alerts still handle verified sales.
Price and quantity checks support US fixed-price eBay listings and physical
Etsy listings in USD without variations; other listings show status and an
explanation. Check results describe the moment you clicked Sync; click again
after making changes locally or on a marketplace.

### Recovering direct marketplace orders

Connect eBay or Etsy, then open **Marketplaces → eBay/Etsy → Orders → Recover orders**.
Choose a UTC range of up to 30 days within the last 90 days. Recovery reads orders
**modified** in that period, so it also finds updates to older orders. Pages run
sequentially; stop after the current page and use **Continue from page** to resume.
A failed page can be retried without replaying successful pages. Reconnecting the
account stops the old recovery session. All pages reuse the first page's upper
time boundary. Run recovery again to include orders changed while it was running.

The local order-line ledger reuses webhook identities, preserves dismissed
alerts and existing assignments, and reports identity conflicts without guessing.
Unpaid and unsupported order states are counted as skipped. eBay uses Trading
order-line IDs, matching its notifications; Etsy uses receipt and transaction IDs.
Completed cancellations and refunds update order status and stop treating those
lines as active sales. A pending cancellation stays visible for review. Partial refunds remain paid sales. eBay payment/refund status is verified through
its order API while retaining the Trading line IDs used by notifications.

Recovery never notifies, restocks, relists, delists, or subtracts historical orders
from your edited/imported local quantities. Review stock with **Sync** before any
marketplace action. A quantity/status update makes older stock observations stale.
Counts cover the pages checked; identity conflicts and limits remain visible for
follow-up. Only order/listing/line IDs, quantity, status and dates enter this ledger;
buyer names, contact information, addresses and raw order bodies are not stored.
[eBay recovery](https://developer.ebay.com/DevZone/XML/Docs/Reference/ebay/GetOrders.html)
covers its 90-day retained sales history, with at most 30 days per modification window.

### Partial sales and remaining stock

A multi-unit sale keeps the item listed until its remaining stock is verified.
Open the item and click **Sync**: Anteroom saves the verified marketplace stock
read, and its sale alert shows either the remaining quantity, exhausted stock,
or a request to check. A new sale makes an older stock read stale. Replacement
listing IDs do not reuse the old listing's stock. Ended or inactive listings
alone do not establish that all units sold.

Local quantities are never reduced by subtracting historical orders: they may
already reflect those sales or include your edits. Partial or unverified
multi-unit sales never trigger automatic delisting, even with that option
enabled. Review stock on every marketplace before approving quantity updates
or delisting. Existing single-item sale behavior is retained; a fresh direct
stock check can show remaining stock there too.

### Editing something that is already listed

Anteroom writes the Vendoo **form**. Vendoo carries a form change onto a live
marketplace listing only when the listing is taken down and posted again, so
after **Update Vendoo** the buyer still sees the old copy until you delist and
relist in Vendoo (the ⋮ menu beside Vendoo Form → **Delist Item**, which takes
the item off every marketplace at once, then list it again).

Anteroom labels every step of that rather than leaving it to memory. A blue
**unsent** chip says the edit has not reached the Vendoo form yet — press
**Update Vendoo** and it clears. After that the listing wears an amber
**relist** badge in the sidebar, the editor shows which marketplaces are behind,
and **Filters → Unsent edits / Needs relist** collect each set. The badge survives the
delist itself — in that middle window Vendoo reports the item as a draft, because
it is live nowhere, and the banner switches to "list it again to finish". It
clears itself once Vendoo reports a listing date newer than Anteroom's last write,
or on **Done** in the banner for a relist Anteroom cannot see.

## Architecture

```
vendoo-studio/           Local web app (FastAPI + React)
├── server/              Python backend
│   └── vendoo_studio/
│       ├── main.py      FastAPI application
│       ├── config.py    Settings and paths
│       ├── database.py  SQLite setup
│       ├── models/      Pydantic schemas, SQLAlchemy models, protocol
│       ├── routes/      API endpoints
│       ├── services/    Keychain, photos, extension gateway
│       ├── repositories/ Database queries
│       └── providers/   ChatGPT and Xiaomi MiMo clients
├── src/                 React frontend
│   ├── app/             Main app and routing
│   ├── api/             API client
│   ├── components/      UI components
│   └── styles/          CSS
└── data/                SQLite database and photo storage (dev only)
```

## Security

- Binds only to `127.0.0.1` (localhost).
- Optional **Settings → Connections → Remote access** uses private Tailscale Serve to proxy HTTPS to that loopback port. Funnel stays off; only devices on your Tailnet can open the link. Same Anteroom process and data as on the Mac — not a second install.
- API keys and ChatGPT tokens live in macOS Keychain, never in SQLite, browser storage, or logs.
- Never sent to the React frontend or Chrome extension.
- Photos are served only through the Anteroom origin (`GET /api/photos/{id}`).
- No publication automation — only draft saving.

## Development

```bash
./scripts/dev.sh     # backend + frontend together
./scripts/doctor.sh  # check Python, Node, Chrome, ports
cd vendoo-studio && npm run build
PYTHONPATH=server .venv/bin/python -m pytest -q server/tests
```

Set `VENDOO_STUDIO_DEV=1` if you start the backend yourself and want auto-reload.

The extension connects to `ws://127.0.0.1:4318/api/extension/ws`. **Connect Chrome** opens Vendoo in everyday Chrome and reloads Anteroom's listing extension. Load unpacked from the folder shown in Settings → Connections (`data/vendoo-extension/` in development).

## Database

SQLite at `data/vendoo_studio.db` in development, or `~/Library/Application Support/Anteroom` in the packaged app. Replacing the `.app` does not touch this folder. Tables:

- `conversations` — listing sessions
- `messages` — chat history
- `photos` — uploaded images
- `listings` — current listing state
- `listing_revisions` — edit history
- `jobs` — automation jobs
- `job_events` — per-job event log
- `category_trees` / `category_tree_nodes` — full General/eBay/Poshmark/Mercari/Depop/Etsy category trees

The same data folder also holds `photos/`, `fill-logs/`, `settings.json` (marketplaces, hidden fields, UI prefs), `pairing_token.txt`, `catalog-index/`, `vendoo-extension/`, `backups/`, and `logs/`.

### Backups

Anteroom snapshots the database to `backups/` on startup, every six hours, and before any update or schema migration. Snapshots are written with `VACUUM INTO`, so they are consistent while Anteroom keeps running, checked with `PRAGMA integrity_check` before they are kept, and then gzipped — the database is mostly JSON text and compresses around six-fold, which matters when a month of copies is kept and every one is also sent to the backup folder. Recent snapshots are kept for a day, then one per day for a month, and the newest three are never discarded.

`restore-db.sh` unpacks a snapshot for you. To look inside one by hand:

```bash
gunzip -c backups/vendoo_studio-20260920-031932-timer.db.gz > /tmp/look.db
```

**Copy them off this machine.** Snapshots in `backups/` die with the disk they sit on. Set a backup folder in **Settings → Data → Backups** — an external drive, a synced folder, or a network share — and every snapshot goes there too, along with your photos. Photos are copied incrementally and are never deleted from the backup folder when they are deleted in Anteroom, because that is the copy you want when the deletion was a mistake. The same thing over the API:

```bash
curl -X PUT http://127.0.0.1:4318/api/backups/folder \
  -H 'Content-Type: application/json' \
  -d '{"folder": "/Volumes/Backup/Anteroom"}'
```

Do not point that at the live database, and do not put `data/` itself inside Dropbox or iCloud: a sync daemon copying an open SQLite file mid-write produces snapshots that will not open.

| | |
|---|---|
| List snapshots | `GET /api/backups` |
| Snapshot now | `POST /api/backups` |
| Check a database | `./scripts/check-db-schema.sh [db]` |
| Restore one | `./scripts/restore-db.sh <snapshot.db>` |

Restoring quits nothing for you — close Anteroom first. The current database is snapshotted before it is replaced, so restoring the wrong copy is itself undoable.

### Schema changes

The schema is owned by Alembic; revisions live in `server/vendoo_studio/migrations/versions/`. On startup Anteroom migrates to the newest revision, snapshotting first, and refuses to open a database stamped with a revision it does not recognise — that is what stops an older build from writing into a database a newer one created.

A database from before migrations existed is brought up to the current schema, checked for anything still missing, and only then recorded as current.

`test_the_models_match_the_migrations` compares what the migrations build against what the models declare, so a model change without a revision fails in CI rather than on someone else's machine. Adding a column to a model requires a revision:

```bash
.venv/bin/alembic revision --autogenerate -m "add whatever"
```


Category leaves, observed schemas, skill dropdown options, listing-rule chunks, and extension fill helpers are materialized under `catalog-index/` in the same data directory and searched with Semble (`GET /api/catalog/search`). First launch may download the local embedding model once.

### Category tree seed

A checked-in seed lives at `data/category-trees-seed.json.gz` (those two tables only). Anteroom imports it automatically on launch whenever any of the six marketplace trees is missing or incomplete — including after a packaged app update that ships the seed. Listings, photos, jobs, and settings are left untouched.

Manual import (optional):

```bash
cd vendoo-studio
./scripts/category-trees.sh import \
  --db "$HOME/Library/Application Support/Anteroom/vendoo_studio.db"
```

Export from a DB that already has complete trees with `./scripts/category-trees.sh export --db /path/to/vendoo_studio.db`.
