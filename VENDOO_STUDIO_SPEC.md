# Anteroom Specification

## 1. Objective

Create a purpose-built local web application that lets the user:

1. Add product photos and optional notes.
2. Chat with Xiaomi MiMo about the item.
3. Generate a complete listing (see `docs/json-contract.md`).
4. Review and revise the listing conversationally.
5. Edit individual fields or raw JSON directly.
6. Validate the listing.
7. Click **List** on a marketplace and confirm.
8. List directly: eBay and Etsy through their APIs; Poshmark, Mercari, Depop through the extension, which writes through each marketplace's own signed-in web client.
9. Watch progress in the web app; Anteroom reads every write back from the marketplace.

**List** and **Update** each name one marketplace and carry `confirm`. Save
draft writes the marketplace's own draft and never publishes. Progress reports
the active stage and elapsed time, without estimated percentages. Validation
issues link to their fields. History compares revisions and restores listing
fields into a new revision; photos stay as they are, and restoration never
lists or updates anything.

The system is local-only, intended for one user, and never publishes without
the seller's confirmation. Direct actions run side by side, one per
marketplace, never alongside another automation job such as a delist or a page
inspection.

Generation prepares category ids and marketplace fields in the saved Anteroom
listing using read-only category/schema requests. List and Update reuse this
preparation. Generation does not create labels, upload photos or write to any
marketplace.

## 2. Product Decisions

### Confirmed

- Purpose-built interface, not a T3 Code or LibreChat fork.
- React and TypeScript frontend.
- FastAPI Python backend.
- SQLite persistence.
- Xiaomi MiMo API.
- `mimo-v2.5` for multimodal photo analysis.
- `mimo-v2.5-pro` for listing synthesis, chat, and revisions.
- macOS Keychain for API-key storage.
- eBay and Etsy are listed through their APIs; Poshmark, Mercari, Depop through the Chrome extension in the seller's signed-in Chrome.
- Draft saving only; never publish automatically.
- Publishing, updating and delisting happen only when the seller confirms that action: `POST /api/conversations/{id}/native-marketplaces/{marketplace}/list` or `.../update` (one marketplace, named in the path, body `confirm`), `POST /api/conversations/{id}/marketplace-listings/delist` (marketplaces named, body `confirm`), and eBay `POST /api/ebay/actions/confirm` (`confirm: true`). Nothing publishes everywhere by default, on a queue, on a retry, or as a side effect.
- The extension runs without its own interface. Anteroom owns review, confirmed actions, progress, and diagnostics.
- Direct List, Update and Save draft run side by side, one per marketplace, but never alongside another automation job such as a delist or a page inspection.

### Marketplace workspace

The sidebar and mobile navigation have one **Marketplaces** entry. Inside it,
eBay contains its seller workspace and a Marketing tab, because eBay's
marketing has a real API behind it. Etsy contains its connected shop
dashboard, orders, and listings. Poshmark, Depop, show a sales
dashboard only. No marketplace other than eBay has a Marketing tab. The eBay
sale calendar shows only eBay. Saving a local sale plan does not apply a
marketplace promotion. Inventory-wide discount suggestions live in **Analytics**.

Etsy reads shop details, paginated receipts, and listings through the existing
relay and Keychain connection. Its dashboard shows current shop counts and
paid order totals for the newest displayed page in the chosen UTC date range;
currencies stay separate, and totals include shipping and tax before refunds
and fees. Order and listing views support filters and pagination. Buyer names,
addresses, messages, and credentials are excluded from responses, and these
workspace reads are not persisted. Etsy account changes during a read discard
its result. This workspace has no publish, delist, refund, or shipment actions.

### Security Decision

The API key previously posted in chat must be rotated. It must not be committed, written to SQLite, included in logs, returned to the frontend, or sent to the extension. The replacement key will be entered through the application's local Settings page.

## 3. System Architecture

```text
Anteroom (http://127.0.0.1:4318)
├── React frontend
│   ├── Listing library
│   ├── Photo workspace
│   ├── MiMo chat
│   ├── Structured listing and JSON editors
│   ├── Validation
│   └── Automation progress
├── FastAPI backend
│   ├── MiMo provider
│   ├── Listing rules and schema
│   ├── Conversation and revision service
│   ├── Photo storage
│   ├── SQLite repository
│   ├── Keychain service
│   ├── Job orchestrator
│   └── Authenticated WebSocket server
└── Chrome extension (Bridge)
    ├── Reconnecting WebSocket client
    ├── Durable active-job state
    ├── Marketplace workers (Poshmark, Mercari, Depop)
    └── Field results and read-back
```

The frontend communicates with FastAPI through HTTP and Server-Sent Events. The extension connects to FastAPI through an authenticated loopback WebSocket. API keys never leave the backend process.

## 4. Proposed Repository Structure

Add a new application at the workspace root:

