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

The seller approved option B on 2026-11-03. The existing Machine now has one
performance CPU and 2 GB RAM. No additional Machine or larger configuration
has been approved.

## 2. Use database connections deliberately

[Supabase's connection guide](https://supabase.com/docs/guides/database/connecting-to-postgres)
recommends a direct connection for a persistent backend such as a Fly Machine,
and session pooling for an IPv4-only network. Transaction pooling is aimed at
short-lived clients.

From this Machine on 2026-11-03, `SELECT 1` through the current transaction
pooler (port 6543, `sslmode=verify-full`) took 719–880 ms on a new connection
and about 70 ms when the connection was reused. Session mode on port 5432 took
about 160–240 ms reused. A direct connection took about 240 ms reused. The
hosted process therefore keeps the transaction pooler and reuses connections,
with prepared statements still disabled. Desktop and test processes keep
`NullPool`.

The same sample reported `max_connections` 60 and 40 rows in
`pg_stat_activity`. Live two-seller generate traffic exhausted a 5-connection
budget (`4` plus `1` overflow, 1s wait) with QueuePool timeouts. The hosted
process now budgets 8 plus 4 overflow and waits up to 3s. Generate/assistant
SSE open a short session only to enqueue, then stream. A second process needs
its own budget inside the remainder. Do not raise the pool to one connection
per seller.

Transaction-local `set_config` for the seller role still runs at the start of
every transaction. The isolation check runs once per pooled connection. Keep
the unprivileged runtime login, certificate verification, and the separate
migration login. Do not bypass row security.

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

## 4. Deploy and recover without losing work

`/api/health` is liveness and remains the Fly check. `/api/ready` runs
`SELECT 1` with a one-second local statement timeout and is not the Fly check
while only one Machine can take traffic. A model outage does not fail
`/api/health`.

Fly still sends SIGTERM with a 300-second grace period. Uvicorn allows active
requests 120 seconds, then the draft worker stops claiming and has up to 120
seconds to save the current draft. Work past that window keeps its lease and
is not marked complete or replayed. See
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

## Audit

| Priority | State on 2026-11-03 | Still required |
| --- | --- | --- |
| 1: Remove avoidable load | Lease renewal no longer publishes `listings`. Saves and terminal task states still do. Seller A inventory list is about 1.3k drafts / 565 KB and took 7–71 s on 0.1.702. The list endpoint now closes its seller session before encoding that body. | Page or slim the inventory payload so the encode/send itself is cheap; measure again under generate. |
| 2: Size and separate execution | Routing across processes is implemented and tested. One shared CPU idle load about 0.17 after the 0.1.702 deploy. | Set `ANTEROOM_PROCESS_TOKEN` and apply `b6e2c4a81f09` before a second Machine. Cost options above are unchanged. |
| 3: Tune database access | Hosted pool is 8 plus 4 overflow, 3 s wait. SSE and inventory list release before long waits/bodies. On 0.1.708: inventory list ~2.1 s; idle draft GET p50/p95 571/698 ms; under two generates, overlapping draft GET p50/p95 1.8/5.8 s; QueuePool still timed out once—stack was suggestions nested settings while native form refreshes held checkouts across eBay/Chrome waits. Native `editor_form` now rollbacks before those waits. | Re-measure two concurrent generates after deploy; expect no QueuePool errors. Do not raise the pool further. |
| 4: Operational readiness | Request concurrency is explicit. Readiness exists and is not the only Machine's routing check. Seller asked to keep the existing database password (no rotation). | Dashboard review of plan, backups, Auth delivery, and storage egress. |
| 5: Release load | Partial on 0.1.702. Seller A Mac ChatGPT connected (`gpt-6.1-sol`). Two generate streams returned keepalives while draft reads continued. QueuePool still timed out under that overlap. One Mac worker still serves one hosted account. | Second seller ChatGPT on another Mac. Sustained session with zero QueuePool errors. Only then add a Machine (token + migration first). |
