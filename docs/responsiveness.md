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

## Pool resilience, `/screen` collapse, and frontend request scheduling, 2026-11-20

Summary of changes on branch `t3/app-responsiveness-db-timeouts` (on top of `6dcc4bc1`, 0.1.1068):

- **Backend resilience & pool efficiency:**
  - Global `SQLAlchemyError` handler in `anteroom/server/anteroom/hosted.py` logs via `note_cause()` and returns a retryable `503` (`Retry-After: 5`, `Cache-Control: no-store`) instead of an unhandled `500`.
  - Hosted SSE stream polling (`anteroom/server/anteroom/routes/hosted_tasks.py:task_stream`) tolerates up to 3 consecutive transient `SQLAlchemyError` polls before emitting a clean SSE `error` + `[DONE]`.
  - Lazy settings loading (`HostedSettingsStore.read()`) reuses the caller's open seller session with `no_autoflush` (eliminating nested pool checkouts and skipping `user_preferences` reads on routes that do not read settings), while separating generator-yielded FastAPI dependencies (scoped per request context) from direct `with`-block sessions (scoped per owner thread) so sessions are never shared across concurrent requests or worker threads.
  - Function-scoped DB dependencies (`scope="function"`) across hosted routes (except the two streaming CSV exports in `sales_ledger.py`) so pooled connections return as soon as handlers finish.
  - Short-lived TTL cache (`STALE_SECONDS[device] / 3`: 10 s Chrome, 15 s Mac) for confirmed owned device routes in `device_routes.py`, invalidated on drain/reset/release.
- **Fewer round trips per open & save:**
  - `GET /api/conversations/{identity}/screen` (`hosted_workspace.py:listing_screen`) now includes `marketplace_listings`, `native_marketplaces`, and `marketplace_sales` in one seller transaction, collapsing 9 opening browser reads into 1 request.
  - `PUT /api/conversations/{identity}/listing` (`hosted_workspace.py:save_listing`) commits in a single transaction without a post-commit reload.
- **Event loop hygiene:**
  - `listing_promotions.py`, `promotion_automations.py`, and `hosted_promotion_scheduler.py` now run DB work through short-lived `in_worker_session(...)` calls, clearing all remaining entries from `STILL_ON_THE_LOOP` in `test_async_service_sessions.py`.
- **Frontend request scheduling & cancellation:**
  - Two-lane priority request scheduler (`anteroom/src/api/requestScheduler.ts`) starts interactive clicks, navigations, and writes immediately while pausing background prefetches and private thumbnail warm-ups until interactive traffic clears, capping background concurrency at `MAX_BACKGROUND_CONCURRENT = 2`, promoting queued background signals when a real screen mounts, and excluding long-lived SSE/relay streams.
  - Background route prefetches serialize and cancel on navigation or hover leave (`navigationQueries.ts`, `useNavigationIntent.ts`), and opening a draft reads its marketplaces and sales directly from `/screen`.

### Hosted load test, 2026-11-20 (5 sellers, 24 ms DB RTT)

Same `anteroom/scripts/hosted-load-test.py` shape as the 2026-11-12 5-seller run (`ANTEROOM_HOSTED=1`, `ANTEROOM_SANDBOX_WORKER_PROCESS=1`, `ANTEROOM_SANDBOX_DB_RTT_MS=24`, 5 sellers, 40 extra inventory items each, 2 browsing tabs + 3 generating drafts + 1 follower stream per generation, 5 s fake model, 150 s idle + 150 s load) on the same shared 6-vCPU / 11 GB Linux VM (one-minute host load average during the run: mean/max `2.79/3.69` idle, `7.72/11.70` load; web CPU mean/max `0.31/0.56` idle, `0.34/0.60` load; worker CPU `0.14/1.03` load):

| Request | 5 sellers, idle p50/p95 (max) ms | 5 sellers, 15 drafts generating p50/p95 (max) ms | Errors |
| --- | --- | --- | --- |
| Status | 155 / 198 (400) | 164 / 307 (414) | 0 |
| Open a draft (`/screen`, now includes marketplaces + sales) | 474 / 608 (1,337) | 523 / 716 (920) | 0 |
| Open a generating draft (`/screen`) | 473 / 590 (962) | 542 / 751 (1,168) | 0 |
| Save notes | 401 / 514 (718) | 445 / 639 (888) | 0 |
| Save a listing field | 470 / 608 (1,149) | 526 / 732 (907) | 0 |
| Inventory | 788 / 1,636 (3,216) | 873 / 3,018 (4,379) | 0 |
| Suggestions | 704 / 943 (1,208) | 814 / 1,406 (2,219) | 0 |
| Analytics | 560 / 857 (1,528) | 674 / 1,000 (1,589) | 0 |
| Sales | 203 / 355 (531) | 272 / 678 (1,179) | 0 |
| Samples / errors | 1,566 / 0 | 1,440 / 0 | 0 |