```text
vendoo-studio/
├── README.md
├── pyproject.toml
├── package.json
├── vite.config.ts
├── tsconfig.json
├── index.html
├── src/
│   ├── main.tsx
│   ├── app/
│   │   ├── App.tsx
│   │   ├── router.tsx
│   │   └── query-client.ts
│   ├── api/
│   │   ├── client.ts
│   │   ├── conversations.ts
│   │   ├── listings.ts
│   │   ├── jobs.ts
│   │   └── settings.ts
│   ├── components/
│   │   ├── AppShell.tsx
│   │   ├── PhotoTray.tsx
│   │   ├── ChatPanel.tsx
│   │   ├── ChatComposer.tsx
│   │   ├── ListingEditor.tsx
│   │   ├── JsonEditor.tsx
│   │   ├── ValidationPanel.tsx
│   │   ├── ExtensionStatus.tsx
│   │   └── JobProgress.tsx
│   ├── features/
│   │   ├── conversations/
│   │   ├── listings/
│   │   ├── automation/
│   │   └── settings/
│   ├── styles/
│   │   ├── tokens.css
│   │   └── app.css
│   └── types/api.ts
├── server/vendoo_studio/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── models/
│   │   ├── conversation.py
│   │   ├── listing.py
│   │   ├── job.py
│   │   └── protocol.py
│   ├── repositories/
│   │   ├── conversations.py
│   │   ├── listings.py
│   │   └── jobs.py
│   ├── services/
│   │   ├── keychain.py
│   │   ├── photos.py
│   │   ├── listing_rules.py
│   │   ├── listing_generator.py
│   │   ├── listing_revisions.py
│   │   └── extension_gateway.py
│   ├── providers/xiaomi_mimo.py
│   ├── providers/chatgpt_codex.py
│   ├── providers/cursor_agent.py
│   └── routes/
│       ├── conversations.py
│       ├── listings.py
│       ├── photos.py
│       ├── jobs.py
│       ├── settings.py
│       ├── events.py
│       └── extension.py
└── tests/
    ├── server/
    └── frontend/
```

Do not add Redis, MongoDB, Celery, Docker, or a cloud backend in version 1.

## 5. Frontend Specification

### Main Layout

Desktop uses three resizable columns:

```text
┌───────────────┬──────────────────────────┬──────────────────────┐
│ Listings      │ Photos and Chat          │ Listing              │
│ Search        │ Photo thumbnails         │ General              │
│ Recent items  │ Conversation             │ Marketplace tabs     │
│ Job status    │ Chat composer            │ JSON and validation  │
│ + New listing │                          │                      │
├───────────────┴──────────────────────────┴──────────────────────┤
│ Extension connected · Valid listing        [List on …]         │
└────────────────────────────────────────────────────────────────┘
```

Mobile and narrow screens use Photos, Chat, Listing, and Automation tabs.

### Visual Direction

- Purpose-built local listing workbench for one operator. Photos in, marketplace listing out, seller confirmation before anything is published.
- Dense enough for daily operational use.
- T3 Code-inspired information hierarchy, not an IDE clone.
- T3 Code stock dark shell from `pingdotgg/t3code` `themePalette.ts`: `#000000` sidebar, `#0a0a0a` workspace, `#111111` surfaces, `#191919` borders, and `#346bf1` accent. Settings → Appearance also offers a neutral light shell (inverted surfaces, same cobalt accent) and System (follow the OS). Preference is stored in `settings.json` under `ui.theme` (`dark` | `light` | `system`, default `dark`).
- The main workspace is inset by 8px inside a 12px rounded frame; navigation remains 244px wide.
- Sidebar, workspace, and inspector share the same 8px inset and aligned top/bottom frame.
- Column footers stay independent: sidebar keeps Settings and updates; inspector keeps the marketplace actions; only the chat column gets a T3-style composer.
- Chat chrome follows T3 Code: user right, assistant left, no bubble cards. Assistant and user text render as T3-style markdown (GFM tables, headings, lists, inline code). The composer is always visible as a rounded `#111111` pill on `#0a0a0a` with a circular `#346bf1` send control. Empty state keeps Generate Listing when photos exist.
- Browser preview is a bottom pane in the center column only when the seller opens **Browse draft**. **List** and **Update** run in the background with progress in the editor; they do not open the preview or switch the mobile pane. Collapse the preview when the seller closes it. Do not keep a permanent ~42% split.
- Browse draft makes the preview interactive, following T3 Code's in-app browser. **Use** forwards pointer, wheel, keyboard, and paste to the draft tab. **Pick**, **Box**, and **Pen** point Anteroom at fields, and **Missed fields** outlines empty listing fields. Picked fields show as chips in the chat composer. While the draft is open, chat messages go to the fix agent with those fields: it reads the live form, takes one action at a time (Anteroom's filler first, then clicks and typing), re-reads the form to verify, saves, and replies with the changed values and its steps. It changes only what the seller asked for, skips account settings, pauses when the seller presses, scrolls, or types in the pane, and stops after 24 steps. Input waits while Anteroom is filling. In the phone layout the pane sends its size and Chrome lays the draft out at that size with touch emulation, a finger on the pane is forwarded as touch (tap, drag to scroll), and **Keyboard** focuses a hidden text box so the phone's keyboard opens and its edits replay on the draft. Fills always run on the desktop layout; the phone layout returns when they finish.
- T3 Code blue marks primary actions, selection, and focus; marketplace colors remain sparing.
- System UI sans handles navigation, controls, and content while the T3 Code mono stack handles measurements, JSON, and technical status.
- Avoid generic dashboard cards and excessive rounded containers.
- Persistent status bar for extension and validation state.
- Fully usable desktop and mobile layouts.
- Settings → Connections → Remote access enables private Tailscale Serve to the loopback Anteroom (same process/data as the Mac UI). Copy the HTTPS link for phone browsers on the Tailnet. No native mobile app; Funnel stays off.

### Sourcing

