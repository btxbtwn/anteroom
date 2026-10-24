# Anteroom

[![CI](https://github.com/btxbtwn/list-this-vendoo-suite/actions/workflows/ci.yml/badge.svg)](https://github.com/btxbtwn/list-this-vendoo-suite/actions/workflows/ci.yml)

Turn product photos into marketplace listing drafts, review them in **Anteroom**, and send approved listings to Vendoo from your Mac. Automated sends stop at saved drafts. You choose when and where to publish.

Anteroom combines a listing editor, inventory, a business assistant, sales analytics, and wholesale sourcing. It uses the listing rules in this repository and a Chrome extension to fill Vendoo forms for eBay, Poshmark, Mercari, Depop, and eligible Etsy items.

[Download for Mac](https://github.com/btxbtwn/list-this-vendoo-suite/releases/tag/studio-macos) · [Run from source](#run-from-this-repo) · [Contribute](CONTRIBUTING.md)

![Anteroom listing workspace with sample inventory on the left, item chat in the center, and marketplace listing forms on the right](docs/screenshots/anteroom-workspace.png)

*The current interface with sample listing content. Screenshots use a separate local database with no personal photos or connected accounts.*

## Roadmap

We plan to roll out direct listing from Anteroom to **Depop, eBay, Etsy, Poshmark, and Mercari**, without requiring Vendoo. These integrations are planned; the current workflow saves drafts through Vendoo.

## From photos to a saved draft

1. Create a listing and drop in the item's photos, or upload folders to create several listings at once.
2. Add known details such as flaws, cost, SKU, and measurements. **Read from photos** can read measurements from ruler photos during bulk upload.
3. Generate the listing, then review the copy, price, categories, and marketplace fields in **Forms**. Use the item's chat to make changes.
4. Approve **Send to Vendoo**. The extension fills and saves a draft in Chrome; approved sends wait in the queue while another send is running.
5. Review the saved draft in Vendoo and publish to the marketplaces you choose.

Edits to an existing listing have two separate steps: **Update Vendoo** writes the Vendoo form, and a delist/relist in Vendoo updates the live marketplace copy. Anteroom tracks unsent edits and listings that still need a relist. See the [existing-listing workflow](vendoo-studio/README.md#editing-something-that-is-already-listed).

## Inside Anteroom

| Workspace | What you can do |
|---|---|
| Inventory | Search by title, SKU, or label; filter drafts, active items, and sales; review listing forms and revision history. |
| Assistant | Ask about sales, stale inventory, and sourcing results. Review proposed listing changes before applying them. |
| Analytics and Marketing | Explore sales and profit, track ad spend, and plan sale events. |
| Sourcing | Compare wholesale clothing boxes and track the items and returns from boxes you bought. |
| Settings | Choose AI providers, connect Chrome, manage backups, and configure private remote access through Tailscale. |

<details>
<summary>Business assistant</summary>

Ask about the shop across listings, sales, and sourcing. Each listing also has its own chat for item-specific work.

![Anteroom business assistant home with suggested questions about sales, profit, aging inventory, and wholesale boxes](docs/screenshots/anteroom-assistant.png)

</details>

<details>
<summary>AI provider settings</summary>

Choose ChatGPT, Claude, Xiaomi MiMo, or Cursor. Configure the primary listing provider, fallback, and photo analysis provider in **Settings → Providers**.

![Anteroom provider settings showing ChatGPT, Claude, Xiaomi MiMo, and Cursor alongside sign-in and listing provider controls](docs/screenshots/anteroom-providers.png)

</details>

## Install on a Mac

You need **macOS 13+**, **Google Chrome**, a Vendoo account, and access to one of the supported AI providers. The app includes its Python runtime and frontend; recipients do not need Python, Node.js, or a clone of this repository.

1. Download `Anteroom-macos.zip` from the [Mac release](https://github.com/btxbtwn/list-this-vendoo-suite/releases/tag/studio-macos), unzip it, and read **How to Open.txt**.
2. Drag **Anteroom.app** into Applications. Control-click it and choose **Open** the first time; follow the included instructions if macOS warns about the developer.
3. Follow the setup guide or open **Settings → Providers**. Sign in with ChatGPT or Claude, or enter a Xiaomi MiMo or Cursor API key.
4. Click **Connect Chrome**, then load Anteroom's unpacked extension once: open `chrome://extensions/`, enable **Developer mode**, and choose **Load unpacked**. Use the folder shown in **Settings → Connections** (normally `~/Library/Application Support/Anteroom/vendoo-extension` for the Mac app).
5. Sign in to Vendoo in that Chrome window. Confirm Anteroom shows the extension as connected before sending a listing.

Listings and photos are stored in `~/Library/Application Support/Anteroom`. **Check for updates** downloads and installs newer Mac builds while preserving that data.

Claude requires Claude Code installed on the Mac; its provider settings show the install and sign-in steps.

## Run from this repo

```bash
./setup.sh
./start.sh
```

Setup creates the Python environment, installs backend and frontend packages, and builds the frontend. Start runs the API at `http://127.0.0.1:4318` and opens the development UI at `http://127.0.0.1:5173`. Complete provider and Chrome setup as described above; development uses `vendoo-studio/data/` for app data.

Requirements: macOS, Python 3.12+, Node.js 20+, Google Chrome.

Quit another running Anteroom instance before starting from source; both use port `4318` by default. If setup or startup fails, run:

```bash
./vendoo-studio/scripts/doctor.sh
```

See the [Anteroom README](vendoo-studio/README.md) for backups, database migrations, packaging, and backend development.

## What's in the repo

| Path | Role |
|---|---|
| [`vendoo-studio/`](vendoo-studio/) | Anteroom: React + TypeScript frontend, FastAPI backend, SQLite storage, and Mac packaging. |
| [`vendoo-extension/`](vendoo-extension/) | Chrome MV3 extension, marketplace fill scripts, manual JSON popup, and page diagnostics. |
| [`skills/list-this/`](skills/list-this/) | Canonical listing rules, research workflow, JSON template, and marketplace dropdown references. |
| [`skills/box-scout/`](skills/box-scout/) | Wholesale clothing box research used by Anteroom's Sourcing workspace. |
| [`skills/measure-this/`](skills/measure-this/) | Garment measurement extraction from ruler photos, with two reads checked for agreement. |

## Chrome extension

Load the extension folder shown in **Settings → Connections** in `chrome://extensions/` (**Developer mode → Load unpacked**). The extension pairs automatically and runs in the background. Review listings and approve draft Sends in Anteroom; it owns photos, chat, progress, and diagnostics. See the [extension README](vendoo-extension/README.md) for setup and troubleshooting.

## Data and privacy

- Anteroom stores listings, chat, photos, and settings locally in SQLite and the app data folder. Automated snapshots support backup and recovery.
- AI requests send relevant photos and text to the provider you configure. Local storage does not make AI processing offline.
- API keys and ChatGPT tokens stay in macOS Keychain and are never included in SQLite, frontend responses, logs, or extension messages.
- The server binds to `127.0.0.1`. Optional remote access uses private Tailscale Serve for devices on your Tailnet.

## JSON contract

Anteroom, the listing skills, and the extension share a listing payload. This abbreviated example shows the structure; `ebay_specifics` keys use camelCase where shown. Category-dependent fields and allowed dropdown values come from the [canonical template](skills/list-this/references/vendoo_listing_template.md) and [listing skill](skills/list-this/SKILL.md).

<details>
<summary>Example listing JSON</summary>

```json
{
  "title": "Levi's M Blue Cotton Crew Neck T-Shirt",
  "description": "Blue cotton crew neck T-shirt...",
  "price": 25,
  "brand": "Levi's",
  "size": "M",
  "condition": "Good",
  "category_path": "Clothing, Shoes & Accessories > Men > Men's Clothing > Shirts > T-Shirts",
  "primaryColor": "Blue",
  "sku": "TEE-001",
  "quantity": 1,
  "weight_lb": 0,
  "weight_oz": 8,
  "package_dimensions_in": "13x10x3",
  "images": ["..."],
  "ebay_specifics": {
    "type": "T-Shirt",
    "department": "Men",
    "sizeType": "Regular",
    "size": "M",
    "brand": "Levi's",
    "fit": "Regular",
    "material": "Cotton",
    "pattern": "Solid",
    "style": "Basic",
    "features": "All Seasons",
    "neckline": "Crew Neck",
    "closure": "Pullover",
    "countryOfOrigin": "United States",
    "fabricType": "Jersey",
    "garmentCare": "Machine Wash",
    "handmade": "No",
    "personalize": "No",
    "vintage": "No",
    "occasion": "Casual",
    "season": "Summer",
    "unitQuantity": "1",
    "unitType": "Unit"
  },
  "depop_specifics": {},
  "etsy_specifics": {},
  "poshmark_specifics": {}
}
```

</details>

The complete template covers marketplace-specific fields beyond this example. Listing rules belong in `skills/`; app and extension code consume that contract.

## Build a zip to share

```bash
cd vendoo-studio
./scripts/setup.sh
source .venv/bin/activate
pip install -e ".[package]"
./scripts/package-macos-app.sh
```

That writes `vendoo-studio/release/Anteroom-macos.zip`. When you intend to publish a release, run `./scripts/publish-macos-release.sh` from `vendoo-studio/`. Pushes to `main` that touch the app, extension, or listing skills also build and update the rolling Mac release through [Studio macOS](.github/workflows/studio-macos.yml).

## Development

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for PR checks and [`AGENTS.md`](AGENTS.md) for product invariants.

CI on pull requests and `main` lints, tests, and builds Anteroom, lints and tests the backend, syntax-checks and unit-tests the extension, validates skill JSON, and scans the tree for secrets. See the [CI workflow](.github/workflows/ci.yml) for the full checks and the [product specification](VENDOO_STUDIO_SPEC.md) for architecture.
