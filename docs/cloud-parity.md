# Desktop and web parity

The web app must support the desktop seller workflows through the same React
components and domain services. A page being bundled or visible does not count
as a completed workflow. Actions must load real account data, persist changes,
report failures, and survive refresh. Marketplace publishing still requires an
explicit seller request naming the marketplaces and carrying confirmation.

## Agreed scope

On 2026-10-30 the seller explicitly chose to skip video in the web version.
Desktop video support stays intact. Web parity work excludes video upload,
playback and marketplace video selection; no Supabase plan upgrade is needed
for that feature. Keep video controls hidden in the hosted runtime.

## Work in progress

The shared service foundation merged in PR [#982](https://github.com/btxbtwn/anteroom/pull/982). The deployed public release is now 0.1.587. Do not describe the currently deployed inventory/editor
as a complete web port.

The service boundary is `services/service_context.py`. A verified hosted request
installs its seller's database session factory, settings, Vault and photo store.
Async tasks and worker threads inherit that boundary. Desktop calls retain the
local implementations. Hosted operations without an execution owner must fail
before accessing local files, the Keychain or an unscoped database session.

`1e3d624c0ae2` adds owner-scoped references to Vault credentials and consolidates
the first cloud release's preferences behind the shared settings service. Only
private, owner-checked SQL functions may access Vault; the application role has
no grants on its secret tables or decrypted view. This revision is applied in production.

`56b5ee7250ec` adds a durable draft-generation/chat queue and private event
journal. The trusted worker claims work through a restricted function, then
enters the claimed seller's service context. Every ORM save checks its lease;
cancellation or expiry prevents late writes. Interrupted work keeps its saved
draft and asks for review rather than replaying partial generation. Publishing
and delisting cannot enter this queue. The hosted draft worker is enabled in the deployed 0.1.574 release.

The branch now mounts the canonical provider-key/model settings, analytics,
sales ledger/CSV exports, sale calendar, box records, and photo-edit handlers.
Hosted API-key providers use Vault and sanitize request/stream errors. SDK
sign-ins still require the registered-worker integration. Photo rotate,
adjustment previews, saved edits and reset use private Storage and retain the
original. Mac Vision background removal still needs the worker.

Catalog indexes, materialized documents and rebuild threads have seller-owned
execution state. Business-context cache keys include the seller. CSV downloads
use authenticated transport and reject results if the account changes while
the export is loading.

## Completion checklist

- Shared inventory/editor, draft fields, notes, history and private photo reads
  are deployed, including crop, rotate, adjustments and reset. Background removal
  still needs the Mac worker.
  Finish copy/templates UI, bulk actions,
  category options, and all field/default controls.
- Finish provider settings, encrypted credentials, generation, chat, assistant,
  measurements, comps and suggestions using the shared services. Credentials
  never enter ordinary JSON preferences, responses, logs or extension messages.
- Import the Mac's inventory, photos and related records into the authenticated
  account, preserving IDs/relationships safely and checking existing data.
  Done: the Mac app's **Move this Mac to your account** (see
  `cloud-architecture.md`, step 2). Videos and chat attachment files remain.
- Add reviewed Chrome Bridge command contracts for every supported marketplace
  form and approved action. Preserve the shared browser operation locks and the
  app-managed local Bridge build check. Never add an arbitrary API/RPC proxy.
- Connect an outbound Mac worker for SDK sign-ins or other capabilities that
  need that desktop. Work must remain bound to the authenticated seller and
  explicitly registered worker.
- Port real sourcing, analytics, sales ledger/calendar, marketplace connections,
  offers, automations, usage, settings, logs, diagnostics and export workflows.
  Replace filesystem dependencies with account storage or browser equivalents.
- Make ongoing work durable across API restarts. Restrict retries to safe draft
  and read operations; ambiguous publish/delist outcomes need seller review.
- Remove capability gates only after their workflows are implemented and tested.
  Verify required checks, two-account isolation, desktop regression flows, and
  signed-in browser flows before deployment and the final handback.