- Compare a combined wholesale buy list with supplier-only alternatives using the same purchase budget. Open supplier carts for the seller to review; never purchase or publish automatically.
- Let sellers set shipping/preparation time and a target selling window. Pass dated window context and recorded sales from comparable calendar periods to sourcing research; show the window and fold historical context into optional details. Follow the canonical seasonal, evidence and selection rules in `skills/box-scout/SKILL.md`.
- Keep sourcing rules and calculations in `skills/box-scout/`. The Anteroom service supplies catalog data, model research and saved preferences; it does not maintain a second rule set.
- Show dated, linked, AI-reported sold examples, current asking-price comparisons, research recency, purchase costs, operating allowance, estimated profit, break-even sales and a lower-sales sensitivity result. Distinguish validated supplied data from independent source-page verification and measured demand from planning assumptions. Never claim a guaranteed sale or an uncalibrated confidence percentage.
- Show all checked box variants outside the selected buy list immediately, including when there are no recommendations; do not collapse them or hide same-theme variants. Distinguish available inventory from missing sold evidence and financial exclusions. Explain why each alternative did not qualify using the skill’s decisions. Keep cart links disabled while saved planning preferences differ from the snapshot, including changes made during an ongoing refresh.
- Store disposable sourcing snapshots and preferences in the local sourcing state file; rebuild obsolete snapshots without losing preferences. No inventory database schema change is needed for this workflow.

### Listing Library

- Create a new listing.
- Search by title, brand, SKU, or job ID.
- Filter by draft, active, sold, and failed status.
- Listings show `Draft`, `Active` or `Sold` from Anteroom's own marketplace listings and sales (`services/item_status.py`), on top of any imported Vendoo history, plus `Failed` for an action that broke. `In progress` shows during MiMo generation or refinement and `Listing` while a direct action runs.
- The label is read-only: it reports what the marketplaces say and offers no way to set it.
- A live listing with unsent edits keeps its `Active` label and wears an `unsent` tag; `Filters → Unsent edits` collects them. `Draft` holds only listings that are live nowhere.
- Sold listings settle onto the collapsed shelf, so the working list holds what is still sellable.
- Show last-updated time and each marketplace's listing link.

### Workspace

- Drag-and-drop multiple photos.
- File picker and folder picker where browser support allows it.
- Reorder and remove photos before approval.
- Image preview.
- Notes, cost, SKU, labels, measurements, and package-dimensions inputs.
- Item details use a full-width notes textarea, then bordered field cells: labels and package dimensions on one row; cost and garment measurements (top: pit to pit, length, sleeve; pants: waist, rise, inseam, leg opening; skirt: waist, hip, length) on the next. Narrow screens reduce the grids to two columns (then one). Poshmark original price is always 0 and is not a seller input. Condition and category are inferred from photos and notes, not seller inputs.
- Bulk upload's, Regenerate review's and a single listing's **Read measurements** suggests measurements from each item's yardstick photos with the `skills/measure-this` method (script crops, two independent reads by the photo provider, a value only when they agree within 1/4 in). It fills blank fields only, marks them as suggestions until the seller edits them or creates the drafts, names the measurements to take by hand, and saves nothing on its own. On a single listing it lists what it read and saves only when the seller presses **Fill in**.
- Poshmark and Mercari inspector tabs expose every field their automation steps fill, including shared listing values and marketplace-specific defaults.
- Marketplace tab strips include additional space below the active underline before the first field row.

### Chat

- Stream model responses.
- Include uploaded images in conversation context.
- Support generation and revision instructions.
- Display model changes as a field diff.
- Allow accepting, undoing, and restoring revisions.
- Infer brand, size, category, and other product facts from photos and the seller's initial notes. Flag remaining uncertainties in the description; never ask clarifying questions.
- Normalize generated categories to selectable General leaf paths before marketplace categories are chosen. Women's shirts and T-shirts use `Clothing, Shoes & Accessories > Women > Women's Clothing > Tops`; men's T-shirts use `Clothing, Shoes & Accessories > Men > Men's Clothing > Shirts > T-Shirts`. Treat `Shirts & Blouses` as a marketplace alias, not a General category.

### Listing Editor

Tabs:

- General
- eBay
- Poshmark
- Mercari
- Depop
- Etsy
- Raw JSON

Requirements:

- Friendly structured inputs.
- Required-field indicators and inline validation.
- Marketplace-specific field groups.
- Syntax-highlighted raw JSON editing.
- Valid JSON edits update the structured editor.
- Invalid JSON cannot overwrite the last valid listing.
- Revision history and restore action.

### List Controls

Each marketplace's **List** is enabled only when:

- The marketplace is connected (the extension for Poshmark, Mercari, Depop; the API sign-in for eBay and Etsy).
- The listing passes validation and the marketplace's pre-flight.
- Required photos are present.
- No other action on that marketplace, delist or page inspection is running.
- The seller confirms that one marketplace and the saved revision.

List and Update carry `confirm`; Save draft never publishes.

### Automation Progress

Each running action shows its marketplace, its current step (Saving the listing,
Publishing, Updating the live listing, Waiting for Chrome to confirm, Checking
the result) and elapsed time. Failures show the failed field, evidence, last
error, Check and Cancel, and the marketplace link when one exists.

## 6. Backend Specification

### Runtime

- Bind only to `127.0.0.1`.
- Default port: `4318`.
- Serve the built React frontend from FastAPI in production.
- Use Vite with an API proxy during development.
- Provide one documented startup command.

### Dependencies

Python:

- FastAPI
- Uvicorn
- Pydantic
- SQLAlchemy or SQLModel
- SQLite driver
- `keyring`
- `httpx`
- `python-multipart`
- Pillow

Frontend:

- React
- TypeScript
- Vite
- TanStack Query
- Accessible local components
- Monaco Editor only if its complexity remains justified

### API Endpoints

