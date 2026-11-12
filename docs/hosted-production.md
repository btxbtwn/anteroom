# Fly.io and Supabase operating guide

Reviewed against official documentation on 2026-11-02, with measurements from
the San Jose Machine on 2026-11-03. Requirements below are not all claims of
completion. The audit table says what has been measured or implemented.
For interaction targets and live test history, see [responsiveness](responsiveness.md).

## 1. Isolate and size the work

Fly process groups run on separate Machines and can scale independently.
Use the existing `app` group for interactive API and device control, and add a
worker group for durable draft work and scheduled jobs only after the cost
below is approved. Separate event-loop threads inside one Python process still
share its CPU and memory. This is our application of
[Fly's process-group model](https://docs.fly.io/launch/processes).

Shared Fly CPUs can burst, but their documented sustained baseline is 6.25%
per vCPU after credit is depleted. Performance CPUs have a full CPU quota.
See [CPU performance](https://docs.fly.io/machines/cpu-performance).

The 0.1.695 Machine is `shared-cpu-1x` with 2 GB in `sjc` (id `8d1d23ce0264e8`).
On 2026-11-03 its one-minute load was 19 shortly after restart and about 6
later, on one vCPU, with about 1.2 GB of memory free. About 26% of `/proc/stat`
ticks since boot were steal. The cgroup `nr_throttled` counter was 0, so that
sample does not by itself prove CFS quota exhaustion.

Mac and Chrome commands are seller-scoped. The process the device is polling
records a private route and keeps the command payload in memory. Another
process forwards once to that machine's private address. A failed forward is
not retried, so an uncertain marketplace action is not sent again. The route
table is `anteroom_private.device_routes`. Until that migration is applied, a
single process keeps using its local relays. A worker process must not be
started before the migration and `ANTEROOM_PROCESS_TOKEN` exist.

Direct marketplace jobs record their owner on the job row. On Fly
(`FLY_MACHINE_ID` set) the Machine running a job holds a 30-second lease on it
and renews it every 5 seconds from one thread. Another Machine's watchdog, Check,
Reset and Mark treat the job as running until that lease lapses, and only then
mark it interrupted with its result unconfirmed; nothing is retried or queued
again. Cancel sets the row's `cancel_requested`; the owner picks it up with its
next renewal, so a Cancel that reaches another Machine stops the job within
about 5 seconds. One server process per Machine is assumed: a restarted Machine
does not wait out its own earlier lease. The Mac writes no lease and keeps
liveness in memory.

Scheduled work on two Machines with role `all` (or two workers): draft and
assistant tasks are claimed with fenced leases; automation slots by a unique
run per slot, and a waiting run by an atomic status change; a timed promotion
round by an atomic change of its due time; each promotion send re-checks its
journal under the seller's advisory lock; eBay offer sends reserve their rule
atomically; sale rows are unique per order line. Still not claimed across
Machines: an automation run still `running` after 20 minutes can be requeued by
another Machine and continue its unacted items there (regenerations and
approval requests only, never marketplace writes); a Machine starting up marks
another Machine's in-flight eBay offer or promotion round unknown or
interrupted and pauses that rule (conservative, never resent); and Sourcing
refreshes and sale reads can run on both, costing duplicate reads only. Run one
role `all` or `worker` Machine until these are claimed.

**Not deployed, pending cost approval.** Prices are Fly's published monthly
rates for continuous Machines in the cheapest US regions (`iad` / `ewr`) as of
2026-11-03. San Jose uses a regional multiplier; confirm it on
[Fly's pricing page](https://fly.io/pricing) before accepting a number.

| Option | Shape | Published monthly cost | Why it is or is not enough |
| --- | --- | --- | --- |
| Current | 1× shared-cpu-1x, 2 GB | $12.70 | Load of 6–19 on one vCPU. |
| A | Add a second shared-cpu-1x, 2 GB worker | about $25.40 | Each shared CPU still settles near 6.25% after burst. |
| B | Replace the Machine with performance-1x, 2 GB | $33.00 | One full core. HTTP and workers still share the process. |
| C | Keep the shared web Machine and add a performance-1x, 2 GB worker | about $45.70 | Separates interactive traffic from generation and gives the worker a full core. |

The seller approved option B on 2026-11-03. On 2026-11-11 the seller approved
adding a worker Machine (about $13 a month more than option B), which reverses
option C: sellers' pages kept 33% of the CPU in the 2026-11-09 sample
(inventory and Suggestions rebuilds), so the serving Machine keeps the full
core and the worker takes the shared one.

| Process group | Role | Machine | Runs |
| --- | --- | --- | --- |
| `app` | `web` | performance-1x, 2 GB | Seller requests, Mac and Chrome connections, direct marketplace jobs, Chrome sale checks and promotion rounds (`chrome` lease) |
| `worker` | `worker` | shared-cpu-1x, 2 GB | Drafts, assistant answers, automations, eBay offers, the job watchdog, Sourcing and sales sync (`schedulers` lease) |

Chrome sale checks and promotion rounds stay on the web Machine because they
list sellers from the Chrome tabs connected to their own process
(`hosted_browser_relay.ready_sellers`); on the worker that list would be empty.
`process_handoff.lead` holds each lease separately and releases them only after
every set has stopped. A worker whose draft loop has died fails `/api/health`
(503), so Fly restarts it.

A web process queues drafts and leaves them to the worker group; it never
claims one. The worker answers forwarded Mac and Chrome calls on the private
network and has its own health check, so blue-green deploys wait for it. If
long runs of generation exhaust the shared CPU's burst credit, drafts slow
down but pages do not; move the worker to performance then.

## 2. Use database connections deliberately

[Supabase's connection guide](https://supabase.com/docs/guides/database/connecting-to-postgres)
recommends a direct connection for a persistent backend such as a Fly Machine,
and session pooling for an IPv4-only network. Transaction pooling is aimed at
short-lived clients.

From this Machine on 2026-11-03, `SELECT 1` through the current transaction
pooler (port 6543, `sslmode=verify-full`) took 719–880 ms on a new connection
and about 70 ms when the connection was reused. Session mode on port 5432 took
about 160–240 ms reused. A direct connection took about 240 ms reused.
On 2026-11-11 the same reused `SELECT 1` took about 24 ms in every mode
(transaction pooler 24.5, session pooler 24.4, direct over IPv6 23.4), and a
transaction of start, seller role, one query and commit about 95 ms. That cost
is the distance from San Jose to Oregon, not the pooler. The hosted processes
therefore keep the transaction pooler, which also absorbs the second set of
client connections while blue-green deploys overlap, and reuse connections,
with prepared statements still disabled. Each process has its own pool (8 plus
4 overflow); the pooler multiplexes them onto its server connections.
Supabase recommends a direct connection for persistent servers; revisit that
only if the pooler measures slower or a feature needs session state. Desktop and test processes keep
`NullPool`.

The same sample reported `max_connections` 60 and 40 rows in
`pg_stat_activity`. Live two-seller generate traffic exhausted the earlier 5-connection
budget (`4` plus `1` overflow, 1s wait) with QueuePool timeouts. The hosted
process now budgets 8 plus 4 overflow and waits up to 3s (`database.py`;
`ANTEROOM_DB_POOL_SIZE`, `ANTEROOM_DB_MAX_OVERFLOW` and
`ANTEROOM_DB_POOL_TIMEOUT` override them). Generate/assistant
SSE open a short session only to enqueue, then stream. A second process needs
its own budget inside the remainder. Do not raise the pool to one connection
per seller.

Transaction-local `set_config` for the seller role still runs at the start of
every transaction. The isolation check runs once per pooled connection. Keep
the unprivileged runtime login, certificate verification, and the separate
migration login. Do not bypass row security.

The same statement sets `statement_timeout = 15s` and
`idle_in_transaction_session_timeout = 30s` for the seller transaction
(`services/hosted_database.py`), so a runaway query, a long lock wait or a
transaction left open across a wait cannot keep a pooled connection
indefinitely. The longest seller read measured is the inventory list, about
2 s; the longest legitimate pause inside a transaction is one Storage call,
capped at 20 s. Migrations use their own login and are unaffected. When a
seller sees `canceling statement due to statement timeout` or a connection
closed for idle-in-transaction, fix the code holding the transaction rather
than raising these. The seller policies read the signed-in seller through a
scalar subquery, so Postgres evaluates it once per statement (an InitPlan)
instead of parsing the claims JSON for every row a scan visits.

Inventory, suggestions and sales totals read the stored generated
`listing_revisions.inventory_fields` projection. The complete marketplace
forms remain in `listing_json`; routine inventory reads must not extract their
small fields from that large document. Large PostgreSQL values can be compressed
or stored out of line ([TOAST](https://www.postgresql.org/docs/current/storage-toast.html)).
Production 0.1.706 still exhausted the pool during two concurrent generations;
several full-form JSON facet queries were active for about 16 seconds without
blocking locks. Do not treat the CPU upgrade or refresh coalescing as proof of
two-reseller readiness.

Editor reads must share the existing `/screen` request on a cold open as well
as navigation prefetch. Fetch marketplace forms on tab intent or selection;
never warm every marketplace again after each listing revision. JSON route
dependencies use FastAPI's `scope="function"` to return connections before
sending responses. Fully materialized screen DTOs close the session before
encoding too. Streaming routes retain their separate, short enqueue sessions.
The regression holds three editor responses open with a three-connection pool
while another draft saves. This proves response delivery cannot reserve that
pool; production generation and latency still require a live browser run.

The 0.1.713 production browser run still reproduced pool exhaustion. Its first
error stack showed repeated full inventory reconciliation and suggestion
calculations from several open tabs, including notes parsing and quantity
derivation while transactions remained open. Hosted inventory and suggestion
reads therefore share one build per seller and view. Completed snapshots are
validated with the seller's complete change-feed vector on each request.
The active draft-ID set is checked too, so durable task changes made by worker
SQL invalidate busy suggestions even without a feed notification. Lease renewal
timestamps and queued-to-running transitions do not change that set.
Time-dependent suggestions also refresh after at most 60 seconds. A change
during a build leaves that result uncached. Waiting tabs hold neither a database connection nor a
worker thread; the build owns its session and survives a tab disconnect.
The completed cache retains at most 64 seller/view entries for memory usage,
without limiting requests, sellers or generation tasks. Results never cross
seller accounts, and errors are not cached.

The 0.1.714 browser run reached a separate editor failure without reproducing
pool errors: a late conversation or default-package read cancelled the pending
notes autosave. The deterministic hosted browser regression delays the initial
screen read, types before it completes, and verifies the saved note plus
untouched SKU/flaws. Detail edits now live with their QueryClient and draft ID,
overlay only changed fields on incoming reads, and finish after the editor
unmounts. Saves merge against loaded notes and retain managed metadata; failed
edits remain available for retry. This still requires a production rerun.

Production 0.1.740 exhausted the pool again on 2026-11-06 at 01:43 UTC, and
every listing read from the web editor failed during it. Sampling
`pg_stat_activity` on 0.1.741 showed the promotion scheduler `idle in
transaction` for up to 139 s after a `listing_promotions` read: a share or
offer check read the listing, then waited on Chrome with that transaction
open. Each such wait holds one of the 12 connections for as long as Chrome
takes. The check and the scheduler's like-feed read and read gap now commit
first. `services/chrome_wait_connections.py` records which asyncio task holds
each pooled connection. A Chrome wait (`ReplyWaits.register_wait`) that starts
while its task still holds one logs the call site once in production and fails
the test suite. Tests that replace Chrome above `register_wait` with a mock do
not exercise the check. Not yet measured after deploy: whether the pool stays
clear with automations running alongside two sellers' editors.

Async route handlers that took a request session were a second cause: 62
`async def` handlers ran their queries on the event loop, where a pool wait
stalls every other request, and several kept the transaction open across eBay,
Etsy or Chrome calls (direct List/Update preflight, Check, Delist, imports).
Those handlers now take no request session: each database step runs through
`database.in_worker_session`, a short session on a worker thread that closes
before the next external wait. Handlers that only read are plain `def`. Two
backend tests stop this returning: one fails on any new `async def` handler that
depends on `get_db`/`get_seller_db`, and an autouse fixture fails a test whose
async handler checks out a connection on the event loop. Neither check has
exceptions: every handler has been converted. Async hosted routes that stream a
request body use `hosted_database.in_seller_session`, which keeps
`get_seller_db`'s checks on a worker-thread session. A test holds eight concurrent slow external waits on a
one-connection pool without a timeout. Not yet measured after deploy.

Production 0.1.848 exhausted the pool again on 2026-11-09 at 02:24 UTC
(`GET /api/sourcing`, then most page reads at 3–20 s). Sampling
`pg_stat_activity` showed no long holder: Postgres sessions were idle or
`idle in transaction` on `ClientRead` for about 0.1 s, so connections were
spent on round trips, not on queries. Four `py-spy dump`s of the process showed
2–3 connections in every sample writing draft progress
(`background_tasks.append_events`), beside 5–7 page requests and the
schedulers, on one vCPU at load 1.1–1.5. Each running draft or assistant answer
wrote its progress every 0.25 s in a transaction of about seven round trips
(begin, seller `set_config`, lease check, sequence read, the same lease check
again in `before_flush`, insert, commit), while open streams read it only
every 0.5 s. Writers and readers now share `background_tasks.EVENT_INTERVAL`
(0.5 s) and a leased worker session checks its lease once per write: about
12 round trips a second per running draft instead of 28. Not yet measured
after deploy: whether page reads stay under the pool timeout with two sellers
generating. The stream reader's own poll (about five round trips every 0.5 s
per open stream) is the next cost of the same kind.

The same incident had a CPU cost as well. Samples a few minutes after the
error found all 12 connections `idle in transaction` for 1 to 7 s between
ordinary reads while the process waited for the CPU, and a 30-second py-spy
sample put about 88% of the one vCPU in the API process and 9% in the catalog index worker,
with 33% in the inventory and Suggestions snapshot rebuilds, about 19% in
generation's form preparation and 8% in the catalog index rebuild. Some
listings stored up to 3 MB of `_native_forms` (saved marketplace form
definitions with every option), and each form build and preflight deep-copied
the whole listing, forms included, inside its transaction: once per form,
then once more per marketplace in preflight. A 2 MB form takes about 0.6 s to
copy. Snapshots now share the saved forms read-only
(`native_contract.copy_listing`), and a test fails if form preparation or
preflight copies them or changes them. Remaining: each read still decodes the
full revision JSON, the snapshot rebuilds and catalog index still compete for
the same CPU, and the pool has not been re-measured after deploy.

Production 0.1.910 stalled again on 2026-11-11 at 03:22 UTC, right after the
seller pressed List everywhere on one item: page reads took 30–110 s and
`GET .../marketplace-listings` hit the pool timeout, while the Machine's CPU
stayed under 40%. The requests were waiting for a thread, not the CPU.
`asyncio.to_thread`, which `database.in_worker_session` and every other
async hand-off use, runs on the event loop's default pool, and asyncio sizes
that pool from the CPU count: five threads on one vCPU (`py-spy dump` showed
`asyncio_0`–`asyncio_4`). Each direct marketplace start holds one of them while
`approved_photos.capture` downloads and re-uploads every photo to Storage, so
four or five starts at once left every other database step queued. Plain `def`
routes were unaffected only because FastAPI gives them 40 threads of their own.
The request loop and every `BackgroundLoop` now get a 40-thread pool
(`background_loop.give_threads`); `tests/test_loop_threads.py` holds 12
blocking calls at once on a one-CPU count. Not yet measured after deploy:
whether List everywhere with several marketplaces stays clear of the pool
timeout. Storage refusals still reach the seller as "Could not upload or load
the photo" without Supabase's status in any log, and the operator error emails
failed with `SMTPDataError` during this incident, so the pool-timeout holder
snapshot was not delivered.

Machines reach each other's `/internal/` routes at
`http://[FLY_PRIVATE_IP]:8080` on the private network
(`device_routes.local_base_url`), which does not pass through Fly's proxy. The
proxy adds `Fly-Client-IP` to every request it forwards from the internet, so
any `/internal/` request carrying that header answers 404 before the process
token is checked. A leaked `ANTEROOM_PROCESS_TOKEN` alone therefore cannot
reach a seller's Chrome or Mac from outside. Do not route internal calls
through the public hostname or `<app>.fly.dev`.

## 3. Keep traffic and refresh costs proportional to useful work

Fly's HTTP concurrency type is `requests`, which is what
[Fly's concurrency guide](https://docs.fly.io/apps/concurrency) recommends for
web services. There is no hard limit. The soft limit is Fly's default of 20
until a second Machine exists to absorb overflow; with one Machine and no hard
limit, crossing the soft limit does not reject traffic.

Lease renewal does not publish the `listings` topic. Saves, completion,
failure and cancellation still do.

Private photos stay private. A thumbnail cache and Realtime fan-out have not
been re-measured in this change.

Chat image messages hold at most one upload per seller and four across the
Machine (each can hold about 170 MB while it is parsed; four fit the 2 GB
Machine's measured free memory). A message that cannot get a slot within 10 s
answers 429 (same seller) or 503 (Machine busy) with `Retry-After`; nobody
waits behind another seller's 120-second upload. Images are written to Storage
before the draft row is locked and the task is queued; a failed queue deletes
them again.

New generate, chat and assistant submissions take a token from a per-seller
bucket: 120 at once, refilled at one a second. This is abuse protection, not a
quota; selecting a hundred drafts and generating them all fits in the burst.
Resuming a stream does not count. The bucket lives in each process, so a second
Machine would double the effective allowance; that is acceptable while one
Machine serves traffic.

Uvicorn's access log stays off because sign-in callbacks carry one-time codes
in the URL. The hosted app logs one line per request instead: method, matched
route template (`/api/conversations/{conv_id}/messages`, or `-` when no route
matched, such as a refused session), status and milliseconds. It never logs the
path as sent, a query string, a header or a body.

## 4. Deploy and recover without losing work

`/api/health` is liveness and remains the Fly check. `/api/ready` reads the
database's Alembic revision (through `anteroom_private.schema_revision()`,
since the runtime login cannot read `alembic_version`) with a one-second local
statement timeout. It answers 503 `unavailable` when Postgres cannot be
reached and 503 `schema_mismatch` when the revision is not the one this build
was made for. It is not the Fly check while only one Machine can take traffic:
a database blip or the old Machine seeing the new schema mid-deploy must not
stop the proxy from routing, or restart the only Machine in a loop. The deploy
workflow instead waits for `/api/ready` after the new version is live and
fails the run if it never becomes ready. A model outage does not fail
`/api/health`.

`alembic upgrade head` runs on every deploy. `migrations/env.py` sets
`lock_timeout = '5s'` on the migration connection (`ANTEROOM_MIGRATION_LOCK_TIMEOUT` overrides it; the test suite sets `0` because its workers migrate fresh databases at once), so a revision that cannot
get its table lock fails the deploy (before Fly is touched) instead of queueing
every seller's queries behind it. Retry when traffic is quieter rather than
raising the timeout. Revisions after `e3bea75f1b9b` that build an index on an
existing table must use `op.create_index(..., postgresql_concurrently=True)`
inside `with op.get_context().autocommit_block():`
(`tests/test_migration_locking.py` enforces it). A concurrent build that fails
leaves an `INVALID` index; drop it before retrying.

Deploys use Fly's `bluegreen` strategy: the new Machine starts, passes its
health check and takes traffic before the old one is stopped, so a deploy
never leaves no Machine to answer. Before this, the single Machine was stopped
first; on 2026-11-07 a deploy with open streams answered 503 for 2 min 15 s
(the old process drained requests for up to 120 s before the new one booted),
and an idle one for about 20 s. The two Machines overlap for a few minutes per
deploy; no Machine runs continuously beyond the one approved.

During the overlap only the Machine holding the `schedulers` lease
(`anteroom_private.process_leases`, revision `e5b81d27c4a6`) runs automations,
eBay offers, the job watchdog, sourcing, Chrome sale checks, promotions and
sales sync, because each treats work in progress at its startup as cut off.
Drafts are claimed row by row and run on either Machine. The lease lasts 30 s
and is renewed every 10 s (`services/process_handoff.py`).

Fly sends SIGTERM with a 300-second grace period. On the signal
(`vendoo_studio.hosted_server`) the old Machine at once stops claiming drafts
and stops its schedulers, then releases the lease and its Mac and Chrome
routes, so the new Machine takes them over within about 10 s. Uvicorn then
allows active requests 120 seconds, and the current draft has up to 120
seconds to save. Work past that window keeps its lease and is not marked
complete or replayed.

The server listens on one dual-stack socket: IPv4 for Fly's proxy and IPv6 for
the private network. Until 2026-11-07 it listened on IPv4 only, so
`http://[FLY_PRIVATE_IP]:8080` refused connections and no Machine could have
forwarded a Mac or Chrome command to another. `ANTEROOM_PROCESS_TOKEN` must be
set (staged with `fly secrets set --stage`) before the first blue-green
deploy, or a command that reaches the Machine not holding the device fails
during the overlap. See
[deployment strategies](https://docs.fly.io/launch/deploy) and
[health checks](https://docs.fly.io/reference/health-checks).

Apply Alembic revision `b6e2c4a81f09` with the migration login before starting
a second process. The runtime Machines do not have the migration password.
Apply `ca4d82e917f6` before deploying a build that reads `inventory_fields`.
It computes the projection for existing revisions and keeps future writes
consistent in the database, including Mac imports and direct SQL updates.
It needs a table lock; its five-second Postgres lock timeout refuses to wait
behind long transactions. Check for active work and allow reads to drain before
applying it rather than extending that timeout on a busy system.

The Supabase plan, backup restore, Auth SMTP, and storage egress were not
re-checked from the dashboard in this change. The Free plan can pause and has
no downloadable managed backup; do not describe production as backed by a paid
plan until the dashboard says so.
[Database backups exclude Storage objects](https://supabase.com/docs/guides/platform/backups).

## 5. Prove readiness with the intended workload

Automated tests cover lease notifications, the connection pool choice, route
ownership, seller denial of the route table, forwarded inference, and a single
forward of a marketplace request that is not retried. They do not replace a
live generation.

500 open sessions and 500 simultaneous generations are different tests. Neither
has been run. There is no separate staging app in this change, so that load
was not pointed at production.

## 6. Bridge approval key

The hosted API signs every command it relays to the seller's Chrome (List,
Update, Save draft, result checks, Delist, Cancel, shares and offers, Vendoo
drafts, and the reads of sales, likes, imports and sign-in status), and binds
the photos relayed with a List or Vendoo draft. Anteroom Bridge 0.2.68 and later
run none of them without that signature (0.2.56 to 0.2.67, writes only), so a
script on the Anteroom page cannot list, change or delete a listing, swap its
photos, or read the seller's marketplace data on its own. See [the Bridge protocol](cloud-extension.md#signed-approvals).

The private key is the Fly secret **`ANTEROOM_BRIDGE_SIGNING_KEY`**: an ECDSA
P-256 key in PKCS#8 PEM. Generate it once, on a trusted computer, and never
commit it:

```bash
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out bridge-signing.pem
fly secrets set ANTEROOM_BRIDGE_SIGNING_KEY="$(cat bridge-signing.pem)" --stage
openssl pkey -in bridge-signing.pem -pubout | sed '1d;$d' | tr -d '\n'; echo
```

The last command prints the public key. Put that one line in
`BRIDGE_APPROVAL_PUBLIC_KEY` in `vendoo-extension/background/cloud-approvals.js`.
The two must be a pair: a Bridge with another key, or none, refuses every write
from the web and tells the seller why. Keep the PEM file in the team's password
manager, not on disk. Every API Machine needs the same secret.

Roll out in this order, because installed Bridges update days after a release:

1. Set the secret (staged, as above).
2. Deploy the API. It signs every command only for a Bridge that declares
   `marketplace.signed_commands`, and writes only for one that declares
   `marketplace.signed_approvals`, so Bridges already installed keep working
   unchanged.
3. Release the Bridge with the matching public key. From then on, that Bridge
   refuses any relayed command without a valid approval.

Rotating the key is the same three steps. Approvals last five minutes, so a
Bridge released with the new key refuses writes signed with the old one: deploy
the API with the new secret first, then release the Bridge promptly. Without the
secret the API logs a warning for each command and sends it unsigned, which a
current Bridge refuses.

## Audit

| Priority | State on 2026-11-03 | Still required |
| --- | --- | --- |
| 1: Remove avoidable load | Lease renewal no longer publishes `listings`. Saves and terminal task states still do. Seller A inventory list is about 1.3k drafts / 565 KB and took 7–71 s on 0.1.702. The list endpoint now closes its seller session before encoding that body. | Page or slim the inventory payload so the encode/send itself is cheap; measure again under generate. |
| 2: Size and separate execution | Routing across processes is implemented and tested. One shared CPU idle load about 0.17 after the 0.1.702 deploy. | Set `ANTEROOM_PROCESS_TOKEN` and apply `b6e2c4a81f09` before a second Machine. Cost options above are unchanged. |
| 3: Tune database access | Hosted pool is 8 plus 4 overflow, 3 s wait. SSE and inventory list release before long waits/bodies. On 0.1.708: inventory list ~2.1 s; idle draft GET p50/p95 571/698 ms; under two generates, overlapping draft GET p50/p95 1.8/5.8 s; QueuePool still timed out once—stack was suggestions nested settings while native form refreshes held checkouts across eBay/Chrome waits. Native `editor_form` now rollbacks before those waits. | Re-measure two concurrent generates after deploy; expect no QueuePool errors. Do not raise the pool further. |
| 4: Operational readiness | Request concurrency is explicit. Readiness exists and is not the only Machine's routing check. Seller asked to keep the existing database password (no rotation). | Dashboard review of plan, backups, Auth delivery, and storage egress. |
| 5: Release load | Partial on 0.1.702. Seller A Mac ChatGPT connected (`gpt-6.1-sol`). Two generate streams returned keepalives while draft reads continued. QueuePool still timed out under that overlap. One Mac worker still serves one hosted account. | Second seller ChatGPT on another Mac. Sustained session with zero QueuePool errors. Only then add a Machine (token + migration first). |

## Operator error emails

Every distinct error in the hosted app is emailed to `ccanizares@myanteroom.com`
by `vendoo-studio/server/vendoo_studio/services/error_alerts.py`, switched on by
`ANTEROOM_ERROR_EMAIL = '1'` in `deploy/fly/fly.toml`. Sandboxes and the Mac app
never send.

**What is reported:** every WARNING and ERROR log from Anteroom, uvicorn and asyncio; a
route's unhandled exception and any 4xx or 5xx response (with method, route template,
Fly request id and seller id); uncaught thread and process exceptions; failed
draft tasks and generations; and browser crashes, unhandled promise rejections
and render failures from the web app (`POST /api/error-reports`, signed-in only).
Handled query and mutation failures use that endpoint too, even when the screen
shows its own error message. Marketplace offer preview refusals include the
scrubbed marketplace reason; all jobs transitioning to failed are logged.
Promotion checks report enabled rules more than 30 minutes overdue, including
previously connected sellers whose Chrome has disconnected. This monitoring
remembers sellers within the scheduler process; after a restart it starts
monitoring a seller when their capable Chrome connects again. A stopped process
cannot report its own outage. Reading the automation list also reports overdue
rules, even after a restart before Chrome reconnects. Background reports carry
the seller execution id.

Marketplace reporting applies to every operation through the Chrome bridge,
hosted Chrome reads, and direct eBay and Etsy API clients: listing, saving
drafts, updates, delisting, imports, sales reads, form inspection, offers and
other API operations. Rejected replies and unconfirmed results are logged even
inside HTTP 200 responses, including nested action results and individual bulk
API failures. Reports keep the operation, marketplace, error code and scrubbed
marketplace message; they never include the request payload, photos or order
records. Reporting does not retry an operation or change its result.
Form-fill entries marked failed, invalid, not found or uncertain are reported
even if another field succeeds. Entered values and selectors are excluded.

**Each email:** a subject with environment, severity, exception type and
message; then severity, time, fingerprint, occurrence count, code location,
logger, request, browser, release, Machine/region/process, the message, the
exception, the stack trace, and the same report as JSON. Credentials are masked
by `services/redaction.py` and SQL parameter values are removed.

**Noise control:** reports group by severity and fingerprint (exception type
plus code path, or call site and message shape). Errors are eligible immediately;
repeats wait 15 minutes. Warnings are grouped into at most one digest per hour.
Each message contains at most 20 groups; remaining groups stay pending. The
outbox keeps the latest scrubbed report and occurrence count for each group.

**Budget:** set `ANTEROOM_ERROR_EMAIL_DAILY_LIMIT` to an operator-selected
allowance below the Workspace account's verified relay quota, leaving headroom
for other senders. Its default is zero: retain reports without sending until
an allowance is configured. Google does not provide our customer-specific
allowance in the rejection; confirm it with Workspace support rather than
assuming the published maximum applies to this account.
Every SMTP attempt, including failed or uncertain attempts, reserves one slot
in the rolling 24-hour budget before network activity. A quarter of the budget
(at least one slot) is reserved for errors. A shared 60/hour ceiling also
applies. Accounting, pending groups and backoff live in `error_email_state`,
survive restarts, and are locked transactionally across Machines. SMTP never
holds a database connection. Only the API role can access this operator table;
seller and Supabase client roles have no access.

**Relay refusals:** a daily-quota rejection pauses all senders for 24 hours,
retaining pending reports. Other permanent SMTP refusals wait an hour; transient
failures wait five minutes. These are local retry times, not a promise that
Google's quota has reset. No shutdown flush bypasses the budget. If the process
dies during SMTP, its reservation remains charged; a 120-second lease prevents
another sender from immediately repeating it. An uncertain delivery can be
repeated after lease expiry, still charged against the budget.

**Operator status:** set `ANTEROOM_OPERATOR_USER_ID` to the operator's verified
Supabase user UUID. Only that signed-in seller sees Settings → General → Error
emails, showing budget use, pending occurrence count, the safe delivery failure,
last relay acceptance and next eligible attempt. `/api/error-reports/delivery`
returns no global mail details to other sellers. No report bodies are returned.
The request path still hands scrubbed reports to a bounded in-memory queue;
abrupt termination before persistence or a full queue can omit an email report.
Original application logs remain the diagnostic source; the status includes
the process's queue-drop count.

**Delivery:** Google Workspace SMTP relay (`smtp-relay.gmail.com:587`, TLS
required), which accepts mail only from the app's static egress addresses
`209.71.113.242` and `2a09:8280:e633:1:0:1d6:a261:0` (`fly ips list`, region
`sjc`). These are app-scoped, so they survive deploys. A new region needs its
own egress address added to the relay rule.

**Not covered:** the Mac app (its errors stay in Settings → Logs), the iPhone
app, the Chrome extension, the webhook relay, and errors that code catches
without logging outside the shared request, query, mutation and job paths.
Device failures returned to a hosted job are covered by the failed-job report;
standalone device crashes still need device-side reporting.