## Verification already run on this branch

The first service-context changes passed 45 context/Keychain tests. The scoped
settings/middleware changes passed 127 local storage, session and migration
tests, plus 37 Postgres ownership/isolation/hosted API tests. These results are
intermediate; they do not establish web/desktop feature parity.

The full SQLite backend suite passed 3,777 tests and 1,420 subtests; all 817
frontend tests and the frontend build passed. Later media changes passed 29
desktop tests (one platform-specific skip) and 10 Postgres hosted reporting,
worker, ownership and image-edit tests. The full Postgres suite also passed 3,777 tests and 1,420 subtests before the subsequent copy/media changes.

Copy/template and photo-namespace changes passed 147 desktop/import tests and
two hosted copy/startup tests. A rollback-only transaction on the real Supabase
project verified both Vault and private worker claims under the restricted
roles. The transaction rolled back test credentials, tables, functions, grants
and migration stamps; production stays at `c93392ae8c16`. These are intermediate
checks. Full media, worker and UI parity remain unfinished.

The 0.1.570 integration passed 828 frontend tests, lint, both desktop and hosted
frontend builds, version/changelog checks and gitleaks. The complete SQLite
backend run passed 3,823 tests and 1,424 subtests (52 platform/feature skips).
The complete Postgres run passed 3,826 tests and 1,424 subtests (49 skips).
Import test fixtures now pass the destination conversation ID required by
owned photo storage. No production migration, deployment or plan upgrade was
performed during this verification.

Provider settings UI is connected on `feat/hosted-provider-settings`: API-key
providers and MiMo use the shared controls with account Vault storage. The web
settings sidebar currently exposes Providers; other sections remain pending
their service integrations. Existing SDK provider preferences remain visible,
with a Mac-worker notice rather than hosted sign-in/terminal controls. Generation, text chat and private image attachments are deployed.

On `feat/hosted-generation-flow`, runtime availability of the durable hosted
worker controls Generate and listing text chat. Status now reports the seller’s
configured provider. Chat image attachments remain gated until private durable
attachment storage is connected; desktop attachments remain enabled. Suggestions
retain the assistant capability gate until those endpoints are ported. The
production worker setting remains disabled, so this is not deployed activation.

## Deployed hosted draft release

PRs #982, #983, #985 and #986 are merged. The tested release image is
`registry.fly.io/peaceful-meadowstone-6944:hosted-drafts-8823ab1a4`
(digest `sha256:7cc3134596e0853b16212dbfc705c92e0e7dfb146454e4987a1e2e6f7af9f43f`).
It passed private Fly startup, bundled assets, canonical rules and authentication
checks on a temporary Machine in `ord`, exiting 0. Both temporary test Machines
were removed. Version 0.1.574 is deployed; public health/assets/authentication and the deployed worker/database isolation checks passed. Production is migrated to `56b5ee7250ec`, with a private consistent recovery copy of the prior public application records. Fly trial billing still affects uptime.

Fly configuration enables the existing draft worker and keeps one Machine
running. An idle-stop policy cannot guarantee work continues after a browser
disconnects. This worker accepts generation and text chat only; marketplace
publishing and delisting remain outside its queue.

The completed deployment preserved a consistent private recovery copy of all
public application records, then migrated from `c93392ae8c16` through
`56b5ee7250ec`. Listing, photo and message counts were preserved. Restricted
runtime access, seller row isolation, worker configuration, public health, assets
and authentication rejection passed. No database was reset.
The application and worker must never log credentials or session tokens.

This release is an intermediate working web increment. Continue private chat
attachments, native forms/options, approved browser actions, the outbound Mac
worker, inventory import and the remaining views after deployment. Video stays
excluded. Full parity has not been established.