```text
GET    /api/health
GET    /api/status

GET    /api/settings/provider
PUT    /api/settings/provider
POST   /api/settings/provider/test
DELETE /api/settings/provider/key

GET    /api/settings/brave
PUT    /api/settings/brave
POST   /api/settings/brave/test
DELETE /api/settings/brave

GET    /api/settings/cursor
PUT    /api/settings/cursor
POST   /api/settings/cursor/test
DELETE /api/settings/cursor

POST   /api/conversations
GET    /api/conversations
GET    /api/conversations/{conversation_id}
POST   /api/conversations/{conversation_id}/messages

POST   /api/conversations/{conversation_id}/photos
DELETE /api/conversations/{conversation_id}/photos/{photo_id}
PATCH  /api/conversations/{conversation_id}/photos/order
GET    /api/photos/{photo_id}

GET    /api/conversations/{conversation_id}/listing
PUT    /api/conversations/{conversation_id}/listing
POST   /api/conversations/{conversation_id}/listing/validate
GET    /api/conversations/{conversation_id}/revisions
POST   /api/conversations/{conversation_id}/revisions/{revision_id}/restore

GET    /api/conversations/{conversation_id}/vendoo-api/fields

GET    /api/conversations/{conversation_id}/native-marketplaces
POST   /api/conversations/{conversation_id}/native-marketplaces/{marketplace}/draft
POST   /api/conversations/{conversation_id}/native-marketplaces/{marketplace}/list
POST   /api/conversations/{conversation_id}/native-marketplaces/{marketplace}/update
POST   /api/conversations/{conversation_id}/native-marketplaces/{marketplace}/check
POST   /api/conversations/{conversation_id}/native-marketplaces/{marketplace}/reset
POST   /api/conversations/{conversation_id}/native-marketplaces/{marketplace}/cancel
POST   /api/conversations/{conversation_id}/marketplace-listings/delist
POST   /api/ebay/actions/confirm

POST   /api/jobs/ensure-draft
GET    /api/jobs
GET    /api/jobs/{job_id}
POST   /api/jobs/{job_id}/cancel
GET    /api/jobs/{job_id}/events
GET    /api/jobs/{job_id}/photos/{photo_id}

WS     /api/extension/ws
```

Provider responses return only masked configuration:

```json
{
  "provider": "xiaomi-mimo",
  "configured": true,
  "maskedKey": "sk-...4K9P",
  "visionModel": "mimo-v2.5",
  "listingModel": "mimo-v2.5-pro"
}
```

## 7. Listing Generation Pipeline

### Stage 1: Photo Analysis

Use `mimo-v2.5` to produce structured evidence rather than listing copy.

Input:

- Ordered product photos.
- User notes.
- Extraction instructions derived from `skills/list-this/SKILL.md`.

Output example:

```json
{
  "brand": {
    "value": "Levi's",
    "confidence": 0.98,
    "evidencePhotoIds": ["photo-2"]
  },
  "size": {
    "value": "M",
    "source": "tag",
    "confidence": 0.96
  },
  "condition": {
    "value": "Pre-Owned - Good",
    "visibleFlaws": ["Small mark on left sleeve"]
  },
  "measurements": [],
  "uncertainties": []
}
```

The model must separate visible facts from inference, identify tag evidence, flag disagreements, avoid invented details, and use measurement-derived sizing only under the existing skill rules.

### Stage 2: Listing Synthesis

Use `mimo-v2.5-pro` with:

- Structured photo evidence.
- User notes.
- Current listing rules.
- Listing JSON schema (`docs/json-contract.md`).
- Marketplace value constraints.
- Conversation instructions.

Generation also supplies a bounded, read-only seller-history context after the
general category is resolved: current listing facets matched by brand, full
category and known condition, recent recorded sale prices and durations, and
similar active inventory. It excludes the current item, asking-price substitutes
for sales, missing or invalid sale dates, and unrelated notes or photos. The
canonical `skills/list-this/SKILL.md` owns how that evidence affects pricing and
historical vocabulary. Small samples remain weak examples, and historical item
facts never replace evidence about the new item. Photo analysis preserves its
evidence sources, model confidence and uncertainty details; discovered field
requirements and exact allowed options reach the initial generation prompt.

Generation receives deliberate field edits recorded before automatic
normalization, separately from revisions. Current-item corrections retain
explicit clears; related title and description edits are bounded examples and
never transfer another item's facts or replace canonical/customized formulas.
These records survive regeneration and are removed when the listing is deleted.
Old revisions are not backfilled as deliberate edits.

A dated imported Vendoo sale froze its remote listing at first observation,
and that snapshot is kept. The record is explicitly first-observed-sold text,
not verified text at the sale instant. Generation matches historical facets
against that snapshot, so later listing edits cannot rewrite historical
examples. Legacy listings without a snapshot remain labeled as current text with
unknown at-sale provenance.

The output contains a user-facing response, complete proposed listing, uncertainties, and validation warnings.

### Stage 3: Revision

Chat revisions produce a patch rather than silently replacing the listing:

```json
{
  "message": "I shortened the title and changed the price.",
  "listingPatch": [
    {"op": "replace", "path": "/title", "value": "..."},
    {"op": "replace", "path": "/price", "value": 45}
  ]
}
```

The server applies the patch, validates the complete result, stores a revision, returns a diff, and preserves the previous valid revision.

### Listing Rules Source

The editorial policy remains in:

- `skills/list-this/SKILL.md`
- `skills/list-this/references/vendoo_listing_template.md`

`listing_rules.py` loads or packages the relevant rules. The Pydantic schema is the canonical machine-readable contract; the skill remains the canonical editorial policy.

## 8. Listing Schema

Define the complete schema in `vendoo-studio/server/vendoo_studio/models/listing.py` and generate frontend TypeScript types from OpenAPI.

