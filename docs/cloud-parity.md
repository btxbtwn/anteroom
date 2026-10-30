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

The shared service foundation merged in PR [#982](https://github.com/btxbtwn/list-this-vendoo-suite/pull/982). The public release is now 0.1.574. Do not describe the currently deployed inventory/editor
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
with a Mac-worker notice rather than hosted sign-in/terminal controls. Generation and text chat are deployed; image attachments are the next increment.

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

Private listing-chat attachments are implemented on `feat/hosted-chat-attachments`.
Images upload before queue submission; durable payloads keep owned Storage
references rather than data URLs. The shared chat handler consumes those references,
and authenticated reads/thumbnails verify the conversation owner. Draft deletion
removes both queued and message attachment objects after metadata commits.
Request size and upload concurrency are bounded. The runtime advertises attachment
controls only with an active draft worker. Full parity remains unfinished.
