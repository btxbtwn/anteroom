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

Branch: `feat/hosted-desktop-parity`. The public release remains 0.1.561 until
this work is verified. Do not describe the currently deployed inventory/editor
as a complete web port.

The service boundary is `services/service_context.py`. A verified hosted request
installs its seller's database session factory, settings, Vault and photo store.
Async tasks and worker threads inherit that boundary. Desktop calls retain the
local implementations. Hosted operations without an execution owner must fail
before accessing local files, the Keychain or an unscoped database session.

`1e3d624c0ae2` adds owner-scoped references to Vault credentials and consolidates
the first cloud release's preferences behind the shared settings service. Only
private, owner-checked SQL functions may access Vault; the application role has
no grants on its secret tables or decrypted view. This revision is not applied
to production yet.

`56b5ee7250ec` adds a durable draft-generation/chat queue and private event
journal. The trusted worker claims work through a restricted function, then
enters the claimed seller's service context. Every ORM save checks its lease;
cancellation or expiry prevents late writes. Interrupted work keeps its saved
draft and asks for review rather than replaying partial generation. Publishing
and delisting cannot enter this queue. The worker remains disabled in the
deployed configuration until the remaining integration is verified.

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
  are deployed. Finish full photo editing, copy/templates, bulk actions,
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