```json
{
  "title": "...",
  "description": "...",
  "price": 45,
  "cost": 8,
  "quantity": 1,
  "brand": "...",
  "condition": "...",
  "primaryColor": "...",
  "secondaryColor": "...",
  "category_path": "...",
  "size": "...",
  "sizeType": "...",
  "tags": [],
  "labels": [],
  "weight_lb": 0,
  "weight_oz": 8,
  "package_dimensions_in": "13x10x3",
  "internal_notes": "...",
  "ebay_specifics": {},
  "poshmark_specifics": {},
  "mercari_specifics": {},
  "depop_specifics": {},
  "etsy_specifics": {}
}
```

Validation levels:

- **Error:** blocks List and Update.
- **Warning:** requires acknowledgement.
- **Information:** quality recommendation.

Blockers include missing core listing fields, missing required photos, invalid marketplace values, unresolved brand/category conflicts, malformed dimensions, and invalid JSON.

## 9. Persistence Model

SQLite tables:

### `conversations`

- ID, title, status, notes, and timestamps.

### `messages`

- ID, conversation ID, role, text, provider/model, and timestamp.

### `photos`

- ID, conversation ID, original/stored filename, MIME type, size, display order, SHA-256 checksum, and timestamp.

### `listings`

- Conversation ID, current revision ID, validation status, and timestamp.

### `listing_revisions`

- ID, conversation ID, listing JSON, source, parent revision ID, and timestamp.

### `jobs`

- ID, conversation ID, approved revision ID, immutable listing snapshot, status, current step, attempts, last error, and timestamps.

### `job_events`

- ID, job ID, sequence number, type, step, payload, and timestamp.

API keys must not appear in any table.

## 10. Extension Protocol

### Connection

The background service worker connects to:

```text
ws://127.0.0.1:4318/api/extension/ws
```

- The backend generates a local pairing token.
- The extension stores it in `chrome.storage.local`.
- The server permits one active extension connection.
- Heartbeats keep the MV3 worker active while connected.
- Reconnection uses bounded exponential backoff.

### Message Envelope

```json
{
  "version": 1,
  "type": "job.start",
  "messageId": "uuid",
  "jobId": "uuid",
  "sentAt": "ISO-8601",
  "payload": {}
}
```

Server-to-extension messages:

- `connection.accepted`
- `job.start`
- `job.retry`
- `job.cancel`
- `extension.reload`
- `browser.open`, `browser.close`: open or release the interactive draft tab for a job
- `browser.input`: fire-and-forget pointer, touch, wheel, key, and text events with coordinates as viewport ratios
- `browser.viewport`: fire-and-forget phone layout for the draft tab (`width`, `height`, `scale` in CSS pixels); dropped whenever a fill takes the tab
- `browser.pick`, `browser.snapshot`: read the field under a point, or every visible listing field with value, options, and rectangle
- `browser.act`: agent `click` (by selector, point, or visible text), `type` (focused field only), `press`, `scroll`, and `wait`; the extension refuses publish, list, delist, and delete controls and any action off the open draft
- `ping`

After an Anteroom update, the backend copies the bundled extension over the installed copy and sends `extension.reload`. The service worker calls `chrome.runtime.reload()`, then refreshes open marketplace tabs so content scripts pick up the new code.

Extension-to-server messages:

- `extension.ready`
- `job.accepted`
- `job.progress`
- `job.step_completed`
- `job.step_failed`
- `job.cancelled`
- `job.completed`
- `job.preview_frame`: JPEG frame with URL and CSS viewport size; accepted for finished jobs so browsing a saved draft stays live
- `browser.result`: reply to `browser.*` requests, keyed by `request_id`, with the current controller (`human`, `agent`, `busy`, `none`)
- `pong`

### Idempotency

- Job IDs are unique.
- Extension persists active and completed job IDs.
- Repeated starts return current state instead of creating another listing.
- Retry resumes at an explicit step.
- Approved listing snapshots cannot change during execution.

## 11. Direct Action States

A direct action runs as one job whose step names the marketplace and the
action: `native_<marketplace>_<step>`, where the step is `save_draft`,
`publish`, `list`, `update` or `check`. Its record in `marketplace_listings`
moves through:

```text
not_saved
draft
saving
publishing
updating
published / pending_review   (live)
unknown                      (no answer came back; Check decides)
```

Field states:

```text
not_started
attempted_unverified
visibly_committed
persisted_after_save
audited_complete
blocked_with_evidence
```

No field is complete when values were merely sent. Completion requires the
value to read back from the marketplace.

## 12. Extension

### `vendoo-extension/manifest.json`

- Loopback host access for `http://127.0.0.1:4318/*` and the local WebSocket.
- Host permissions for the marketplaces it lists on: Poshmark, Mercari, Depop, and eBay pages it reads.
- Do not add `<all_urls>`.

### `vendoo-extension/background.js`

- WebSocket lifecycle and pairing.
- Active-job persistence and idempotency.
- Loads the marketplace worker modules in `background/` and routes messages to them and to the content scripts.
- Timeouts, progress forwarding, and restart recovery.

### Marketplace workers and content scripts

- Poshmark, Mercari, Depop listings are written through each marketplace's own signed-in web client, in the seller's Chrome. Authentication stays in Chrome; only listing data and results cross the bridge.
- Fetch authorized job photos from the backend and preserve their order.
- Fill SKU only when explicitly supplied.
- Return structured field results and read the saved listing back.
- Never publish, delist or delete outside a seller-confirmed action.

### Categories and schemas