Private listing-chat attachments merged in #991 and are deployed in 0.1.578.
Images upload before queue submission; durable payloads keep owned Storage
references rather than data URLs. The shared chat handler consumes those references,
and authenticated reads/thumbnails verify the conversation owner. Draft deletion
removes both queued and message attachment objects after metadata commits.
Request size and upload concurrency are bounded. The runtime advertises attachment
controls only with an active draft worker. Full parity remains unfinished.

On `feat/hosted-native-catalog-forms`, hosted Poshmark and Depop use the exact
canonical native form builders and authenticated HTTP contract used by desktop.
The Chrome-only form transport override is removed. Categories, category-dependent
choices, sections, row widths and saved values come from those builders, with
owner/revision checks and no marketplace jobs or writes. Account-dependent forms
and approved marketplace actions still need their browser/worker integration.

The shared Poshmark/Depop forms merged in #992 and are deployed in 0.1.579.
PR #994 is merged and deployed in 0.1.581. Reviewed shared preference handlers replace
the Poshmark-only marketplace placeholder and read-only defaults. Marketplace
selection, package/ZIP/location defaults, formulas and hide/restore controls use
the seller-owned settings store. Listing-specific changes verify ownership before
calling the shared service. Other settings sections and account actions remain
pending their reviewed integrations.

The Listings settings page is connected on `feat/hosted-listings-settings`. It
uses the shared template/default/formula controls and label-management handlers.
The similar-item finder reads private account photos, stores owner-scoped hashes
and reviews, and merges only explicitly chosen owned items. Active draft tasks
block merges under the submission row locks. Vendoo controls remain unavailable
in hosted mode until their approved browser actions are connected.

Release 0.1.581 passed public health/version and unsigned private-route checks.
Its release image also passed private startup, assets and synthetic-owner
settings/database checks with the production runtime role, creating no account
data. The API now runs in `ord` after a stalled image pull on its prior host;
that stopped, stateless Machine was removed after the replacement was healthy.
Fly's trial limit still requires billing for reliable uptime.

PR #995 is merged and deployed in 0.1.582. Public health/version and unsigned
settings/duplicate-review rejection checks passed. General settings are being
connected on `feat/hosted-general-settings`: appearance and listing-name choices
use account preferences; About reloads the web bundle and reads the same bundled
changelog as desktop. Desktop installation endpoints and its SDK/Brave setup
tutorial remain unavailable in hosted mode. Other unported sections stay gated.

PR #996 is merged and deployed in 0.1.583; public health reports that version.
On `feat/hosted-bulk-inventory`, selection exposes the shared bulk-copy review
and price-markdown dialogs. Hosted price requests require owned items and the
reviewed revision for every drop, with bounded batch sizes. Price writes share
draft submission row locks and refuse queued/running tasks. Other bulk actions
stay unavailable until their own service/browser workflows are ready.

PR #997 is merged and deployed in 0.1.584; public health/version and unsigned
bulk-price rejection checks passed. The Suggestions feed is being connected on
`feat/hosted-suggestions` as an independent capability from the assistant and
generation. The canonical rules use owned inventory, photos, sales and stale-day
preferences; queued/running draft tasks stay out of the feed. Cards open existing
listing controls or the reviewed markdown dialog. The assistant remains gated.

PR #999 is merged. Release 0.1.587 is deployed, including Suggestions and the
newer eBay offers UI fix. Its final image passed private startup/isolation checks
and public health/version and unsigned Suggestions rejection checks. The brief
unmerged 0.1.585 deployment was restored to 0.1.584 before this merged release.

On `feat/hosted-bulk-edits`, the shared bulk editor reviews and saves owned
fields, tags and labels with row locks, draft-task checks and revision/basis/result
fences. Static dropdown choices use the canonical catalog handler. Bulk category
pickers and category writes stay gated until the verified browser-backed catalog
is connected. Desktop retains its existing category pickers and send gate.

On `feat/hosted-parity-next`, selection also exposes the shared bulk delete
review. Hosted deletes reuse the single-item removal service: only owned,
unsent, idle drafts are removed, each under its own row lock with the desktop
review token re-checked, and Storage objects are released after the commit.
Nothing is delisted and no job is created.

