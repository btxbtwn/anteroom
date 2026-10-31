# Desktop UI reference for the native iPhone port

## Review scope and evidence

Reviewed the production desktop frontend built as Anteroom **0.1.609**, source
`74d598ae5d3bb0bc2cf1ab0116b18a195699f27b`, served by the real desktop FastAPI
entry point in its isolated sandbox. Four invented listings and generated
sample photos exercised the real UI components and routes. No seller data,
provider credentials, marketplace connections, publishing or delisting were used.

This is a review of the built frontend that the Mac webview renders. The
packaged macOS window, traffic-light controls and OS integration were not
visually inspected. Connected marketplace pages, real provider accounts,
populated sales dashboards and every dialog are also outside the visual
coverage. Source review of a feature is distinguished from observing it work.

T3 preview could not start because AppArmor blocks its browser sandbox; its
suggested setup required an unavailable sudo password. After two failed opens,
local Playwright/Chromium was used for the visual review. Screenshots are
external review artifacts, not tracked listing photos or diagnostics.

Observed at 1440 × 900 in dark and light appearance:

- Inventory with suggestions, filters, lifecycle tabs and selected rows.
- General listing view with expanded and minimized contextual chat.
- Structured seller inputs, measurements, generated General fields and history.
- Condition choice control and an eBay form's disconnected/error state.
- Standalone Assistant, Analytics, Sourcing, Marketplaces and Automations.
- Settings General, Listings, Assistant, Providers, Connections, Agents, Data,
  Logs and Help. Usage was observed in its loading state; its populated account
  view was not verified. Empty and disconnected states are sandbox evidence,
  not proof that external integrations work.

A 390 × 844 responsive-web sample was also captured. It is not evidence that
native SwiftUI layouts work, and a resize-transition screenshot must not be
used as the native acceptance reference.

## Findings that explain the mismatch

1. **The information structure differs.** Desktop keeps inventory, a
   marketplace selector and the selected listing connected. The native app
   currently offers draft inventory followed by a single General editor.
   Colors alone cannot make those the same product.
2. **Seller inputs are not the generated listing form.** Desktop places Photos,
   Notes & Labels, Pricing & Shipping and Measurements before the generated
   General sections. Native currently omits several of these inputs and treats
   the entire notes value as a plain text field. Desktop's `parseNotes` reads
   structured notes; that contract needs a native representation, not a raw
   JSON editing experience. Changing the serialized notes format is not part
   of this visual audit.
3. **Theme selection differs.** Desktop defaults to Dark and persists Dark,
   Light or System. Native follows the device appearance and does not read the
   shared preference. A light iPhone beside a dark desktop immediately looks
   unrelated even when the palette implementation is similar.
4. **Control types differ.** Desktop has searchable choices, category controls,
   measurements, labels and field-specific help. The native General editor
   uses free text for many of these. A field's semantic type is part of UI
   parity, not an optional cosmetic refinement.
5. **Chat has a different role.** Desktop contextual chat stays mounted while
   minimized and reports progress/unread state. Native chat is a modal task
   whose view model is recreated on opening. The separate shop-wide Assistant
   is another destination; it is not interchangeable with listing chat.
6. **Completion feedback differs.** Desktop edits use a serialized save queue
   with pending/error/retry states and field-level validation. Native has an
   explicit Save and a broader conflict alert. Explicit Save can be a valid
   iOS adaptation, but the status and recovery behavior must remain clear.

PR #1029 improves rows, photo presentation and flat editor sections. It does
not resolve all six findings and must not be described as full UI parity.

## Desktop component map

Paths below are relative to `vendoo-studio/`.