- After photo analysis, choose a real terminal category independently in each selected marketplace tree via local semantic catalog search (Semble) over verified `category_tree_nodes` leaves, inferring the best-supported leaf from photos and notes when evidence is thin. Preserve `category_path` and `marketplace_categories` through generation. Infer supportable unknown fields from photos and initial notes; leave unsupported facts empty and flag them — never ask the seller clarifying questions.
- `/api/catalog/categories` reports per-marketplace tree completeness; `/api/catalog/schema` exposes observed category fields; `/api/catalog/search` semantically searches local category leaves, observed schemas, skill dropdown options, listing-rule chunks, and extension fill helpers. Generation retrieves relevant skill chunks; field repair enriches gaps with catalog options; fill-log reports attach related fill helpers for failed fields. Searchable dropdown options remain partial unless exhaustiveness is established.
- Direct forms come from each marketplace's own catalog (`marketplace_schemas.py`, `chrome_catalog.py`); the eBay and Etsy editor forms come from `GET /api/conversations/{id}/vendoo-api/fields`.

Structured result example:

```json
{
  "ok": false,
  "step": "native_mercari_save_draft",
  "fields": {
    "brand": {
      "state": "audited_complete",
      "expected": "Levi's",
      "observed": "Levi's"
    },
    "size": {
      "state": "attempted_unverified",
      "expected": "M",
      "observed": null
    }
  },
  "error": "Mercari's draft read back without a size"
}
```

### Extension interface

- No toolbar popup, on-page debug panel, field highlighting, or manual JSON controls.
- Pair automatically with the local Anteroom server and reconnect in the background.
- Show connection status, active jobs, fill results, and errors in Anteroom.

### Documentation

Keep current:

- `vendoo-extension/README.md`
- Root `README.md`

## 13. Read-back Requirements

A write succeeds only when the marketplace's own copy is read back and matches.
Clicking a button or getting a reply does not prove persistence.

- Each List, Update and Save draft reads the listing or draft back from the marketplace.
- A reply that never came leaves the record `unknown`. Check reads the marketplace (never writes) and runs by itself after a lost reply, on startup and when Chrome reconnects.
- Nothing retries a write on its own. Reset is the seller saying they looked on the marketplace themselves, and requires `confirm`.
- A failed, empty or partial read-back leaves the record unconfirmed.

## 14. Photo Handling

### Storage

- Copy uploads into an app-managed directory.
- Generate safe internal filenames.
- Store original names as metadata.
- Verify MIME type and actual format.
- Reject unsupported or oversized files.
- Never expose arbitrary filesystem paths.

### Model Input

- Preserve original uploads.
- Generate resized inference copies if needed.
- Correct EXIF orientation.
- Send ordered photos to `mimo-v2.5`.

### Extension Transfer

- Create job-scoped signed photo URLs.
- Extension downloads bytes over loopback HTTP and uploads them to the marketplace in seller order.
- Read the photo count and order back before continuing.

Use a Native Messaging host only if browser-based file assignment is tested and proven incompatible.

## 15. Security Requirements

- Bind only to `127.0.0.1`.
- Optional private Tailscale Serve (Settings → Connections → Remote access) may proxy HTTPS from the Tailnet to that loopback port. Do not enable Tailscale Funnel. Do not bind Anteroom to `0.0.0.0`.
- Store MiMo and Cursor API keys in macOS Keychain.
- Cursor Listing AI uses the official `cursor-sdk` local agent against an empty scratch workspace under Anteroom user data (never the product repo). Model default: `composer-2.5`.
- Redact authorization headers and secrets from logs.
- Never send the key to React or the extension.
- Never store the key in SQLite or browser storage.
- Use an explicit CORS allowlist.
- Validate HTTP and WebSocket origins.
- Require an extension pairing token.
- Use signed, expiring photo URLs.
- Restrict files to registered job assets.
- Reject path traversal.
- Limit upload size and count.
- Escape model output in the UI.
- Validate model JSON before persistence.
- Do not expose a publish command in protocol version 1.

## 16. Logging and Diagnostics

Backend logs may include request ID, conversation ID, job ID, step, duration, provider/model, token usage, and redacted errors.

Extension logs may include connection state, job ID, step transitions, retries, read-back failures, and sanitized marketplace URLs.

Do not log API keys, pairing tokens, authorization URLs, image bytes, base64 payloads, or full listing content by default.

Each failed job preserves a structured event history viewable in Anteroom.

## 17. Testing Strategy

### Backend Unit Tests

- Valid and invalid listing schemas.
- MiMo response parsing.
- Patch application and rollback.
- Mocked Keychain operations.
- Photo path traversal rejection.
- Signed-photo expiration.
- Job transition validation.
- Duplicate-job idempotency.

### Frontend Tests

- Photo upload, reorder, and removal.
- Streaming chat.
- Listing diff display.
- Structured editor and JSON synchronization.
- Invalid JSON preserving the last valid listing.
- Send-button enablement.
- Job progress and retry states.
- Full API key never displayed.

### Extension Tests

- Protocol message validation.
- Job transitions and retries.
- Idempotency.
- Field normalization and comparison.
- Save/audit aggregation.
- Tab creation and content-script readiness with mocked Chrome APIs.
- Worker restart recovery.

### End-to-End Tests

Mocked flow:

- Fake MiMo API.
- Fake extension WebSocket client.
- Generate, approve, dispatch, report progress, and complete.

Direct listing smoke test (a private draft on a disposable item):

1. Save draft on one marketplace.
2. Confirm the marketplace's draft reads back with the expected photos and fields.
3. Check the record without writing.
4. Confirm no publish action occurred.

## 18. Implementation Milestones

### Milestone 1: Contracts and Foundation