- **Zero timeouts or 5xx errors:** 0 HTTP errors across 3,006 browsing requests, 0 refused/failed generations across 19 started (4 finished in p50/p95 `79.4/79.6 s`, 15 still running at cutoff and cleanly cancelled, 0 error events), and 0 `TimeoutError`, `PoolTimeout`, `QueryCanceled`, `IdleInTransactionSessionTimeout`, `deadlock`, `503`, or `500` entries in the sandbox log (compared with 24 pool timeouts, 9 generate `500`s, 5 error events, and 2 inventory `503`s in the 2026-11-12 5-seller 24 ms RTT run).
- **Connections & latency shifts:** Peak DB connections dropped from 26 to 22 (`19.58` mean in load); submit ack p50/p95/max was `1,267/2,435/2,531 ms` (down from p95 `4,379 ms`); `save_field` dropped from `658/1,110 ms` to `526/732 ms` in load; `inventory` load p95 dropped from `4,884 ms` to `3,018 ms`; `sales` dropped from `548/884 ms` to `272/678 ms`. `/screen` itself rose ~90–100 ms (`387/433 → 474/608 ms` idle, `425/672 → 523/716 ms` load) because it now returns `marketplace_listings`, `native_marketplaces`, and `marketplace_sales` in the same transaction instead of 3 separate browser HTTP requests.
- **Caveats (unchanged from 2026-11-12):** Local Postgres without Supabase PgBouncer/TLS overhead, uncapped process CPU on a shared host (note: an earlier run on this host during a transient host-wide load spike of `52.45` experienced an ~83 s VM freeze that triggered 4 retryable `503`s and 0 `500`s/pool timeouts), 40 extra drafts per seller rather than 1,300, and fake 5 s model calls. Cross-region Fly (`sjc`) ↔ Supabase (Oregon) 24 ms DB RTT remains the latency floor for server-confirmed writes and cold reads until compute and database are co-located in the same region.

## Assistant answer stage timing, 2026-11-18

A hosted answer reported as "Worked for 1m 21s" (question to saved answer
80.4 s, from persisted turn times) could not be attributed: Anteroom recorded no
per-stage assistant timing. Each answer now logs one line, `assistant answer
timing:`, with only fixed field names and whole milliseconds: `outcome`,
`queue_wait_ms` (hosted task creation to the worker starting it, `-` on the
Mac), `prepare_ms` (shop read and prompt), `rounds`, per-round
`first_token_ms` (first piece including thinking, `-` when none arrived) and
`round_ms`, `lookups`, `lookup_ms`, `save_ms` and `total_ms`. Values are capped
at 600,000. The timing message contains only fixed labels and numeric timing
values: no question, answer, SQL, provider text, URLs or tokens. This describes
the message, not the full operator report: existing alert enrichment may attach
seller identifiers and request/environment metadata. That policy is unchanged.

`total_ms` starts when `_answer` starts and ends after its save/cleanup wait;
queue wait is separate. First-token time is a point within its round, not an
additional duration to sum. Round time includes local processing and streaming
handoff while consuming the provider, so it is not pure provider/network time.
Preparation and save measure their awaited intervals, including thread dispatch;
on cancellation a thread may still finish after the await ends. Parsing,
proposal processing and cleanup can leave time outside the named stages.

Hosted timing is emitted once after journal settlement/cleanup so journal and
heartbeat stop reasons can distinguish seller Stop (`cancelled`), lease loss
(`lease_lost`) and ordinary shutdown (`interrupted`). The final message's commit
confirms its answer outcome synchronously, before the post-commit row refresh;
earlier memory/thread commits and the pre-commit settle callback do not confirm
it. Unfinished work is still cancelled during cleanup, including after settle.
A thread that commits only after the sample is emitted remains unconfirmed in
that sample. A confirmed save retains its answer outcome
despite a concurrent lease revocation. `total_ms` ends with the answer's cleanup
wait, not the later journal settlement or log emission.

Answers under 30 s log at INFO, which hosted logging drops; slower ones log at
WARNING and, when operator alerts are enabled, enter the hourly batched operator alert email (grouped by call site,
as `Slow response` lines are). The digest retains only the latest sample in each
group. Hosted workers also retain individual warnings in the existing local
`logs/anteroom.jsonl` diagnostic sink under the configured data directory. Once
the file exceeds 512,000 bytes and has more than 2,000 entries it is trimmed to
its last 2,000 entries; this is a rolling entry limit, not a 512,000-byte hard cap. Messages
are capped at 4,000 characters. Operators can read this worker-local file; it is
not shared across Machines and can be lost with the filesystem. Disk logging is
best effort and does not change alert delivery. No stderr handler is added.

`queue_wait_ms` compares the worker clock with the database's `created_at`;
skew is clamped at zero, not corrected. Time between `total_ms` and the
follower seeing `[DONE]` (journal polling) is not included. This line measures;
it changes no answer behavior and proves no cause by itself.


## Assistant shop read: shared range inputs, 2026-11-18

