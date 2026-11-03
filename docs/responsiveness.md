# Responsiveness and concurrent work

The web, Mac and iPhone apps share the responsiveness requirements in
[AGENTS.md](../AGENTS.md#responsiveness-and-concurrent-work). Independent drafts
and accounts must overlap; long work must leave navigation, input and unrelated
drafts usable. This is a product requirement, not a promise of infinite hardware
capacity or zero model/network latency.

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

Target input acknowledgement within 100 ms and cached navigation within 200 ms
on supported devices. Keep already loaded screens available during background
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