- Create `vendoo-studio/`.
- Add FastAPI, React, SQLite, health endpoints, listing schema, and generated frontend types.
- Verify one command starts the app and data survives restart.

### Milestone 2: Settings and MiMo

- Add Keychain settings, MiMo connection test, multimodal analysis, Pro synthesis, and streaming chat.
- Verify photos produce evidence and Pro produces valid listing JSON.

### Milestone 3: Listing Workspace

- Add library, photo tray, chat, structured editor, JSON editor, validation, and revision history.
- Verify generation, revisions, direct edits, undo, and responsive behavior.

### Milestone 4: Extension Connection

- Add pairing, authenticated WebSocket, heartbeat, reconnect, durable jobs, and acknowledgements.
- Verify online status, duplicate prevention, and restart recovery.

### Milestone 5: Direct Listing

- Add List, Update and Save draft per marketplace: eBay and Etsy through their APIs; Poshmark, Mercari, Depop through the extension.
- Verify each action reads the listing back and records its state, and a lost reply leaves the record `unknown` until Check reads it.

### Milestone 6: Recovery and Polish

- Add Check, Reset, cancellation, event history, listing links, diagnostics and documentation.
- Verify a failed or lost action never creates a second listing.

## 19. Relevant Existing Files

- `docs/json-contract.md`: the listing JSON shared by skills, Anteroom and the extension.
- `docs/direct-form-parity.md`: each marketplace's form, sharing and offer support, with sources.
- `skills/list-this/SKILL.md`: canonical listing-generation policy.
- `skills/list-this/references/vendoo_listing_template.md`: JSON and marketplace guidance.
- `vendoo-studio/server/vendoo_studio/services/native_marketplaces.py`: direct List, Update, Save draft, Check and Reset.
- `vendoo-extension/manifest.json`: permissions and extension metadata.
- `vendoo-extension/background.js`: Anteroom connection and job orchestration.
- `vendoo-extension/sample-listing.json`: shared validation and automation fixture.
- `vendoo-extension/README.md`: setup and workflow documentation.

## 20. Non-Goals for Version 1

- Automatic publication.
- Multi-user accounts.
- Cloud hosting.
- Native mobile application.
- Distributed workers.
- LibreChat or T3 Code integration.
- Native Messaging unless browser-based photo upload fails.
- A generalized provider framework beyond the small MiMo boundary.

## 21. Completion Criteria

Version 1 is complete when the user can:

1. Start the local application.
2. Configure a rotated Xiaomi MiMo key securely.
3. Upload and reorder product photos.
4. Generate a listing using MiMo V2.5 and V2.5 Pro.
5. Chat to revise the listing.
6. Edit structured fields or raw JSON.
7. Pass validation.
8. See that the extension is connected.
9. Confirm **List** on one marketplace.
10. Have the listing written and read back on that marketplace.
11. See live progress and actionable failures.
12. Check a lost or failed action without creating a duplicate listing.
13. Open the listing on the marketplace.
14. Confirm that nothing was published without the seller's confirmation.


## Sale calendar in Marketing

The calendar in eBay's Marketing tab stores local promotion plans and records for eBay.
A plan covers every active listing on its marketplace, because the seller runs
sales shop-wide, and two sales on one marketplace cannot overlap. Effective fees
and seller-paid shipping start from that marketplace's sales over the last year.
The suggested discount is the deepest 5% step, up to 30%, at which every listing
with a cost still clears the seller's minimum profit; the plan lists the
listings that fall below it and those with no cost.
Saving, editing, recording, or cancelling an event never writes to a
marketplace. The seller runs the sale separately and marks it as run.

