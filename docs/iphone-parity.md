# Anteroom iPhone parity tracker

This is the feature checklist for the full native iPhone port. A successful
build or TestFlight installation is not feature completion. The iPhone uses
the shared hosted backend; Python, Keychain, the desktop filesystem and Chrome
automation do not move into the iOS process.

Sources: `src/components/workspaceCrumbs.ts`, `src/app/runtime.tsx`,
`src/components/settingsNav.ts`, `server/vendoo_studio/hosted.py` and the
routers it mounts (paths are relative to `vendoo-studio/`). The native source
is `anteroom-ios/Sources/`. Backend availability below describes code, not a
verified production deployment. Recheck `/api/runtime` on the deployed server.

The built-desktop visual/code reference is
[`desktop-ui-reference.md`](desktop-ui-reference.md). Use its source map,
observed screen hierarchy and acceptance criteria before further UI changes.
It records where the native form still differs from the desktop workflow.

## Completion rule

For every row, verify its seller-scoped backend contract, native interface,
loading/empty/error handling, and end-to-end behavior on an iPhone. Review
VoiceOver labels, accessibility text sizes, keyboard handling, safe areas,
light/dark appearance and interruption recovery for each relevant screen.
Do not add a destination until its workflow works. Native tabs represent
primary destinations; creation and confirmations are modal tasks. Preserve
Anteroom's terminology and identity while following iOS navigation conventions.

## Feature inventory