On `feat/hosted-bulk-regenerate`, the web reset endpoint backs Clear, single
Regenerate and bulk Regenerate. It shares the removal service's rules: only
owned, unsent, idle drafts are wiped. Regenerate keeps photos and carries
listing-only facts into Item Details; Clear also releases photos from Storage.
The single price drop bulk Regenerate saves first uses the shared service and
submission row lock. Bulk Regenerate is offered only when the draft worker is
running, and its generate stream uses the authenticated transport. Items with
a Send or marketplace record stay excluded until imported inventory and
marketplace actions are ported.

On `feat/hosted-catalog-categories`, the shared category trees are loaded into
Postgres by the operator command in `deploy/fly/README.md`, using the migration
login. The web app mounts the canonical category tree and search handlers and
reports `catalog_categories` only when all six trees are complete. Bulk category
writes then use the verified leaves; a hosted eBay pick must also be a synced eBay
leaf with the same path. Each seller's catalog index state now persists across
requests, and hosted lookups never build it inline: a background build publishes
it while search uses the verified lexical fallback. The Machine has 2 GB for that
build. The index is still per seller; a shared category index is needed before
many sellers use the web app.

On `feat/hosted-usage`, the Usage view uses the canonical usage-limits handler.
Its cache is keyed by seller, and on the web the Claude row reports that
sign-in usage is shown in the Mac app instead of probing a CLI login on the
server.

On `feat/hosted-mercari-form`, the Mercari tab uses the canonical native form
builder through the same owned, revision-checked route as Poshmark and Depop.
Its only outbound read is Mercari's anonymous public shipping quote; a failed
quote leaves every product field available with a note.

On `feat/hosted-automations`, the Automations view mounts the desktop handlers
for saving, previewing, pausing, running and history, plus `/api/errors`. A
`SECURITY DEFINER` function (`due_automation_owners`, revision `3b9d6f2a1c47`)
lets only the trusted process login list sellers with due or unfinished runs;
each tick then runs in that seller's row-secured context. Regenerate queues the
seller's durable draft task. Marketplace writes stay approvals, and approving
one is refused until web marketplace actions exist. Offers and sharing panels
need marketplace sessions and stay hidden. Fly's trial stops the Machine after
five minutes, so schedules only fire while it is running.

On `feat/hosted-assistant`, the Assistant view mounts the canonical assistant
routes. Answers stay in-process like desktop, but the run key and its thread
are per seller, so one account never blocks or follows another's answer.
Images are stored under the seller's own assistant thread and readable only by
its owner; deleting a thread releases them. Applying proposed changes takes the
item's row lock first. The shop summary skips the Mac's local sourcing file,
which the web app does not have yet. Answers do not survive an API restart.

On `feat/hosted-marketplace-accounts`, Settings → Connections shows eBay and
Etsy sign-in on the web. Each seller registers as their own relay device and
keeps tokens in their Vault through the keychain boundary. Pending sign-ins and
token-refresh locks are keyed by seller, the seller's browser opens the sign-in
page, and the server never opens a browser. eBay and Etsy listing defaults and
policy pickers use the canonical handlers. Sale streaming, sale detection and
Chrome sessions stay off on the web until they have a seller-owned worker.

The same branch adds the eBay tab: the canonical eBay form builder reads the
category schema (cached per seller in `category_field_schemas`) and the seller's
own business policies and locations with their connected account.

On `feat/hosted-marketplaces`, the Marketplaces view mounts the desktop eBay and
Etsy workspace handlers, eBay and Etsy imports, order recovery and sale alerts,
all in the seller's row-secured context with their own tokens. eBay account
changes keep the review-then-confirm steps. The eBay Offers tab stays hidden
until automatic offers have a per-seller scheduler; the offer status read leaves
out the Mac timer's last check. Poshmark, Mercari, Depop, Sellwild and Vinted
show their recorded sales; their Chrome imports need marketplace sessions.