Weekday suggestions scan every run of one to six consecutive weekdays over up
to 52 complete weeks of imported sales (Kulldorff's scan statistic), so the
window length comes from the data. A seeded Monte Carlo test against evenly
spread sales says whether the peak is stronger than chance or may be chance.
Picking days requires at least 20 dated sales across 8 complete weeks, a unique
strongest window, and a peak that beats chance; otherwise the suggestion is a
7-day sale starting today, which covers every weekday and keeps promotions from
skewing the pattern. Suggestions are experiments, not predictions of discount
lift. Records show sales and recorded profit during the event and a
comparison with matching weekdays before it, plus marketplace sales over up
to 14 days after it. Overlapping recorded promotions
or insufficient history suppress that comparison. Missing prices, costs, and
fees are disclosed. Recent sales get event labels only from events marked as
run, in the event time zone and selected item scope. Marketplace-wide historical
records have no item scope. A new sale refreshes the calendar results.

Earlier multi-marketplace event records migrate to one record per marketplace;
events that covered all marketplaces keep that scope. Their dates are retained,
and UTC is explicitly flagged as assumed because the earlier schema stored no
time zone. Sellers can correct the time zone on historical records.

## Ad spend

Studio no longer has a screen for recording Poshmark Promoted Closet or Etsy
Ads spend. Entries recorded earlier stay in the database and still count in
Analytics and the business assistant: each entry's spend, clicks, orders, and
attributed revenue are spread evenly over its days, and profit after ads is the
period's profit on sales with a recorded cost, less all ad spend in the period.
Figures left blank stay unknown and never read as zero.

## Business assistant

The Assistant page is a chat about the business as a whole, apart from any
listing's chat, and it keeps one running conversation of its own. On every
question Anteroom builds a fresh brief from its local data and sends it with the
question and the recent turns to the seller's listing provider:

- every listing as one row: id, status, brand, category, size, condition,
  colour, labels, SKU, the marketplaces it is listed on, asking price, cost,
  sold price, fees, profit, where it sold, dates, days, and box;
- the Analytics totals for each period;
- each change to a listing's asking price between saved revisions;
- the boxes bought and what each returned, the current Sourcing buy list, ad
  spend, sale events, and sourcing checks;
- the seller's own note about the business, edited under the composer and kept
  in `settings.json`.

Nothing in the brief is stored with the conversation. Listing rows have a size
budget. Past it the oldest sales and the drafts are left out and the brief says
how many; the period totals still count them.

**Lookups.** The providers have no tool calling, so the assistant asks for more
by replying with nothing but one fenced `sql` block. Anteroom runs that `SELECT`
and replies with the rows, up to six times per question, before the answer
streams; the seller sees only a "Looking up…" status. The query runs against a
throwaway in-memory SQLite copy built for that question, holding the same rows
plus each listing's description and full listing JSON, with tables `listings`,
`price_changes`, `boxes`, `ad_spend`, and `sale_events`. An authorizer allows
only reads of that copy (no `ATTACH`, `PRAGMA`, or writes), each query has a
time limit, and results are capped. Anteroom's real database is never handed to a
query.

**Answers outlive their connection.** An answer runs as a background run in
the same registry listing generation uses, not inside the request that asked.
A phone that locks or drops off the network mid-answer loses only its stream:
the page retries, and `POST /api/assistant/messages/resume` replays the answer
from its start. Opening the page, or returning to the tab, reattaches the same
way. One answer runs at a time; `POST /api/assistant/messages/stop` ends it and
keeps what was written.

**Links.** An answer names a listing as `[title](#listing-<id>)`; clicking it
opens that listing in Anteroom.

**Changes.** The assistant can change listings, and only by proposing. When the
seller asks for a change, or agrees to one it suggested, the answer ends with
one fenced `changes` block: a JSON array of `{"listing": id, "set": {...}}`.
`set` may hold `asking`, `cost`, `title`, `description`, `brand`, `size`, `sku`,
`labels` and `box`; condition, colour, category and other marketplace fields
stay with the listing's own chat. The block never reaches the seller as text.
Anteroom checks it against the listings as they are (unknown listings, fields it
may not set, and values a listing already has are dropped; at most 300
listings), and stores what is left with the answer in
`assistant_messages.changes` as a proposal showing each value before and after.
The proposal is worked out by running the listing editor's own save on a copy,
so it shows everything saving would move, not only what the model named: a new
size appears with each marketplace form's size and the title that follow it.

**Sizing.** A listing has one general size, and saving makes every marketplace
form and the title carry it. `services/size_audit.py` flags an unsold listing
when a save would rewrite a form's size or the title, when it has form sizes
but no general size, or when the latest photo analysis read a different size.
The brief lists those listings with the general size, each form's size, and the
size the photos showed and where it was read; the lookup copy carries the same
as `form_sizes`, `photo_size` and `size_issue` on every listing. The assistant
fixes sizing by proposing the right `size`, and leaves out a listing whose
evidence does not settle it.

The page draws the proposal as a card under the answer with **Apply** and
**Dismiss**. Nothing is written until Apply
(`POST /api/assistant/messages/{id}/changes/apply`), which saves each listing
through the listing editor's own save as a new revision with source
`assistant`, so the listing's revision history can restore it. A listing that
is gone, or that Anteroom is generating or sending, is skipped and says why; the
rest apply. A proposal is settled once, and later questions tell the model
whether it was applied or dismissed.

Applied changes are saved in Anteroom only. The Assistant never sends, lists,
delists or publishes, and never calls a marketplace: a listing already live
keeps its old values there until the seller updates it.
question still writes nothing but the conversation and the seller's note.
Photos and secrets are in neither the brief nor the copy.

## Direct marketplace transition scope

Anteroom owns inventory and connects directly to marketplaces. Inventory
imported from Vendoo earlier keeps its history (statuses, labels, sale dates)
as read-only data; nothing new depends on Vendoo.
Extend the direct connections, listing records and sales reconciliation already
implemented here; do not introduce parallel identity or connection systems.

The agreed scope includes direct account connections and connection health;
listing links, imports and duplicate reconciliation; sales recording, fees,
quantities, partial sales and cancellations; direct drafts and updates; bulk
field and label editing, copying and seller-approved list/delist/relist; saved
seller templates and CSV inventory/sales exports; photo editing, background
removal and listing videos; and offers, sharing, refreshing, bumping and broader
marketplace coverage. Deliver working increments on the existing implementation.
Listing rules remain canonical in `skills/`.

Public URLs identify listings but do not verify ownership or current status.
Direct actions and sales matching use the same validated reference. Seller
linking rejects references already attached to another item, and an ambiguous
sale remains unmatched rather than being assigned to the first duplicate found.
Linking and reconciliation are local operations and do not publish or delist.

Bulk List, Delist and Relist start from a seller selection. The seller names
the action and each marketplace, reviews what every item would do, and confirms
once. Anteroom then sends one request per item, in order; each carries the
reviewed marketplaces, revision and a review token that must still match the
item, its photos and its marketplace links. Nothing is stored to run later, a
failed item is not retried, List skips a marketplace where the item is already
live, and a marketplace where it sold is never touched.

Sharing and offers to likers follow the same rule. The seller selects items,
names the marketplace and the action, and reviews each item; an offer needs the
seller's floor for that listing and never goes below it. Each reviewed item is
sent once, holding the automation slot, and journaled before Chrome is asked; a
lost reply is unknown and is checked, never resent. Nothing runs on a timer.
Which marketplaces support which tool, with sources, is recorded in
`docs/direct-form-parity.md`.
