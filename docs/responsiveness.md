# Responsiveness and concurrent work

The enduring goals and per-change workflow live in
[Performance goals](performance.md). This document records reference
observations, verification, dated measurements and remaining latency work;
historical results are not current release guarantees.

The web, Mac and iPhone apps share the responsiveness requirements in
[AGENTS.md](../AGENTS.md#responsiveness-and-concurrent-work). Independent drafts
and accounts must overlap; long work must leave navigation, input and unrelated
drafts usable. This is a product requirement, not a promise of infinite hardware
capacity or zero model/network latency.

## Desktop subscription relay verification

The subscription relay test holds 48 simulated model calls open across two
sellers (24 per seller, split between ChatGPT and Claude) while both sellers
read provider settings and save photo-provider preferences. In a local
in-process HTTP run, 20 reads measured p50 3.1 ms / p95 37.5 ms and 20 saves
measured p50 2.9 ms / p95 6.3 ms, with zero errors. Calls and saved preferences
remained scoped to their seller. Run
`pytest -q -s server/tests/test_mac_inference.py -k two_sellers` to repeat it.

These measurements use memory-backed seller settings and simulated pending
inference, not a live model, remote Postgres, or a browser. They establish
that pending subscription calls do not gate ordinary settings requests;
they do not establish the 100 ms input or 200 ms cached-navigation targets.
Live subscription throughput and production interaction latency still need
measurement with connected Macs. The relay tests also cover cancellation,
Claude without ChatGPT, stale connections, and account isolation; hosted
browser tests cover connected/disconnected guidance at desktop and phone sizes.

## References

- [McMaster-Carr](https://www.mcmaster.com/): use its persistent workspace,
  direct search, fast category/filter changes and lack of unnecessary waiting
  screens as the interaction reference. On 2026-11-02, browsing Screws & Bolts,
  Socket, Stainless Steel and searching for washers updated the existing
  document rather than doing a full-page navigation. One initial navigation
  recorded DOM readiness at 603 ms and load completion at 1,224 ms. This single
  browser session is an observation, not a service-level guarantee.
- [NextMaster](https://github.com/stratolark/NextMaster), inspected at commit
  `6db286afbe57c19b7d0be2cb97e929b3f99d58b8`: its README describes precomputed
  page shells with streamed dynamic data; category pages and search links use
  prefetching. Apply the patterns through Anteroom's existing React, query
  cache, native SwiftUI and API boundaries. Do not copy framework choices or
  its disabled type/lint build checks.

## Targets and verification

Use the shared input-acknowledgement and cached-navigation targets in
[AGENTS.md](../AGENTS.md#responsiveness-and-concurrent-work) on supported
devices. Keep already loaded screens available during background
refresh. Do not block a screen on unrelated requests or hold database
transactions during model or remote-storage waits. Record the tested load,
device/build, p50/p95 latency, errors and any missed target. Separate queue wait,
model work and interface response time.

The regression in `vendoo-studio/e2e/concurrentGeneration.e2e.ts` holds two
independent generation responses open, starts a second batch, selects another
draft and types into its editor. It attaches event timing samples and a
screenshot on desktop and phone; CI retains the browser report on success as
well as failure. Event timings below 16 ms are not reported by this observer,
so the attached percentiles describe the recorded samples, not every input.

The local Chromium run on 2026-11-02 recorded 241 samples on desktop (p50 40 ms,
p95 72 ms) and 208 on the phone layout (p50 32 ms, p95 104 ms). The phone p95
missed the 100 ms target. These are sandbox interaction measurements with held
responses, not native-device measurements or real model throughput results.

Backend tests separately verify overlapping jobs across two sellers, cancellation
of one draft while others finish, correct ownership of saved results, graceful
shutdown and Mac inference overlap beyond the former 16-call cap. Native iPhone
bulk changes require an app build and native runtime testing; a phone browser
test alone does not verify SwiftUI behavior.

## Hosted load test, 2026-11-12

`vendoo-studio/scripts/hosted-load-test.py` (how to run it: "Hosted load
test" in `vendoo-studio/README.md`) drives the local hosted sandbox at main
`cd9a9176` (0.1.951). Conditions:

- Web and worker in separate processes (`ANTEROOM_SANDBOX_WORKER_PROCESS=1`),
  each with the production pool (`ANTEROOM_HOSTED=1`: 8 plus 4, 3 s wait), on
  local Postgres 16. The database round trip was either local (about 0) or
  24 ms through the sandbox's delay proxy (`ANTEROOM_SANDBOX_DB_RTT_MS=24`),
  production's measured San Jose to Oregon distance.
- The fake model answered each call after 5 s, so one generation took
  80–140 s.
- Each seller had two browsing tabs (inventory, open a draft, open a
  generating draft, save a listing field, save the notes, suggestions,
  analytics, sales, status; 0.5 s mean pause) and 40 extra drafts. In the
  load phase each seller also kept three drafts generating, each followed by a
  second open stream. 150 s per phase, one run per configuration.
- Host: a shared Linux VM, 6 vCPU (Haswell), 11 GB, also running other
  agents' work; one-minute load 0.3–3.4 during the runs. Processes were not
  CPU-capped: the web process peaked at 0.9 CPU and the worker at 1.6, where
  production gives each one vCPU.

Client-measured full-response time, p50/p95 ms, with the 24 ms round trip:

| Request | 2 sellers, idle | 2 sellers, 6 drafts generating | 5 sellers, idle | 5 sellers, 15 drafts generating |
| --- | --- | --- | --- | --- |
| Status | 173/284 | 177/208 | 178/196 | 195/286 |
| Open a draft (`/screen`) | 375/390 | 384/444 | 387/433 | 425/672 |
| Open a generating draft | 373/388 | 496/593 | 392/439 | 543/722 |
| Save notes | 397/409 | 413/465 | 411/472 | 456/620 |
| Save a listing field | 570/584 | 591/667 | 592/667 | 658/1,110 |
| Inventory | 926/1,340 | 980/1,853 | 961/1,581 | 1,076/4,884 |
| Suggestions | 641/705 | 666/837 | 673/812 | 735/1,079 |
| Analytics | 511/685 | 519/618 | 610/929 | 701/1,052 |
| Sales | 443/560 | 455/569 | 491/884 | 548/884 |
| Samples / errors | 612 / 0 | 612 / 0 | 1,530 / 0 | 1,348 / 2 (inventory 503) |

In the 5-seller load phase, 9 of 39 generate submissions answered 500, 5
generations ended in an error event, and the server logged 24 pool timeouts.
Submission acknowledgement (response headers) was p50/p95 837/4,379 ms; with
2 sellers 946/1,075 ms. The first progress event arrived 6.3–8.5 s after
submission (p50), 4.3 s even with no added latency; this is not yet
explained.

Without added latency, all requests together measured p50/p95 29/68 ms idle
and 38/126 ms generating for 2 sellers (no errors), and 37/101 ms idle and
56/250 ms generating for 5 sellers. The 5-seller run still had 18 pool
timeouts, 4 inventory 503s and 5 generate 500s, and 15 of 44 generations ended
in an error event: pool exhaustion is not caused by distance alone.

Postgres statements per request (`log_statement = all`, idle, no added
latency): status 6, open a draft 13, save notes 14, save a field 20, sales 16,
analytics 18, inventory 8 while its snapshot is current and 34 after a save,
suggestions 7 and 23. At 24 ms a round trip those alone are 140–820 ms, which
is the measured idle latency: at production's distance the floor is set by
round trips, not by contention. No server-confirmed request met the 100 ms
input target, and no editor read met 200 ms. The browser's optimistic
acknowledgement and cached navigation were not measured here.

What held the pool, from the pool's own snapshot at each timeout (printed
with a diagnostic `sitecustomize`, not committed) and `pg_stat_activity`
sampled four times a second:

1. **Nested checkout in `POST /api/conversations/{id}/generate` (web).**
   `routes/hosted_tasks.py` `generate` holds a seller session and `submit`
   calls `get_listing_provider()`, which reads settings
   (`user_settings.read_settings` → `hosted_context.load`) on a second pooled
   connection. When about a dozen submissions overlap, every connection is held
   by an outer session waiting for an inner one, so all wait out the 3 s pool
   timeout. At the timeouts, `generate` held on average 7 of the web
   process's 12 connections for 3.0 s, and 18 of the 24 timeout tracebacks
   were that settings read; the others were page reads in the same window.
   `message` → `enqueue` → `submit` has the same shape.
2. **Long worker transactions in category selection.** With 15 drafts in one
   worker, `services/category_selection.py` `_first_choices` held about 3 of
   12 connections for 3.7 s each, the catalog index's `fingerprint` (rebuilt
   after the worker starts) about 1 for 3.2 s, and `agent_categories.tree_leaves`
   more. Draft lease renewal (`background_tasks.renew`) and progress writes
   (`append_events`) share that pool, time out, and fail their drafts
   (`Draft task failed (TimeoutError at ... heartbeat > ... renew)`).
3. **Round trips per request**, above. Every seller transaction starts with
   `BEGIN` and the seller `set_config`; in the load phase 2.8 connections on
   average sat `idle in transaction` on those two alone.

Also seen: opening a generating draft took 4–5 times as long during
generation without added latency (p50 24 → 111 ms). One Cancel
(`hosted_tasks.cancel` → `background_tasks.cancel`) deadlocked with the worker
finishing the same draft (`deadlock detected ... locking tuple in relation
"background_tasks"`). The two processes used up to 26 database connections.

Not covered: Supabase's transaction pooler, TLS and its connection costs; a
CPU cap like production's; the phone network and the browser; production's
inventory size (about 1,300 drafts for one seller); real model timing and
output. The default `./sandbox.sh --hosted` does not set `ANTEROOM_HOSTED=1`
and therefore opens a new connection per checkout, so its browser tests
cannot show pool exhaustion.

### Submission nesting fix: matched rerun, 2026-11-12

Before: `d9c16317` (0.1.958). After: that commit plus the focused
`hosted_tasks.submit` change: finish the ownership-read transaction before
resolving the provider, whose settings and credential reads use their own
seller sessions. The existing submission lock and ownership checks still guard
the enqueue. No pool, worker, deployment, schema or production settings changed.

Both runs used fresh local hosted sandboxes, in sequence on the same shared
6-vCPU/11-GB Linux VM: split web/worker, the unchanged 8+4 pools with 3-second
waits, 24-ms simulated DB RTT, five sellers, three generating drafts each,
two browsing tabs each, 40 extra inventory drafts each, 0.5-second think time,
5-second fake model calls and 150 seconds per idle/load phase. The command and
environment are the "Hosted load test" example in `vendoo-studio/README.md`,
using API port 4340. No live services, real AI, seller data or credentials were
used. Heavy suites ran only after both loads. The narrow regression finished
before baseline idle; code-health checks overlapped its first 29 seconds.
Temporary browser-library preparation overlapped setup/idle phases. Neither
load phase ran alongside a test suite.

Full-response p50/p95 milliseconds (170 samples per request in each idle
phase; 154 before and 152 after per request under load):

| Request | Before idle | After idle | Before load | After load |
| --- | --- | --- | --- | --- |
| Status | 178/194 | 177/196 | 190/306 | 196/257 |
| Open a draft | 389/540 | 385/427 | 415/557 | 418/646 |
| Open a generating draft | 390/419 | 386/425 | 529/690 | 515/687 |
| Save notes | 413/442 | 412/453 | 450/677 | 446/593 |
| Save a field | 591/637 | 591/632 | 645/1,127 | 632/808 |
| Inventory | 974/1,781 | 966/1,968 | 1,073/2,951 | 1,084/3,308 |
| Suggestions | 665/775 | 674/818 | 722/962 | 729/1,043 |
| Analytics | 615/844 | 617/869 | 651/941 | 668/1,123 |
| Sales | 479/675 | 472/776 | 529/762 | 553/860 |

Before: 1,530 idle requests without errors; 1,386 load requests with two
inventory 503s. After: 1,530 idle and 1,368 load requests, all successful.

| Generation result | Before | After |
| --- | --- | --- |
| Submission attempts / HTTP 500 | 41 / 10 | 31 / 0 |
| Accepted / completed / error event / cancelled at end | 31 / 15 / 1 / 15 | 31 / 12 / 4 / 15 |
| Submission headers p50/p95/max, ms | 1,207/4,508/4,802 | 984/2,519/2,838 |
| First progress p50/p95, ms | 7,953/13,085 | 9,158/13,475 |
| Completed duration p50/p95, s | 131.9/139.6 | 138.2/139.2 |
| Logged web pool-timeout tracebacks (include duplicate reports) | 22 | 0 |

**Remaining worker bottleneck:** the rerun is not clean end to end. Four worker
tasks failed with pool timeouts in `append_events` → `check_write_lease`; one
listing-generation log also reported a QueuePool timeout (not necessarily a
different task). The baseline had one worker generation error. Its 22 web
pool-timeout tracebacks comprise 14 nested provider-settings reports, six
ownership-read reports and two inventory-snapshot settings reports. Each
generation failure was logged twice (application and ASGI server): these are
seven distinct nested-provider failures, three distinct ownership-read failures
and two inventory failures, not 22 distinct exceptions. After: zero of these
web failures. The fix removes web nesting, not
worker contention. The DB sampler still observed category-tree transactions
(mean 0.75 connections before, 0.72 after), but it records last statements,
not the holders' Python stacks or transaction durations. It does not establish
which category-selection function held the connections during these worker
failures. The earlier category-selection finding remains a separate follow-up;
no worker optimization was folded into this fix.

Under load, mean/max CPU cores were web 0.44/0.74 and worker 0.78/1.53 before,
web 0.45/0.74 and worker 0.80/1.81 after. Total DB connections peaked at 26 in
both runs; mean/max transactions/s were 39.54/120.60 before, 40.08/106.80 after.
Host one-minute load mean/max was 2.33/3.54 before and 2.01/3.12 after.

The regression uses a real one-connection Postgres pool and real settings
lookup for both generate and chat submissions, with different provider choices
for two sellers and a foreign-draft denial. Both cases failed before the fix
with the expected nested checkout timeout and passed after it. Provider
construction also opens a seller session, covering the additional checkout an
API-key provider can need. The full hosted-task file passed (7 tests).

These are single-run observations with fresh data and a warmed idle phase,
not production capacity benchmarks. The same TLS/pooler, CPU-cap, inventory
size, device/network and real-model gaps above remain. Query counts were not
remeasured. API headers/full responses do not measure visible feedback or
cached navigation; this run establishes neither the 100/200-ms interaction
targets nor a general latency improvement. Worker failures and first-progress
delay remain, and some read p95s increased.

## Remaining latency work

After transaction pooling was deployed in web release 0.1.683, three sequential
populated-draft reads took 1,043, 1,126 and 1,816 ms in the shared browser. A
20-request browser burst hit a 12-second client deadline. Neither observation
meets the intended responsiveness standard. Removing admission caps must not be
reported as proof that every workload is fast: profile request setup, query
round trips and contention, and retest with simultaneous real generations.

Physical database, device and upstream provider capacity still exists. Handle
backpressure and failures visibly, preserve drafts, allow cancellation and keep
other work usable. Do not replace a removed limit with an undocumented global
serialization gate or hide provider throttling behind a successful status.

The real two-draft test on web/Mac 0.1.686 submitted both drafts, but both lost
their worker leases and failed. Inventory field reads
were CPU-heavy: fetching 1,332 current revision IDs took 364 ms, whereas reading
three JSON fields took 3,850 ms. A seller-scoped field read exceeded a 15-second
statement deadline during the same session. These observations do not establish
a single cause for every timeout.

Hosted `listing_revisions.listing_json` now uses PostgreSQL `jsonb`, avoiding
the repeated full-document parsing described in the
[PostgreSQL JSON documentation](https://www.postgresql.org/docs/current/datatype-json.html).
SQLite keeps its existing JSON storage. A local PostgreSQL comparison using
1,000 synthetic 192 KB forms and six extracted fields recorded three reads per
format: median 306 ms for JSON and 104 ms for JSONB. This isolates storage cost;
it is not a production latency or throughput result. The migration regression
checks large nested forms, Unicode, numbers, nulls, current revision pointers,
quantity history and two-seller row security against the previous schema.

A production stack sample also found the shared background event loop blocked
in an eBay offer scheduler's synchronous database read. That prevented draft
worker heartbeats from running, independently of the inventory JSON cost.
Drafts, automations, offers, watchdog and sourcing now have separate event-loop
threads. A regression holds an offer scheduler in a blocking wait, verifies
draft heartbeats and HTTP health still run, and checks shutdown joins all loops.

Web 0.1.688 and the JSONB migration were deployed on 2026-11-02. Both real
test drafts were claimed concurrently; one saved photo analysis and renewed
its lease, but both ultimately failed. Opening another
draft also produced reads beyond an eight-second client deadline. The full
live generation test has therefore not passed.

A separate regression reproduced Mac control-channel starvation: a blocked
synchronous endpoint occupied the request thread limiter, preventing even the
Mac's authentication, heartbeat, status and reply requests from completing.
Authentication now uses the event loop's executor instead of the synchronous
endpoint limiter, and the in-memory status/reply handlers are async. The test
keeps the endpoint blocked while all three control requests finish. Worker
failures record exception types and source locations without exception text,
SQL parameters or credentials, so subsequent failures can be diagnosed safely.

Web 0.1.690 was deployed after PR #1157 passed its required checks. In the live
retest, both drafts saved photo analysis, Mac status stayed responsive while
another draft was open, and a note edit on that third draft was saved and
cleared. Ordinary editor reads still took seconds, including requests beyond
six seconds. Both generations later lost their leases. The safe worker logs
confirmed `TaskLeaseExpired` while appending durable events.

Draft lease renewal now has its own event-loop thread, shared by the worker's
active drafts, rather than running on the execution loop that makes blocking
database calls. It keeps each draft's seller context, existing lease fencing
and cancellation rules, and stays alive while shutdown drains active work.
The regression blocks the execution loop with two sellers' drafts active and
verifies both leases renew with the correct seller and token. A full passing
live generation retest remains required.

On 2026-11-03, web 0.1.695 was one shared-CPU Fly Machine with 2 GB in San Jose.
About ten minutes after that deploy the one-minute load average was 19 on a
single vCPU, and a later sample was still about 6. Roughly a quarter of the
CPU time since boot was steal; the cgroup throttle counters read as zero, so
that sample does not separate quota exhaustion from host contention. Memory
had about 1.2 GB free. A new transaction-pooler connection took 719–880 ms to
run `SELECT 1`. Reusing the connection took about 70 ms. Session mode and a
direct connection were slower on reuse (about 160–240 ms). The database
reported `max_connections` 60 with 40 sessions already present. Lease renewal
no longer publishes a listing change. A passing live generation retest is
still required before calling concurrent drafting fast enough.

## Sidebar pages open with data in

A phone recording of web 0.1.830 (2026-11-08) showed a placeholder on
Sourcing's wholesale boxes and a page spinner on Marketplaces before content
appeared. Each page asked for its data only when it opened, and `React.lazy`
shows its fallback for a turn on first render even when the page's code is
already downloaded.

Once the inventory has loaded, the app now reads each visible page's first
data (`src/api/pageQueries.ts`) and keeps it for 30 minutes, then loads the
page code one chunk per idle turn. Pages read through the same query
definitions, so their keys cannot drift from the prefetch. A preloaded page
renders directly instead of going through `React.lazy`. Opening a page shows
the cached data and refreshes it in the background.
`e2e/instantPages.e2e.ts` fails if Sourcing, Automations, Marketplaces or
Settings shows a page spinner or skeleton when opened; it fails on the
previous code. This cache is in memory; the hosted app also keeps part of it
on the device across reloads (next section).

## Hosted reloads open on the kept workspace

iOS Safari often reloads a backgrounded tab, and each reload used to wait on
`/api/runtime` behind a startup screen, then on every read. The hosted app now
keeps the workspace's features and an allowlist of account-wide reads
(`src/cloud/deviceCache.ts`: inventory, display settings and the sidebar pages'
reads) in IndexedDB through TanStack Query's persister. A reload shows them at
once and refreshes them from the server. Chats, photos (they have their own
device cache), jobs and listing details are always read fresh.

Only one seller's data is kept. Another seller signing in, sign-out, an expired
session and account deletion erase it; a new build or 24 hours discard it.
Browser storage has a one-second deadline, so a stalled store cannot hold the
workspace closed. `hosted-e2e/deviceCache.e2e.ts` reloads with every `/api`
request held unanswered and still finds the seller's listing, then checks that
sign-out leaves nothing kept and that the next seller sees none of it.

Measured on 2026-11-08 with a production build against the hosted sandbox,
every API answer delayed 500 ms to stand in for a phone network, three runs
each (Chromium; desktop and iPhone 15 sizes):

| | Before | After |
|---|---|---|
| Workspace shown after reload | 1,155–1,219 ms | 462–563 ms |
| Inventory rows, desktop | 2,086–2,182 ms | 556–587 ms |
| Inventory rows, phone (includes ~400 ms of menu taps) | 1,727–1,814 ms | 1,173–1,286 ms |

These are sandbox measurements with a simulated network, not production or
native-device results. Real hosted reads have taken longer than 500 ms (see
above), which would widen the difference.

## Marketplace form loading

On 2026-11-03 the seller measured 12 s to open eBay's form, 8 s for Poshmark,
Mercari and Depop on the web. Every form ran about 60
database queries before answering, one field-registry lookup per saved
marketplace detail. With about 70 ms per reused hosted connection (above), that
alone is several seconds. A form now reads the field registry once (at most six
queries in total; `test_a_form_reads_the_database_a_handful_of_times`).

Following NextMaster's split between a cached shell and per-item data, the
Mercari, Depop and Poshmark forms no longer embed their brand and category
lists (1.5 MB, 326 KB and 245 KB per form, the same for every listing). A row
names its list and version, and clients download each version once from
`/api/catalog/choice-lists/{name}`, which the browser keeps. The eBay form reads
its category rules, policies and item locations together. Forms warm when a
marketplace tab is pointed at. On the Mac, the form route builds in a worker thread so it no
longer blocks other requests.

In the local sandbox (production build, fast network, clicking right after
opening a listing) Poshmark took 0.3 s, Mercari 1.3 s and Depop 1.2 s before
these changes; with a 1.5 MB/s, 80 ms connection and 4x CPU throttling they took
2.5–2.9 s. These are sandbox measurements, not hosted results; retest on the web
after deploy.

Hosted thumbnails read one row instead of three. Each thumbnail URL carries its
photo's version, so the browser keeps it for that sign-in (`private`, `Vary:
Authorization`); every other hosted response stays `no-store`. A dropped
connection or a 429/5xx response is retried twice instead of leaving the row
blank.

That browser copy did not last: `Vary: Authorization` keys it to the sign-in
token, which Supabase renews hourly, and the server's thumbnail cache lived only
in process memory, so every deploy remade each cover from the full photo. On
2026-11-08 an iPad recording showed most inventory covers blank while scrolling,
filling in one at a time. Thumbnails of the sizes the apps use (96 and 192 px)
are now stored in Storage beside their photo (`<photo>.t96.jpg`) and deleted
with it, and the web app keeps versioned thumbnails per seller in Cache Storage
(`anteroom-thumbnails:<seller>`), cleared on sign-out. Not yet re-measured on
the hosted app after deploy.

Inventory rows now warm covers through 80 rows from the start of the rendered
range, with 10 rows behind it retained in the warm-up window. Three downloads
run at a time, using the hosted image cache or the browser cache on the Mac.
Changing the window stops obsolete warm-ups without cancelling a visible
row's shared download. An aborted download can be requested again immediately.
The sandbox regression uses 120 listings and verifies that cover 50 is requested
while fewer than 35 rows are mounted, and cover 119 is not requested yet. It
passes on desktop Chromium, phone Chromium and phone WebKit. This verifies
prefetch behavior, not hosted latency or concurrent-seller p50/p95; those still
need measurement against the hosted service.

## Poshmark size corrections

On 2026-11-03, a Mac sizing-error report led to two reproducible editor defects:
selecting a size could retain a displayed group label instead of the group's
ID, and routine activity refreshes could reset custom-size text before blur.
The editor now emits the resolved group ID for row edits and size-system
changes, and resets local rows only when their saved values change. The sizing
system switch also honors the field's editing lock.

The sandbox browser regressions failed before these fixes. All six correction
cases now pass across desktop and phone layouts, including preflight checks
that no inventory-size gap remains. The full local browser suite passed 28
cases; the Bridge's Poshmark payload suite passed 40 tests. These tests do not
establish the cause of the brother's specific incident, whose listing and error
text were unavailable.

The bundled 747 category paths, 263 size groups and 16,126 size choices were
checked against [Poshmark's public catalog](https://poshmark.com/vm-rest/meta/catalog_v2).
No bundled category or size IDs were missing at the time of the check. This
does not rule out changes elsewhere in Poshmark's listing flow.

## Hosted DeepSeek generation, 2026-11-03

Production 0.1.720 completed a browser run with two concurrent drafts, a third
draft's edit surviving reload, and mobile-web copy surviving reload, with no
HTTP 5xx responses. The workflow took 391 seconds. Generation validation still
reported a missing Mercari shipping-label choice on one draft; the other had
no blockers. This proves saved drafts, not universal publishing readiness or
native Mac/iOS parity.

The selected OpenRouter model, `deepseek/deepseek-v4.1-flash-1017`, advertises
high reasoning by default. A low-reasoning trial on the same release took 339
seconds for the browser workflow, with no HTTP 5xx responses. One draft passed
validation; the other had a provider-call failure during field finishing and
six unverified required fields. Default reasoning was restored. The trial
does not establish a reliable speed improvement. Its GET p95 was 4.77 seconds
across 1,999 requests, so interactive read latency remains unresolved.

Finishing refreshed and captured marketplace forms, then unnecessarily rebuilt
them before filling fields. Filling now reuses the captured definitions while
their category and dependent inputs match. Missing or stale definitions still
refresh, and the final capture remains. The regression reduces four form-load
passes to three for a simple strict fill without changing its validated
answer. Production latency for this change still needs measurement after
deployment.

After the OpenRouter account credit block was cleared, production 0.1.721 saved
two concurrent photo-based drafts in a 248-second browser workflow. Both copies
survived desktop and mobile-web reloads, and a third draft's edit survived
reload during generation. There were no HTTP 5xx responses. GET p50/p95 was
625/3,416 ms across 1,849 requests; input acknowledgement was not measured.

One generation passed validation; the other finished with six unverified
required fields after a provider exception. Its underlying response was not
retained, so the exact provider failure is unconfirmed. A diagnostic retry of
that QA draft's finishing step produced usable replies and reduced the gaps to
one missing eBay size, but did not prove reliable automatic completion.

Plain provider completions lacked the existing streaming recovery for a reply
containing reasoning without an answer. They now retry that case once on the
same model with reasoning disabled for the retry only. Authentication and
credit rejections are not replayed, and evidence validation still rejects
unsupported field values. Deterministic tests cover recovery through the
field-fill collector and validator plus bounded failure. Production validation
of this recovery remains pending.

## Inventory quantity read volume, 2026-11-14

The November 13 quota investigation identified a still-current read in
`services/item_status.py`: every historical quantity was returned to Python,
and status/sold-unit reads discarded the requested ID scope above 500 items.
The production `pg_stat_statements` observation (14,617 calls and 85,569,373
returned revision rows since the October 29 reset) is cumulative, not a rate
or proof of which service caused billed egress. Main already has scalar
inventory projections, first-photo selection and shared hosted inventory reads;
this change retains those, the complete-list API, filters and pagination.

`server/tests/test_inventory_egress.py` uses disposable databases: two sellers,
600 items each, 30 revisions per item, one paid sale and one live listing per
item. It requests 501 items from one seller. Baseline is `d9c16317`; after is the
SQL quantity summary and scoped batches in this change. The same fixture runs
on SQLite and local Postgres 16; the hosted row uses the real restricted seller
session and RLS. ORM result rows are counted after SQL execution, including
empty queries in query counts (not connection setup or RLS setup statements).

| Read | Before rows / queries | After rows / queries |
| --- | ---: | ---: |
| Quantities for 501 explicit IDs | 15,030 / 1 | 501 / 1 |
| Statuses, privileged multi-seller fixture | 38,400 / 4 | 1,503 / 8 |
| Statuses, hosted seller role / RLS | 19,200 / 4 | 1,503 / 8 |

The quantity result's UTF-8 Python tuple representation falls from 761,226 to
5,902 bytes. This is a reproducible logical representation, **not** PostgreSQL
wire bytes, HTTP bytes or Supabase billed egress. SQL still examines scoped
quantity history; returned rows are not scanned pages or CPU consumption.
Batching trades more round trips for retaining the requested scope.

Instrumented single-run observations on this Linux host: SQLite quantity read
127.9 ms before / 137.1 ms after, complete status calculation 417.6 / 172.4 ms;
Postgres quantity read 132.8 / 161.7 ms, complete status calculation 570.8 /
198.4 ms. These include result materialization, instrumentation and new query
compilation. They are not reliable p50/p95 or cold/warm production benchmarks.
The standalone quantity query does more database aggregation; the observed
complete status path improves while transferring much less history.

Sale cutoffs are joined through a small VALUES CTE using the existing
SQLAlchemy dependency. All requested IDs, including unsold items with null
cutoffs, drive an inner join into the revision index. SQLite EXPLAIN confirmed
this removes a cutoff scan repeated for every revision. The regression's
5-million-instruction SQLite budget fails on the earlier left join
(approximately 32,053,000 VM steps) and passes on the indexed join (1,793,000).
This is a database-work proxy, not billed bytes or production CPU measurement.

Regression coverage compares six cutoff scenarios across 60 synthetic item
histories with the previous chronological replay, including corrections,
restocks, invalid/missing quantities, no sales and undated revisions. It also
checks 1,001-item status/sold-unit scopes, partial-sale quantities and cross-seller
RLS. Reproduce the SQLite volume observation with
`.venv/bin/python -m pytest -q -s server/tests/test_inventory_egress.py` from
`vendoo-studio`; use `ANTEROOM_TEST_DATABASE_URL` pointing only to a disposable
local Postgres server for that dialect (the suite creates its test databases).

No production load test or configuration/schema change was performed. Actual
organization usage, per-service bytes and spend-cap state remain unavailable;
this draft code does not clear accumulated quota. Concurrent generation
interaction latency, production sustained CPU/p95 and native iOS latency remain
unmeasured by this focused read-volume test. Phone browser checks, when run,
are browser coverage, not native-device coverage.