| Concern | Primary source | What must carry into iPhone |
| --- | --- | --- |
| Workspace and view lifecycle | `src/app/App.tsx`, `src/components/WorkspaceTopbar.tsx`, `src/styles/layout.css` | Selected listing context, title and navigation state; adapt columns to native stacks |
| Primary destinations | `src/components/sidebar/SidebarFooter.tsx`, `src/components/MobileNav.tsx`, `src/components/workspaceCrumbs.ts` | Assistant, Inventory, Sourcing, Analytics, Marketplaces, Automations, Usage, Settings; enable only working destinations |
| Inventory | `src/components/ListingSidebar.tsx`, `src/components/sidebar/ListingRow.tsx`, `ListingBits.tsx`, `inventoryFilters.ts` | Suggestions, status shelves/tabs, counts, search, filtering, sorting, covers, selection and row actions |
| Listing context and marketplace status | `src/components/MarketplacesSection.tsx`, `ListingEditor.tsx` | General versus marketplace selection, eligibility, connection/busy/error state, named-platform actions |
| Seller inputs | `src/components/PhotoTray.tsx`, `ItemDetails.tsx`, `garmentMeasurements.ts` | Photos, SKU, notes, flaws, labels, COG, package inputs and garment measurements |
| Generated and marketplace fields | `src/components/ListingEditor.tsx`, `ListingFormControls.tsx`, `listingFieldSections.ts` | Correct controls, sections, labels, source choices, required/help/warning states and field-specific Ask chat |
| Photos | `src/components/PhotoThumbs.tsx`, `PhotoEditor.tsx`, `src/styles/photo-tray.css` | Ordered numbered thumbnails, viewing, edit/rotate/delete, reorder, upload progress and errors |
| Listing chat | `src/components/ListingChatPopup.tsx`, `ChatPanel.tsx`, `src/components/chat/` | Context, composer, model, attachments, progress, stop/reconnect and state across dismiss/minimize |
| History | `src/components/ListingHistory.tsx` | Version context, preview, explicit restore and conflict protection |
| Persistence and validation | `src/api/listingEdits.ts`, `src/components/ListingBlockers.tsx` | Preserve full payload, serialize edits, keep unsaved work after failure, actionable field issues |
| Shared pages and settings | `src/components/WorkspacePage.tsx`, `src/components/settings/SettingsLayout.tsx`, `settingsNav.ts` | Consistent page/section/row hierarchy, descriptions, grouped controls and loading/error states |
| Appearance and primitives | `src/theme.ts`, `src/styles/t3-theme.css`, `base.css`, `src/components/ui/` | Shared neutral palette, typography hierarchy, semantic color and consistent control geometry |

Use `skills/list-this/references/listing-editor-fields.json` for canonical
General field definitions. Do not turn this reference into another listing
rules or marketplace-schema source.

## Observed desktop geometry and visual language

These are reference measurements, not iPhone pixel requirements.

| Element | Observed/reference value | Native adaptation |
| --- | --- | --- |
| Top workspace bar | 38 px | System navigation bar with the listing title/context |
| Inventory column | 340 px in the captured configuration; resizable | Full-width inventory destination with preserved selection/back navigation |
| Inventory cover | About 38 px; rounded 6 px | Approximately 44 pt cover with a comfortably tappable row |
| Body/field text | Typically 14 px | Dynamic Type body/subheadline; never shrink to copy desktop density |
| Inspector heading | 15 px, weight 600 | Scalable semibold subheadline |
| Inspector sections | Flat sections separated by hairlines, 16 px internal gap and 24 px vertical section padding in source | Flat groups with consistent phone gutters, keeping their titles and sequence |
| Input height/radius | Common inputs 30 px tall with 10 px radius in the built desktop | At least a 44 pt interaction target and scalable text; retain restrained borders/radius |
| Common radius scale | Base `.625rem` (10 px), control radius 8 px | A small coherent radius scale; avoid unrelated large cards around every group |
| Photo thumbnails | 64 px, rounded 8 px, numbered 01/02… | Larger touch-friendly thumbnails; a grid or strip is acceptable if order/view/edit controls remain clear |
| Light palette | Near-white canvas, white cards, zinc text, blue primary | Same semantic roles under Light; do not replace the entire hierarchy with default grouped-form gray |
| Dark palette | Neutral near-black canvas, subtly raised surfaces, pale neutral text, blue primary | Same semantic roles under Dark; preserve contrast, not just a black background |
| Status color | Draft warning, in-progress info, active success, sold primary, failed destructive | Preserve status meanings with text/icons as well as color |

Measured root tokens in the built dark UI included `--background:
oklch(14.5% 0 none)`, `--foreground: oklch(97% 0 none)`,
`--primary: oklch(57.1% .21 264)` and a border based on 6% white.
Light uses `oklch(99.2% 0 0)` for the canvas, white cards, zinc foreground and
`oklch(48.8% .217 264)` for primary. Prefer the existing semantic theme source
over maintaining independent guessed colors.