| Area / workflow | Hosted backend | Native iPhone | Verification / remaining work |
| --- | --- | --- | --- |
| Account: email-link sign-in, callback, session restore, sign-out | Existing session-authenticated API | Implemented | Sign-in tested by seller; session expiry and account-switch isolation need review |
| Inventory: search and creation | `/api/conversations` | Implemented | Title/SKU search over every listing; creation uses the shared conversation contract |
| Inventory: all listing states, shelves, status/marketplace/label filters, sorting | `/api/conversations` shared summaries | Implemented in current work | All/Draft/Active/Sold/Failed tabs with counts, settled listings hidden outside Sold and search, marketplace and Not listed filters, the desktop sorts that hosted rows can answer. Hosted rows carry no Vendoo labels, listing dates or relist state, so the label, stale, relist and unsent filters and the Stalest/Freshest/Date Sold sorts stay with Vendoo import |
| Inventory: photo covers, price, SKU, quantity, marketplace status | Shared summaries and private thumbnails | Implemented in current work | Cover thumbnails from the summary, colored lifecycle word, price, SKU and live marketplaces; rename and delete as swipe and context actions |
| Inventory: Suggestions shelf | `/api/suggestions` | Implemented in current work | Collapsible shelf with the desktop's labels and stale captions, three then Show all, opening the same editor, and Mark down N stale listings |
| Inventory: bulk edit, copy, delete, price reductions and generation | Hosted bulk routes mounted; generation capability conditional | Implemented in current work | Select mode with Select all. Edit fields (the desktop's fields, choice lists and label modes), Copy items (name, SKU, quantity, photos, cost per item; one at a time with lost-reply recovery), whole-dollar Markdowns from the seller's sales, and Delete each review every item before applying and report per-item outcomes. Copy listing is also a row action. Regenerate rewrites one listing at a time with Regenerate's input-keeping wipe, waits for each to finish on the server, and can stop after the current item |
| Seller inputs and garment measurements | Existing draft notes save; structured desktop ItemDetails contract | Native implementation in current work | Preservation tests and iOS SDK type check pass; hosted sandbox save/reopen and screenshots run in CI; preference defaults/recent labels remain |
| General listing fields, notes and optimistic save conflicts | `/api/conversations/{id}/listing` revision contract for every listing state | Implemented in current work | Edits are written into the loaded listing field by field, so marketplace specifics, categories and tags keep their exact values (unit tested); revision conflicts prompt a reload. New photos can be added while a listing is a draft, as on the hosted web app |
| Photos: camera/library upload, view and delete | Private seller-scoped photo routes | Implemented | Device review of permissions, failures, delete confirmation and upload interruption |
| Photos: order, crop/rotate, edits, background removal and marketplace media | Hosted order/edit/media routes exist; inspect each advanced action | Partly implemented in current work | Full-size paged viewer with pinch zoom; rotate left/right, Adjust (straighten, exposure, brightness, contrast) with a server-rendered preview, revert to original, delete; Arrange sheet sets order and cover. Crop, background removal and per-marketplace photo sets remain |
| Listing generation | Durable hosted tasks, conditional on worker/provider | Implemented | Generate and Regenerate (keeps photos, measurements, flaws, cost, labels and notes) from the listing assistant. Needs configured-account end-to-end test, Stop and disconnect/reconnect checks |
| Listing assistant: saved transcript and text requests | Hosted messages and durable tasks | Implemented | Needs integration test; live token rendering and automatic foreground reconnection missing |
| Listing assistant: image attachments and measurements | Hosted attachments mounted; audit measurements service | Missing | Native attachment input and corresponding contract verification |
| Listing history: list, preview, conflict-safe restore | Shared revisions and restore routes | Implemented in current work | Compiler/model checks and device restore/conflict verification pending |
| General dropdowns: Condition, Size, Primary/Secondary Color | `/api/catalog/dropdown-options` Vendoo reference lists | Implemented in current work | Searchable native sheet with Clear; a saved value outside the list stays visible; falls back to text when the lists are unavailable. Category stays free text with the desktop's required marker and help, as on the desktop General tab |
| Marketplace form editors, category resolution, dropdowns and variations | Hosted native forms for the marketplaces `/api/runtime` lists (Poshmark, Depop) | Implemented in current work for hosted marketplaces | A form selector switches General and each hosted marketplace. Fields render from the server's native form only, in its sections, order and side-by-side rows: text, description, number, toggles, segmented, single and multi choice with stored values and limits, category paths, notes, read-only rows, drop time, Poshmark sizes (Single/Multi Item, US/AU/EU/UK) with live Your Earnings, and each marketplace's photo pick and order. The desktop's reveal rules and dependent clears apply; a new category's own rows load after Save. eBay, Mercari, Etsy and Sellwild forms need the browser-backed readers the hosted API does not have yet |
| Marketplace photos/video and validation blockers | Partial shared listing/media contracts | Missing | Inspect each platform's supported contract; show actionable review outcomes |
| Marketplace connections, account linking and import | Desktop connections; hosted support incomplete | Missing | Public seller onboarding and secure cross-device browser connection required |
| Save marketplace drafts / approved Send | Hosted command contracts and cross-device delivery incomplete | Missing | Per-seller approval, concurrency and result tracking; automation must stop at draft |
| Explicit List, Update and Delist | Desktop/API capabilities vary by marketplace; hosted command port incomplete | Missing | Name marketplaces explicitly; publishing/delisting require seller intent and confirmation; never queue automatic publishing |
| Listing templates, copies, similar items and suggestions | Shared routers mounted | Missing | Native application/preview of saved templates and suggestions |
| Standalone assistant threads, business context and memory | Desktop assistant services; separate from listing chat | Missing | Audit seller-scoped hosted services before creating the destination |
| Analytics: inventory/sales metrics and charts | Analytics router mounted | Inventory analytics implemented in current work | Inventory analytics view with every desktop period and Custom dates; missing-data review, the six stats with comparisons and notes, Revenue/Sales/Profit chart with losses below zero, top-six Marketplaces/Categories/Brands/Labels by Revenue/Profit/Sales, Sales by price, Inventory today, inventory by age, oldest listings and recent sales, each opening the listing. Sales records, discount suggestions and the Etsy check remain |
| Sales ledger, calendar, expenses and ad spend | Sales/calendar hosted; other service coverage needs audit | Sales records implemented in current work | Analytics opens on Sales records, as on the desktop: UTC date range, marketplace filter, per-currency summary, order lines with Open item and Record amounts (revision-checked save, Reload record), paging, and inventory/sales CSV export through the share sheet. Sale calendar, expenses and ad spend remain |
| Sourcing: boxes, inventory locations, purchase research/scout | Boxes hosted; full sourcing/scout needs audit | Missing | Separate record management from research/provider work |
| Automations: promotion schedules, repricing, relisting and run outcomes | Desktop services; hosted support incomplete | Missing | Seller-scoped execution and explicit approval boundaries; no automatic publishing |
| Usage: provider/token/cost reporting | Desktop routes; hosted coverage needs audit | Missing | Native usage views and server-side accounting contract |
| Settings / General: account and version | Session and bundle | Implemented | Seller tested basic app; full account flows still need review |
| Settings / General: appearance, naming and other preferences | Shared `/api/settings/ui` and `/api/changelog` | Implemented in current work | Settings opens on the hosted sections (General, Listings, Providers). General has Theme and Listing names (inventory rows follow it), Version and What's new. Mac-only rows (setup guide, previous versions) stay on the Mac |
| Settings / Listings: templates, shipping, inventory, formulas, hidden fields and defaults | Hosted settings routes | Implemented in current work except templates, label colors and similar items | Where you list (All/None, Direct and not-sent badges), Stale after, Shipping defaults (package dimensions, ships-from ZIP), always-hidden fields with Restore, and listing formulas that save empty when equal to the built-in. Saved templates, label colors and Similar items remain |
| Settings / Assistant: business notes, memory and instructions | Hosted coverage incomplete | Missing | Inspect endpoints, ownership and secret boundaries |
| Settings / Providers: supported provider configuration and sign-in | Hosted keys/settings routes mounted | API-key providers implemented in current work | Primary, fallback and photo provider; per provider a secure key entry (stored server-side, only a masked form returned), connection test, removal with confirmation, listing/photo models and reasoning. MiMo's own editor and sign-in providers (Mac only) remain |
| Settings / Connections: marketplaces and Chrome/device status | Browser-local Chrome consent exists; cross-device actions incomplete | Missing | Extension must not be assumed to exist on iPhone |
| Settings / Agents: agent configuration | Desktop configuration; hosted coverage needs audit | Missing | Determine which agent settings apply to hosted execution |
| Settings / Data: account import/export and backups | Desktop backup/import services; hosted port incomplete | Missing | Cloud account import/export; Mac-local backup controls stay on Mac |
| Settings / Logs: diagnostics and fill results | Desktop diagnostics and fill logs; hosted coverage incomplete | Missing | Seller-safe diagnostics with no secrets or private exports in logs |
| Settings / Help: onboarding, help, changelog and support | Hosted changelog mounted; other coverage needs audit | Missing | Native onboarding/help routes, support links and app-store readiness |
| App lifecycle: foreground refresh, interrupted work, push/deep links | Durable tasks exist; no complete iOS lifecycle integration | Partial | Reconnect generation explicitly today; audit notifications and stale screen refresh |
| Public release: privacy, account deletion, permission descriptions and distribution | Not covered by existing TestFlight flow | Missing | Complete before a public App Store release; TestFlight is not release readiness |

## Core visual alignment

The current visual pass uses the desktop inspector's section headings and flat
dividers, photos before fields, a native actions menu for assistant/history,
and a prominent Save action. Inventory rows show private cover thumbnails;
the photo tray uses an adaptive grid with an expand control. The separate
layout-review simulator app uses the production row/section/field views and
invented sample data, without calling the backend or touching a seller account.

Simulator inventory layout was inspected. Opening the editor for visual review
was blocked by repeated device-runner build failures after one cache recovery.
Editor appearance, dark mode, accessibility text sizes and real-account photo
loading still need device review. Compilation is not visual verification.

## Delivery order

1. Finish verification of generation/chat and listing history/restore.
2. Port the full shared inventory and listing contract, preserving all fields.
3. Complete provider and listing settings so iPhone workflows do not depend on
   configuring the web app first; finish photo and assistant attachments.
4. Port analytics, sourcing, standalone assistant, usage and remaining settings
   against verified seller-scoped hosted services.
5. Complete public marketplace connectivity and approved marketplace workflows.
6. Finish lifecycle, accessibility, privacy and public-release validation.

Each layer remains usable and reviewable. Keep incomplete rows visible until
verified; do not replace working functionality with placeholder destinations.
