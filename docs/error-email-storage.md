# Operator error-mail storage

The outbox retains every unsent group and its occurrence count. No retention
limit removes warnings or errors. Daily/hourly mail budgets and retry delays
control attempts, not diagnostic retention.

## Per-group access

`error_email_groups` holds one row per fingerprint. Pending JSON preserves the
report, count, first occurrence and due time; scalar count/due/warning columns
support aggregate status and bounded due selection without returning reports.
A delivered fingerprint keeps only its last-send timestamp until the rolling
24-hour history expires. Re-enqueuing joins the end of the pending queue.

All writers first lock the singleton `error_email_state` row (SQLite uses
`BEGIN IMMEDIATE`). This preserves the existing shared lease and budget
serialization; it does not add a seller/draft job gate. Each report reads its
own group. Status aggregates counts in one consistent statement. A tick selects
at most the existing 20-group email batch after checking budget/lease/backoff.
SMTP runs after commit and before a separate completion transaction. Completion
rechecks the lease token and subtracts only the sent counts, retaining adds made
during SMTP. Failed or uncertain delivery retains diagnostics and consumes the
reserved attempt as before.

## Rolling deployment and removal after 2026-12-15

Revision `d7be65e14c2f` locks the old control row/table during backfill. It copies
groups in their original order, including counts and per-fingerprint last-send
history. Budgets, digest time, pause and in-flight lease remain untouched.

Already-running old workers can still write the old JSON shape. An invoker
trigger projects their committed changes into group rows under that same lock.
New workers update group rows and rebuild the old JSON mirror **inside the
database**, atomically with control metadata. They increment `group_sync_version`
so that their mirror does not trigger a redundant projection back into rows.
Thus either worker version can add, reserve or finish around the other, including
an old sender which was outside a transaction during migration. A rollback
rolls back both representations. No Python connection fetches all diagnostics
for the new report/status/tick paths. `EmailStore.read()` remains an explicit
full compatibility snapshot used by migration/regression checks.

The mirror is a dated rolling compatibility cost, not the permanent data model.
It duplicates stored diagnostics and still serializes/rebuilds a large JSON row
inside the database on writes. Old workers still incur their original network
reads. After **2026-12-15**, and only after verifying all running web/worker
processes use this revision, remove the legacy JSON maps, projection trigger,
`group_sync_version`, and mirror assembly in a forward Alembic revision. Keep
the per-group table, control lock, budget/lease semantics, and regression tests.
Do not remove compatibility while an old SMTP sender could still finish.
This PR does not perform that deployment or any production migration.

## Security

The table is operator-only, like `error_email_state`. PostgreSQL RLS is enabled;
PUBLIC, seller, anon and authenticated roles have no table/sequence access.
Only the existing API operator role receives the needed table operations and
sequence use. The compatibility function is SECURITY INVOKER with a fixed
search path and schema-qualified objects; it grants no privilege escalation.
Seller roles receive neither function execution nor an RLS policy. Tests use
isolated databases and exercise both new writes and old trigger writes as the
API role. No account token or SMTP credentials are stored here.

## Evidence and limits

Tests cover previous-release upgrades, mixed-version increments, old/new send
callbacks adding groups and repeats, SMTP failure, migration during an old send,
rollback, budget/lease behavior, and role isolation. All senders are test stubs.

`test_idle_read_payload_is_independent_of_unrelated_backlog` seeds 100 and 1,000 warning
groups with synthetic 1 KiB messages, then measures 20 sequential cycles of one
repeat report, status and a tick with nothing due, on old and new code in the
same isolated database. Its cursor wrapper counts encoded returned scalar
values, excluding protocol framing, TLS, compression and Supabase billing.
It reports p50/p95 wall times and query count; those are local observations,
not production latency, sustained server CPU, or billable egress measurements.
The new path uses more small queries; the temporary mirror still costs database
CPU and writes. Removing it requires the completed rolling transition above.

Neither a claimed 2 GB/day idle use nor a 275 GB cap/December 14 deadline has
been freshly verified. No production data, usage counters or settings were
accessed. Production capacity, multi-seller interaction p50/p95 under generation,
and actual billable egress remain unmeasured here. Synthetic byte reductions do
not establish compliance with a quota or a deadline. The separate alert-budget
investigation owns budget configuration; this change does not alter it.

On 2026-11-15, final-code measurements were taken while other local test workers
were running. Each row below is 20 sequential cycles; reported bytes count
returned encoded values only. Each comparison used the same disposable database
and backlog. There were no operation errors or fake sends.

| Engine / groups | Legacy returned bytes | Group returned bytes | Legacy p50 / p95 ms | Group p50 / p95 ms |
| --- | ---: | ---: | ---: | ---: |
| Postgres 16 / 100 | 9,777,416 | 34,740 | 26.31 / 33.47 | 23.99 / 41.13 |
| Postgres 16 / 1,000 | 97,797,416 | 34,760 | 173.28 / 562.36 | 136.94 / 386.56 |
| SQLite / 100 | 9,777,416 | 34,520 | 12.75 / 20.45 | 11.28 / 14.52 |
| SQLite / 1,000 | 97,797,416 | 34,540 | 69.70 / 97.21 | 60.31 / 86.27 |

The legacy path issued 60 SELECTs over 20 cycles; the group path issued 140.
The new path does not promise lower tail latency (the 100-group Postgres p95
was higher). These small samples under concurrent local test load are not
production or stable benchmark results. Eight-worker mixed-version correctness
is tested separately; sustained CPU, seller interaction latency and billing
remain unmeasured.

Independent review found an initial SQLite backfill race and per-row backfill
round trips. Regressions failed before the early write fence and batched inserts
and pass afterward. Additional regressions pin legacy queue order across new
writes and old-worker re-enqueues. Final independent review found no remaining
blocking issue and reiterated the mirror/query-count tradeoffs above.

Existing policy is unchanged: due errors take precedence over warnings; each
severity queue drains in insertion order. A large error backlog can delay a
newly arriving error, and continuous due errors can delay warning digests. This
fairness concern is reported for separate policy work, not changed by storage
normalization. The alert-budget and any explicitly approved one-off clear are
separate work; neither authorizes automatic eviction here.