## Required native screen structure

### Inventory

Keep photos, readable titles, lifecycle status and selection context. Add the
actual All/Draft/Active/Sold filtering, counts, suggestions and sorts as their
backend contracts are ported. A timestamp beside the word Draft is not a
replacement for that workflow. Row actions belong in accessible menus/swipe
actions; destructive operations need confirmation. Search, create and selection
must be reachable without copying desktop hover-only affordances.

### Selected listing

Use a native stack and an accessible General/marketplace selector. Keep the
selected marketplace and its status explicit; do not render fake marketplace
tabs before a working form exists. Within General preserve this distinction:

1. Photos and seller evidence: Notes & Labels, Pricing & Shipping and
   Measurements (in), including the garment-specific measurements.
2. Generated General fields: Listing, Pricing, Condition, Item, **Size & fit**,
   Color & material, Shipping, Inventory & identifiers.
3. Contextual assistant, history and actionable validation/recovery.

Within Shipping, the observed General order is Package Weight, Package
Dimensions, then Inventory location. A direct marketplace form must instead
follow its native server/extension schema, including sections and half-width
rows; never apply General grouping rules to a native marketplace form.

The existing native generic text field for Category, Condition or Size is not
a complete substitute for a selection control. Where choices are available,
use a searchable native selection sheet. Retain labels, required indicators,
help, current selections and dependent-field reset behavior.

### Photos and chat

Keep the photo order visible and provide full-size viewing. Camera/library
picking is an iPhone adaptation; desktop folder selection is not a required
phone control. Reorder/edit/delete must work without hover or tiny targets.

Keep listing chat clearly associated with its listing. A native sheet or
separate pushed view can replace the floating desktop popup, provided typed
messages, progress and work state survive leaving and reopening it. Preserve
a visible way to reach it. Do not present the shop-wide Assistant as the same
conversation. Never auto-retry a mutation merely because a view reappears.

### Other destinations

- Settings: grouped rows with explanatory text and suitable controls, preserving
  General, Listings, Assistant, Providers, Connections, Agents, Data, Logs and
  Help as their features become available. Mac-local controls such as traffic
  lights, local data paths and updater controls do not belong on iPhone.
- Analytics: maintain Sales records versus Inventory analytics, filters,
  meaningful empty states, currency separation and export actions.
- Sourcing: maintain Wholesale boxes versus Scout an item, purchase inputs,
  evidence/uncertainty and explicit disconnected states.
- Marketplaces: account/channel context, orders/listings/sales subviews and
  connection state; do not transplant a desktop multi-column table unchanged.
- Automations: visibly separate configuration, work awaiting review and run
  outcomes. Scheduling never becomes permission to publish or delist.
- Usage: show readable subscription/provider state and refresh/error feedback;
  the sandbox loading view is insufficient to specify populated charts.

## Prioritized remaining work and acceptance

| Priority | Work | Evidence required before calling it matched |
| --- | --- | --- |
| First | Shared theme preference, exact section names and field order | Same account/theme compared in built desktop and native iPhone, Light and Dark |
| First | Native structured seller inputs and measurements | Photos → evidence/measurements → generated form; notes round-trip without exposing or losing structured values |
| First | Full inventory and full listing contract | Same sample items/status/counts and preserved marketplace payload after an edit |
| Next | Typed selection controls and field-level validation/help | Comparable category/condition/size choices and useful correction flows |
| Next | Persistent contextual chat and complete photo interaction | Leave/return without losing text/progress; view/reorder/edit/delete photos at accessible touch sizes |
| Then | Remaining destinations | Build each from its desktop hierarchy and verified hosted contract, not placeholder pages |

For visual acceptance, compare the **same sample listing** in both apps:
inventory, General top, seller inputs/measurements, generated fields,
marketplace selection, open chat, history, and an error/recovery state. Repeat
in Light/Dark and at an accessibility text size. Confirm keyboard/safe-area
behavior and 44 pt interaction targets on iPhone. Record screenshots in the PR;
compilation or a successful TestFlight install alone does not satisfy this.

## Merge interpretation

The layout PR is an incremental improvement to the existing draft workflow.
It is not the complete desktop-to-iPhone UI port. The remaining items above
stay on `iphone-parity.md` until implementation **and** device verification are
complete. The audit itself changes no app behavior.