Seller report: a hosted assistant answer ("What is selling best the last two
months?") showed "Worked for 1m 21s". Its persisted turns, read seller-scoped
without replay, were saved 06:03:21.005Z and 06:04:41.426Z UTC: 80.4 s from
question to saved answer. That span covers queue wait, the shop read, every
model round and lookup, and the save. At the time, Anteroom recorded no per-stage assistant
timing, and the task row and journal were not reachable, so how the 80.4 s
divides between them is unknown. The worker Machine was shared-CPU, 1 vCPU.

Profiling the shop read found range-independent work repeated per range. Each
of the eight Analytics ranges re-read sale events, ad spend and the full sale
ledger (with the imported-sales archive check) and rebuilt the incomplete-sales
list; box results reloaded every listing a second time. These are now read once
per question (`inventory_analytics.analytics_ranges`, `boxes.results(rows=)`).
The brief and every lookup row are identical on the same data.

Local synthetic benchmark (SQLite, one process, this VPS, uncached read, median
of 5 after a warm-up; 50% sold listings):

| Listings | Before | After | SQL statements before → after |
|---|---|---|---|
| 300 | 449 ms | 172 ms | 53 → 24 |
| 1,500 | 2,066 ms | 881 ms | 69 → 32 |

These are local CPU and statement counts, not production latency. Hosted
statements each cost a Postgres round trip (about 24 ms measured 2026-11-11),
and the shared-CPU worker may run slower than this machine. This does not
establish what part of the reported 80.4 s the shop read took; model rounds,
lookups and queue wait were unmeasured for that answer.

## eBay refused sign-in status, 2026-11-16

An isolated saved-account fixture reproduces Settings reporting connected after
an explicit refresh refusal. The fix remembers the refusal on that exact grant:
two subsequent token requests make one mocked refresh call rather than two.
Status remains a local read; opening Settings does not validate or refresh the
grant. Expiry alone does not mark a refreshable account invalid. A new sign-in
replaces the record, and a late refusal cannot invalidate that newer connection.
Relay authentication failures and temporary outages do not mark a seller grant
invalid. This changes the shared eBay/Etsy account service, not marketplace
write receipts, revision fences or unknown-send handling.

These are deterministic fixture counts, not production latency measurements.
Desktop and phone browser checks use intercepted account status in the offline
sandbox. Concurrent seller generation and input/navigation p50/p95 are not
measured for this fix; no capacity claim is made.

## Mercari explicit preview cooldown, 2026-11-16

An isolated two-listing scheduled-offer fixture reproduces the refusal reported
in fingerprint `45ba8ce0227a`: the first listing returns the explicit 47-hour
eligibility wait with no like count; the second remains eligible. Against the
original implementation, the first round reads both listings and the second
round reads the refused listing again, logging the same refusal twice at error
level. With the fix, the first round still reads both and the second reads
neither: the refused listing has a durable preview wait, while the completed
listing retains its existing send guard. The other listing's normal path is
unchanged. A separate manual-preview fixture discards ORM identity state,
checks the wait through 47 hours 59 minutes, and permits a fresh preview at
48 hours (the reported 47 hours plus a rounding allowance), without sending.
The added lookup only considers skipped reviews completed within 73 hours;
existing all-history unknown/sending guards remain intact.

A later email on release 0.1.974 exposed another reporting layer: the Bridge
reported the same explicit cooldown at ERROR before promotion review classified
it. A focused regression now runs the real Bridge reporter and promotion review
with only the underlying transport stubbed. Both layers retain the refusal at
INFO, below the actual alert handler's threshold. One shared classifier handles
only direct Mercari offer-preview refusals; nested replies do not inherit that
classification from a parent's operation/action or retry-safety context. This
changes reporting severity for the recognized wait, not requests or sends.

These are deterministic fixture request counts, not live Mercari validation
or latency/capacity measurements. The regressions cover listing/action/market
scope, new-like non-bypass, malformed refusals and unknown/sending precedence;
the hosted regression uses the same remote item ID for two local test sellers.
Browser input/navigation p50/p95 and concurrent generation under seller load
remain unmeasured for this focused backend change. No production access,
marketplace requests or configuration changes were made.

## Recent promotion history index, 2026-11-15

The waiting-for-likes lookup filters by automation, marketplace, action and
creation time, then orders by creation time. An isolated 30,000-row synthetic
journal (two rules/sellers, 6,753 recent results per rule, remaining rows 30 days
old) compared the existing single-column indexes with an additional index on
`(automation_id, marketplace, action, created_at)`. The unchanged lookup was
fully fetched 32 times per phase; the first two samples were excluded. Warm
local full-fetch p50/p95 milliseconds on this shared Linux host were SQLite
20.47/22.36 before and 11.65/16.58 after; Postgres 16 10.75/14.31 before and
9.72/13.22 after. SQLite replaced its temporary sort with an ordered range
scan. Postgres used the composite index for its bitmap scan, retaining the
sort of the 6,753 results. Neither planner was forced to use an index.

These sequential fixture measurements establish a narrower history scan,
not production latency, concurrent seller capacity or the cause of the single
2026-11-15 hosted statement cancellation (fingerprint `04ed9d63841c`). Its
parameters, timeout threshold and wait state were unavailable. Completed
production statements supplied separately averaged 16.6 ms; their statistics
do not include the canceled call. The lookup still returns every recent check
and reads prior successful offers; no history is capped or discarded. Both
queries and their existing timestamp-only ordering remain unchanged, including
the lack of a defined winner between conflicting rows with equal timestamps.
The migration regression covers an existing journal on SQLite and Postgres,
waiting results, cutoff boundaries, null likes, unknown sends and ties with
the same waiting outcome. A separate regression runs 40 conflicting-tie cases
through the real waiting lookup, blocking lookup and offer round: unknown or
sending rows tied with skipped (below/above the minimum), done (expired
cooldown), failed or preview rows, in both insertion orders, with and without
the index. Every case must keep both journal rows and avoid marketplace review
and send entirely. The all-history blocking lookup checks for any unknown or
sending row independently of which tied row the waiting lookup encounters
first. Live concurrency and interaction latency remain unmeasured for this
change.

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

The regression in `anteroom/e2e/concurrentGeneration.e2e.ts` holds two
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

`anteroom/scripts/hosted-load-test.py` (how to run it: "Hosted load
test" in `anteroom/README.md`) drives the local hosted sandbox at main
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
environment are the "Hosted load test" example in `anteroom/README.md`,
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

### Worker contention fix: checkout evidence and matched rerun, 2026-11-12

This follows the submission fix above, using `8bf58756` as the worker baseline.
The root cause was repeated category CPU work delaying connection returns:
full-tree ORM hydration, repeated whole-word normalization during cold catalog
search, and full ranking-descriptor construction even for an exact native
category match. Progress writes, lease checks and saves then competed for the
same twelve worker connections. This finding comes from checkout and live
Python stacks with measured hold ages, not from category-table presence in
`pg_stat_activity` alone.

Temporary sandbox-only instrumentation recorded each successful checkout's
code stack, thread, monotonic duration and cursor timing, and sampled current
holder stacks every 250 ms while the pool was busy. Each actual pool exception
recorded its waiter and holders once. It recorded code locations and timings,
never SQL parameters, frame locals or seller data; the instrumentation and raw
logs are not shipped. Cursor timing includes client scheduling and simulated
RTT, so it is not a measurement of Postgres execution alone.

The baseline reproduced four generation error events and **five distinct
worker pool checkout failures**: four in `append_events` → `check_write_lease`
and one in revision persistence. There were no web pool failures. At the first
three worker failures, five `tree_leaves` checkouts had been held for
3.00–3.08 seconds; four fenced writes, two progress writes and a save occupied
the other seven slots. At the later failures, four leaf readers had been held
for 3.37–3.47 seconds. Live stacks also caught repeated
`_whole_word_score`/`_singular_token` work during category selection.

An intermediate projection-only run still failed: its timeout snapshots had
seven category-selection readers held for 3.5–3.9 seconds plus snapshot reads.
After caching lexical normalization, a short diagnostic run caught native
`match_row` → `_parsed`/`_alternatives` work on the event loop while leaf-reader
connections were held for 2.64–2.65 seconds. An isolated exact eBay match built
18,186 ranking descriptors before returning its unique exact match. These
observations explain why reducing returned database rows alone was insufficient.

The focused changes select only terminal category IDs/paths, reuse pure
immutable word normalization in bounded caches, and check the existing exact
normalized key before constructing fallback ranking descriptors. The public
seed has 42,720 leaves but 27,606 distinct paths; an 8,192-entry normalization
cache churned on each large-market traversal. A 32,768-entry cache retained the
working set. An isolated three-traversal probe retained about 28.7 MiB under
`tracemalloc`; this is not total worker RSS. No seller rows, sessions, category
results or settings are cached. Existing matching, ambiguous ties, seller
ownership, fenced saves, leases, cancellation and progress semantics are
preserved. Pool sizes/timeouts and concurrency controls are unchanged.

Both matched runs used fresh, unique temporary data paths and checked-unused
private loopback ports before launcher setup. They ran sequentially on the same
shared 6-vCPU/11-GB Linux VM, with no heavy test suite during either load:
split web/worker, default hosted 8+4 pools and three-second waits, 24-ms simulated
DB RTT, five sellers, three generating drafts and two browsing tabs per seller,
one follower stream per generation, 40 extra inventory drafts per seller,
0.5-second think time, five-second fake provider calls and 150 seconds per
idle/load phase. The harness command is the README's hosted load example with
`--sellers 5 --seconds 150 --inventory 40`; only the private port, data paths and
PID/database sampler arguments differ between runs. No live services, real AI,
credentials or production configuration were used.

| Generation / pool result | Before (`8bf58756`) | After worker fix |
| --- | --- | --- |
| Accepted / completed / error event / cancelled at end | 31 / 12 / 4 / 15 | 30 / 15 / 0 / 15 |
| Submission failures | 0 | 0 |
| Distinct worker / web pool checkout failures | 5 / 0 | 0 / 0 |
| Submission acknowledgement p50/p95/max, ms | 997/2,706/3,168 | 1,002/2,433/2,472 |
| First progress p50/p95, ms | 9,374/12,772 | 7,720/16,508 |
| Completed generation p50/p95, s | 141.9/142.9 | 130.3/132.0 |

Both runs had 1,512 idle requests and 1,350 load requests with **zero browsing
or save errors**. The replacement generation jobs still running at the timed
end were cancelled by the harness, not counted as generation failures.
Full-response p50/p95 milliseconds under load (150 samples per request):

| Request | Before | After |
| --- | --- | --- |
| Status | 194/284 | 192/264 |
| Open a draft | 421/608 | 407/578 |
| Open a generating draft | 550/720 | 553/743 |
| Save notes | 441/605 | 454/658 |
| Save a field | 637/920 | 643/834 |
| Inventory | 1,091/3,566 | 1,048/2,831 |
| Suggestions | 739/1,140 | 758/1,131 |
| Analytics | 721/1,124 | 727/1,062 |
| Sales | 561/936 | 542/1,015 |

Completed worker checkouts lasting **at least 0.5 seconds**, mean/max seconds
and count, over the complete capture including cancellation/drain:

| Checkout site | Before | After |
| --- | --- | --- |
| `tree_leaves` | 3.155/6.202 (25) | 1.670/4.587 (26) |
| `_first_choices` | 4.768/10.778 (31) | 3.136/6.678 (30) |
| Catalog fingerprint/snapshot | 3.970/4.285 (5) | 1.879/2.187 (5) |

These thresholded durations are not percentiles of all checkouts. Long holds
remain, including form recording (after mean/max 2.688/5.740 seconds for 47
checkouts above the threshold). Zero pool failures in one run does not prove
headroom for other items or larger/longer loads. First-progress p95, notes p95,
generating-screen p95 and sales p95 increased.

Under load, mean/max CPU cores were web 0.47/0.71 and worker 0.79/1.38 before,
web 0.47/0.71 and worker 0.75/1.75 after. Total DB connections peaked at 26 in
both runs; mean/max transactions/s were 40.01/121.70 before and 40.84/163.50
after. Host one-minute load mean/max was 2.33/3.51 before and 1.95/3.18 after.
The after run's busy-period sampler observed main-worker RSS as high as
1,817,584 KiB (about 1.73 GiB); it did not sample continuously or measure a
matched baseline RSS. Memory under a production resource cap remains unproven.
The final sampled lexical cache had 418,226 hits, 18,190 misses and 18,184
entries, showing reuse rather than storing a category result per seller.

The regressions fail on the original code: leaf reads hydrate 1,004 ORM nodes,
three taxonomy searches tokenize 60,000 paths instead of 20,000, and an exact
1,001-row native match builds 1,002 ranking descriptors instead of zero. The
same tests pass with the fix, including distinct-market filtering, terminal
flag semantics, inflection/whole-word matching and ambiguous exact ties.

These are single-run sandbox observations. Shared-host scheduling, no Fly CPU
cap, local Postgres without TLS/Supabase pooling, small inventory, repeated
fake item/provider output and no real device/network limit their scope. API
response latency does not establish visible acknowledgement within 100 ms or
cached navigation within 200 ms. Native iPhone validation requires macOS;
browser suites exercise phone-sized Chromium and iPhone WebKit instead.

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

## Imported-sales archive marker connection ownership, 2026-11-16

A bounded local hosted replay on `075f557b` ran 58 reads against disposable
Postgres with signed synthetic seller sessions and the unchanged 8 + 4 pool,
3-second checkout wait. Two sellers had 40, then 600 items each, one revision,
sale and live record per item, small settings and the bundled category tree.
At most four requests overlapped. All reads succeeded in the 26.79-second run,
with a maximum of five connections checked out. This did not reproduce the
production preflight timeout or identify the cause of its twelve holders.
The replay was in-process ASGI, without Supabase/TLS, added RTT, generation,
schedulers or a CPU cap; client JSON parsing shared the Python process.

It did identify a concrete extra checkout: boxes first reads its source boxes,
then checks the imported-sales archive marker. Shared `get_db` routes do not
preload settings like `seller_session` does, so the marker's settings read
opened another session while the boxes transaction still held its connection.
Every one of the eight boxes samples entered that settings load with an
existing same-request holder. Deterministic real-database regressions on
unchanged code observed two simultaneous checkouts without changing pool
capacity or forcing saturation. Both autoflush modes reproduced it; hosted
Postgres reproduced it for saved markers and first-run archival.

The archive helper now loads the execution's settings snapshot through its
supplied session. The marker read suppresses autoflush so pending edits remain
the caller's responsibility. The regression checks one simultaneous connection,
settings memoization, unchanged transaction identity and rollbackable pending
inserts/updates, plus two sellers' actual hosted boxes responses and durable
first-run markers. Archive commit and marker-write ordering remain unchanged.

The earlier replay separately observed connections retained during pure
overview/ledger calculations. That transaction-boundary work is not changed
here. Cursor execution, ORM materialization, pure CPU, checkout and transaction
spans are different measurements, not interchangeable explanations for a slow
query. No production timeout resolution, p50/p95 interaction improvement or
concurrent-generation capacity is established by this narrow fix.

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
`anteroom`; use `ANTEROOM_TEST_DATABASE_URL` pointing only to a disposable
local Postgres server for that dialect (the suite creates its test databases).

No production load test or configuration/schema change was performed. Actual
organization usage, per-service bytes and spend-cap state remain unavailable;
this draft code does not clear accumulated quota. Concurrent generation
interaction latency, production sustained CPU/p95 and native iOS latency remain
unmeasured by this focused read-volume test. Phone browser checks, when run,
are browser coverage, not native-device coverage.

### Assistant asking-price history: compact revision projection (2026-11-14)

The hosted post-deploy Assistant attempt failed before a model call with
SQLSTATE 57014 (statement timeout), in `_price_changes` at 19:19:26.652 UTC.
The query extracted price from every full archived `listing_json`. It now reads
that same JSON value from the existing database-generated `inventory_fields`
column. Every revision remains included, ordered by creation time and ID;
chronological replay and invalid-price handling are unchanged. No pagination,
latest-revision filter, timeout increase, migration or production setting change
is involved.

`test_assistant_price_history.py` compares old/current semantics, including
changed and unchanged multi-revision listings, invalid/missing/zero/negative,
boolean and numeric-string prices, and history exceeding the prompt summary.
Existing `test_inventory_projection.py::test_upgrade_computes_existing_fields_and_sql_writes_keep_them_current`
covers legacy-row backfill, direct SQL updates and imported generated-value
removal; new history fixtures exercise ORM inserts. The stored generated column
computes price at the database boundary for every insert/update, independent of
which application write route was used.

A disposable local Postgres fixture contains two sellers, 80 listings each,
20 revisions per listing (3,200 total), and 1,468,725-byte synthetic marketplace
form payloads. Queries execute as `anteroom_seller` without BYPASSRLS. Exact
expected histories and cross-seller exclusion are asserted. EXPLAIN ANALYZE
BUFFERS measured the former query at 312.3 ms / 10,489 shared buffer accesses,
and the compact query at 5.6 ms / 65 accesses. The regression gates physical
work (at least fourfold reduction), not timing on shared hardware. Before the
fix it failed this gate and the query-input assertion; afterward both pass.

With four concurrent history readers across two sellers and a fifth task
performing 20 seller-scoped reads/writes, history p50/p95 was 71.2/102.2 ms
(20 samples), normal read/write p50/p95 19.4/101.2 ms (20 samples), errors zero.
Each history result contained all 240 expected changes for its seller, and each
read released its transaction. The first application history read after seeding
was 32.5 ms. These were measured with an en_US Postgres locale. Expected rows are checked
independently of ID collation; exact old/current list equality verifies that
ordering remains unchanged. An initial CI test expectation assumed Python
string ordering; its failure was reproduced locally under en_US and corrected.
These are local, warm-cache observations, not production or cold
storage benchmarks. They do not reproduce the hosted 15-second deadline or
measure full Assistant/provider latency, browser interactions, sustained CPU,
production data cardinality or native iPhone behavior. No additional live
question was submitted; hosted resolution remains unverified.

## Offer activity under concurrent work, 2026-11-16

Measured product source `e1fdd35da790a565ddd71884f463bcd565bb5384` on the
shared Linux VM (6 Haswell vCPU, 11 GiB RAM), using the hosted sandbox's
throwaway Postgres, separate web/worker processes, unchanged production pools
(8 plus 4 per process), 24 ms simulated database RTT and a fake model with a
1-second delay per call. No other heavy worker suite ran concurrently. This
was the Vite development build, Chromium 1254, desktop 1440x900 and iPhone 15
browser emulation; it was not a physical phone or native latency measurement.

The existing `anteroom/scripts/hosted-load-test.py` supplied authentication,
independent draft setup, normal reads/writes, generation/follower streams,
cancellation and system sampling. A local adapter added one real
`GET /api/analytics/offers` per browsing cycle: seven inclusive UTC days,
40,000 synthetic terminal journal rows (20,000 per seller), four response rows.
It seeded no enabled offer rules and dispatched no marketplace actions.
Two sellers each had two independent API browsing tabs, two generating drafts
in the load phase and 20 extra inventory drafts; two additional authenticated
browser contexts repeatedly selected periods and navigated. There were four
generations plus four follower streams during load. Each phase lasted 60 seconds,
with 0.2-second mean API think time. This is a bounded concurrency observation,
not a capacity limit or a sustained-load benchmark.

The corrected run reused the first run's synthetic drafts and journal. Its
700 normal/offer HTTP samples had no errors or timeouts. Client full-response
p50/p95 milliseconds (36 samples per request idle, 34 under load):

| Request | Idle | Four drafts generating |
| --- | --- | --- |
| Offer activity | 138/166 | 142/189 |
| Inventory | 982/1,208 | 1,081/2,040 |
| Open a draft | 400/454 | 407/500 |
| Save a listing field | 607/674 | 630/713 |
| Save notes | 450/870 | 438/546 |
| Status | 177/199 | 194/232 |

Eight generations started, four finished and four were intentionally cancelled
at phase end; none ended in an error event or failed submission. Submission
acknowledgement p50/p95 was 787/1,005 ms and first progress 7,728/9,305 ms;
these are separate from input feedback. All four edited drafts retained price
and notes on a fresh read, and each seller's attempt to read the other's draft
returned 404 (ten post-load checks passed). Sandbox logs retained disconnected
marketplace schema refusals and fake-provider parsing warnings; no pool timeout
was found. These are not marketplace/model quality results.

Browser timings start at the DOM click and finish on the next animation frame
that contains the selected period label or cached offer rows. They exclude
Playwright command transport and do not time an uncached server response.
Every measured period selection changed Today to This week. The corrected
driver recorded 554 observations, no page errors or driver failures; first
page/sign-in and phase-end observations are excluded from these percentiles:

| Journey | Desktop idle p50/p95 (n) | Desktop load p50/p95 (n) | Phone idle p50/p95 (n) | Phone load p50/p95 (n) |
| --- | --- | --- | --- | --- |
| Period input to frame | 28/49 (57) | 30/45 (65) | 54/83 (29) | 59/75 (33) |
| Cached Analytics → Automations | 99/141 (57) | 99/136 (64) | 91/144 (28) | 99/122 (33) |
| Reference: Automations → existing Analytics tabs | 150/227 (57) | 152/225 (64) | 118/161 (28) | 122/157 (33) |

The feature journeys met the shared 100 ms input / 200 ms cached-navigation
targets at these observed p95s. The existing Analytics reference journey missed
200 ms on desktop; its implementation is outside this diff, and this measurement
does not establish the cause or waive that gap. The first driver attempted to
return through Settings, which replaces desktop Pages navigation, and timed out
before desktop timing. Its valid phone Settings → Automations observations
also missed 200 ms (idle/load p95 251/265 ms). Those results are retained, not
combined with the corrected Analytics journey or described as a product fix.
That first API run separately had 760 error-free samples, including 76 offer
reads (idle/load offer p50/p95 128/143 and 142/165 ms).

Offer JSON bodies were 770 bytes, excluding headers/transport overhead. Actual
seller-role `EXPLAIN (ANALYZE, BUFFERS)` with the API's `period_bounds` UTC
timestamp arguments confirmed two grouped repository SELECTs using both new
seller/send-date indexes with seller and lower/upper date index conditions.
They returned two/four aggregate rows in 2.1/2.7 ms execution time, with warm
buffers; the endpoint did not hydrate journal history. This measures local
database execution separately from authenticated API latency and billed egress.

Raw samples, corrected and failed driver results, query plans and logs were
retained locally in `/tmp/offer-responsiveness-e1fdd35d/` (`attempt-1/` preserves
the first run). First API reads were already database-warm; single first-page
observations included sign-in/dev module loading and are not cold-cache p95s.
There is no pre-feature build baseline, slower-device/network benchmark, native
input p50/p95, production performance result or proof of unlimited concurrency.
Idle versus load compares concurrent work, not a before/after feature speedup.
The explicit measured target misses above remain follow-up gaps. All owned
browser/server/worker/Postgres processes were stopped and the disposable database
removed before releasing the slot to the next worker.

## Offer-preview read diagnostics, 2026-11-16

Mercari minimum-price and unavailable emails and Depop product-not-found emails
on 0.1.972–0.1.974 identified the overall preview but omitted its failed read.
Local refusal fixtures now retain one fixed read-step label through the Bridge,
promotion review and email sanitizer. Mercari fixtures inject each observed
message at item, eligibility and bounds reads; every GraphQL body contains only
`itemId` variables and no create-offer operation occurs. Depop fixtures place a
404 at account, product and send-all-preview reads; all requests remain GETs.
No request, retry, price computation, listing state or send guard changes.

These bounded fixtures establish diagnostic retention and unchanged request
counts, not the underlying live marketplace cause, sale status, production
latency or concurrent seller capacity. No production, credentials or marketplace
were accessed. Browser/native interaction p50/p95 and concurrent generation
remain unmeasured for this diagnostics-only change.

## Depop multi-size preview reporting, 2026-11-16

The supplied Nov 16 emails repeat the local guard refusing offers on listings
with several sizes at both transport and review layers. A fixture reproduces
both ERROR records, then verifies both become INFO for only the exact direct
Depop offer-preview refusal with `ok=false`, `error_code=rejected` and
`safe_to_retry=true`, without conflicting failure indicators. The visible
refusal, result, request count and eligibility checks remain unchanged.
Unknown sends, other actions, other marketplaces and nested replies retain
ERROR reporting. The hosted relay runs through the same classifier.

No durable gating is added: a seller can change a listing's sizes, and the
existing preview journal does not establish when that change invalidates a
multi-size refusal. Future checks therefore still read fresh facts. This
change does not claim reduced marketplace calls or fix minimum-price/404
refusals. There were no production/credential/marketplace reads or mutations.
Browser/native p50/p95 and concurrent seller latency remain unmeasured; these
bounded fixtures measure severity and unchanged request counts only.

### Account and subscription readiness copy (2026-11-18)

Provider authentication on the local Mac, its Anteroom account session, and
account-scoped hosted relay readiness are separate states. The local account
entry point now offers account navigation before any explicit listing-copy
flow; opening the guidance reads only the configured destination URL. It does
not read listing-copy status or start a session. Navigation occurs only after
the seller chooses Open My Account. Status handlers and hosted provider metadata
selection leave an absent relay absent; actual requested operations establish
relay state as before. Existing ten-second hosted Settings polling is unchanged.
The hosted ready note reports the last check, rather than promising the next
request will succeed. Photo reading may use a different configured provider.

Bulk measurement errors retain photos and typed details and offer manual entry.
Forwarding errors name the connection that could not be reached without claiming
an unknown sign-out, sleep or offline cause. No timeout, capacity, credential
storage, login/handshake or marketplace-action behavior is changed.

Design follows descriptive errors and preserving seller effort from WCAG
[Error Identification](https://www.w3.org/WAI/WCAG22/Understanding/error-identification)
and NN/g [Visibility of System Status](https://www.nngroup.com/articles/visibility-system-status/),
using existing Anteroom dialogs, buttons, notes and theme tokens. No new visual
system or automatic navigation is introduced. Local status/model requests and
production testing are out of scope. Concurrent-load p50/p95, interaction latency,
Mac native behavior and desktop/phone screenshots remain pending the coordinated
VPS validation slot; this note makes no production-readiness claim.

### Inventory read-step diagnostics — 2026-11-17

The deployed 0.1.1009 samples supplied by the coordinator place most elapsed
inventory build time in `build_db_ms`. This is the transaction-bearing phase,
including network, fetching/ORM, Python and transaction teardown, not summed
server SQL execution time. Zero queue/wait fields and endpoint pool snapshots
do not establish the absence of transient waits or identify a database cause.

The next diagnostic adds fixed `inv_<step>_ms` and
`inv_<step>_orm_calls` fields to the existing slow inventory response line:
analytics, reconcile, conversations, covers, listings, direct, sales, unsent and
assemble. The observer is local to the build's session and each step; it reads
no statement text, parameters, identifiers, row contents or exception text and
issues no query. It counts ORM execution attempts, including failed attempts,
not all driver round trips or autoflush SQL. Counts saturate at 10,000 and
elapsed milliseconds at 600,000; a saturated value is a lower bound. Step wall
time includes work inside that block, not just database execution. Work between
blocks and connection close is outside these step totals. Existing coarse
build fields are unchanged. Cache hits report zeros; peer requests can report
the shared build's measurements. A build that raises before completion has no
completed-build measurements. No extra per-query log or global engine listener
is installed. No performance improvement, production root cause or p50/p95
claim follows from these counters.

Verification uses deterministic clocks, synthetic private-looking parameters,
failure cleanup, independent threaded sessions, cache-hit reset and the real
inventory-response projection test. Production replay, database queries,
query optimization and load benchmarks are outside this diagnostic's scope.
Full selected local validation and hosted/Postgres coverage remain required
before release; focused SQLite coverage alone is insufficient.

### Suggestions and Chrome reply diagnostics — 2026-11-17

This follow-up to the inventory-step diagnostic adds fixed `sug_<step>_ms`
and `sug_<step>_orm_calls` fields for preferences, covers, photo_counts,
listing_rows, direct, conversations, busy and ranking. It reuses the same
session-local observer (now named `snapshot_step`) and preserves queries,
connection release before ranking, output and cache behavior. Preference
settings use a separate hosted session: its elapsed work is measured, but its
SQL is not counted by the observed suggestions session; zero ORM calls there
must not be read as zero database work. Other limits of the inventory counters
apply, including fixed labels, saturation and no server SQL attribution.

For `/api/browser-relay/replies`, fixed `relay_lookup_ms/calls` and
`relay_persist_ms/calls` fields time the existing worker awaits around route
lookup and diagnostic persistence. Calls are zero or one; milliseconds saturate
at 600,000. Elapsed time includes scheduling/network/driver/Python work and,
when cancelled, ends when the await is cancelled, not when its existing worker
later finishes. Replay, missing-reply, absent-timing and error paths retain
their original behavior. Persistence still precedes reply resolution; its
SQLAlchemy errors are still swallowed, other failures/cancellation propagate.
No new timeout, retry, task, query, log line or external call is introduced.
These are diagnostic observations, not a speed fix or a common-cause claim
about the supplied suggestions/jobs/relay samples.

The suggestions parity fixture requires a known validation card and compares
results and query counts with observation active versus inactive in the current
implementation; it does not compare against the previous implementation's query
sequence. Other fixtures cover snapshot cache reset, reply branch ordering,
error propagation and actual awaiting-task
cancellation while its original worker finishes. Full selected local checks,
Postgres/hosted/browser coverage and timing-overhead/load measurements remain
unverified until separately coordinated; no production replay was used.

### Direct marketplace form loading — 2026-11-20

Observed bottlenecks when opening marketplace tabs (eBay, Mercari, Poshmark,
Depop, Etsy), especially on hosted (`myanteroom.com`) and touch devices:

- Hosted `GET /conversations/{id}/native-marketplaces/{market}/form` and
  `POST .../form/refresh` were sync `def` handlers taking `get_seller_db` and
  running `asyncio.run(build_form(...))`, holding a pooled Postgres connection
  across external marketplace waits and bypassing `shared_http`'s keep-alive
  client on the server loop.
- `ebay_form` ran aspects → conditions → variations sequentially, called
  `EbayClient.connect()` up to three times before checking cache, and opened
  four to five nested `SessionLocal()` checkouts on warm reads.
- `mercari_form.editor_form` ran two uncached HTTP calls per open with a fresh
  `httpx.AsyncClient` even for identical `(category, ounces, dimensions)`
  packages.
- Local form builders deep-copied static catalogs (`poshmark-native-form.json`,
  28,853 Mercari brands) and `choice_lists.share` deep-compared them just to
  strip them out.
- `studioFetch` (`cache: "no-store"`) prevented browser HTTP caching of
  versioned public choice lists (>1.5 MB) across page reloads, `nativeFormOptions`
  embedded the raw `_native_forms.<tab>` object in TanStack Query's key, and
  mobile tab menu items lacked `onPointerDown` intent prefetching.

Changes:

- Converted hosted and desktop form read/refresh routes to `async def`,
  finishing the DB snapshot (`form_snapshot`, including batched
  `category_fields.load_rows_many`, `saved_choices.read_entry`, and passing
  `db` through `native_form`/`leaf_schemas`) and releasing the connection
  before awaiting external marketplaces on the server loop.
- Parallelized all five independent eBay reads (`aspects`, `conditions`,
  `variations`, `listing_types`, `policies`) behind a single lazy
  `EbayClient` used only on schema cache misses, with all cache DB reads/writes
  off the event loop.
- Added bounded 15-minute TTL + singleflight caching for anonymous Mercari
  shipping quotes (bypassed on explicit refresh) and reused
  `shared_http.current_client()` in `mercari_api`.
- Indexed static catalogs (`@lru_cache` maps), avoided deep copies of shared
  choice lists, and added pointer-identity fast paths in `choice_lists.share`.
- Persisted versioned public choice lists in Cache Storage with stale-version
  pruning, hashed `_native_forms.<tab>` via `WeakMap` for compact query keys,
  added `onPointerDown` tab-intent prefetching on mobile marketplace menus
  (preserving `docs/hosted-production.md`'s rule against preloading unused
  tabs on open),   added automatic reloading of stale/unavailable marketplace contracts on the
  active marketplace tab driven by preflight so sellers never have to press
  "Reload fields" (while never refreshing unused or disconnected tabs in the
  background), avoided deep-copying untouched `_native_forms` contracts in
  `save_refreshed_form`, passed `db` through `preflight`'s `attach_form`, and
  added single-retry recovery + friendly error messaging for transient network
  drops (`TypeError: Load failed`) on form reads and refreshes.

Measured results (local Apple Silicon M-series, Python 3.12, local Postgres 16
via `pgserver`, fake 150 ms Mercari latency):

- Local form build + `choice_lists.share` CPU avg (50 warm calls):
  - Poshmark: 17.35 ms → 0.095 ms (p50 0.106 ms, p95 0.179 ms)
  - Mercari: 11.23 ms → 0.147 ms (p50 0.113 ms, p95 0.378 ms)
  - Depop: 1.05 ms → 0.034 ms (p50 0.024 ms, p95 0.120 ms)
- Warm eBay `read_form`: 0 nested `SessionLocal()` checkouts, 0
  `EbayClient.connect()` calls, ≤9 SQL queries.
- Single-connection Postgres pool (`pool_size=1, max_overflow=0`): concurrent
  `GET /api/conversations/{id}` succeeds (200) while a Mercari form request is
  waiting on upstream quotes, and upstream runs on the server loop.
- Concurrent multi-seller Postgres burst (2 sellers × 4 drafts, 24 form reads +
  16 listing reads = 40 simultaneous requests on `pool_size=4`, 150 ms simulated
  Mercari upstream): 0 errors, 8 concurrent identical Mercari requests
  coalesced to 1 upstream call; Poshmark p50 206.1 ms / p95 275.8 ms, Depop
  p50 234.5 ms / p95 360.9 ms, Mercari p50 300.8 ms / p95 375.5 ms, listing
  reads p50 339.0 ms / p95 383.1 ms (wall 399.7 ms warm; 2687.4 ms cold with
  first-request catalog JSON load).

Remaining gaps:

- Cold first-process catalog JSON parsing (~3 MB across marketplaces) still
  runs lazily on the first request after a server restart.
- Live production Fly.io / Supabase / eBay / Mercari / iPad Safari timings are
  not measured in this offline test environment; cold uncached eBay categories
  remain bounded by eBay's slowest single API call (now 1 RTT in parallel
  instead of 3 sequential RTTs + 3 connects).
