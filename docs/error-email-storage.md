# Operator error-mail storage

The outbox retains queued groups until delivery. Daily and hourly mail
budgets and retry delays control attempts. Triage suppresses mail while
continuing to record occurrences in issue history.

## Per-group access

`error_email_groups` holds one row per severity and fingerprint. Pending JSON preserves the
report, count, first occurrence and due time; scalar count/due/warning columns
support aggregate status and bounded due selection without returning reports.
The issue fields survive delivery. `occurrence_count` counts all observed
occurrences, including suppressed ones, and appears as `count` in the
group API. `headline` and `last_release` describe the latest arrival.
`first_seen`, `last_seen`, `resolved_at`, and `muted_until` store UTC Unix
seconds. The API returns exposed timestamps as ISO 8601. Activation
initializes history from pending reports. Previously discarded history
cannot be reconstructed.

Completed groups age out of last-send accounting after 24 hours. Pending
repeats retain their last-send timestamp through long pauses. Re-enqueuing
joins the end of the pending queue.

## Issue lifecycle

- **Open** is the schema default and the default group list filter.
- **Resolved** records `resolved_at` and `resolved_release`. An occurrence
  from a demonstrably newer release reopens the group and clears
  `resolved_at`. Errors become eligible as first alerts with `[Regression]`
  in the subject and the earlier release in the body.
- **Muted** with no `muted_until` is indefinite. The first occurrence at
  or after a timed mute expires opens the group. An error is then eligible
  for immediate delivery.

Browser events use the browser release. Server reports and the resolve
default use `ANTEROOM_RELEASE`, then `FLY_IMAGE_REF`, then the app version.
Ordering supports numeric app versions, the hosted workflow's versioned
image labels, and Fly deployment ULIDs. Equal, older, missing, or incomparable
releases keep the group resolved. Pass `resolved_release` explicitly for
browser issues when the browser and server releases use different formats.

Suppressed occurrences update issue history without joining a mail batch.
Previously queued mail is held until the issue opens again. SMTP attempts
already reserved when an operator acts may still complete. Reopen clears
resolution and mute fields and preserves the optional 500-character note.

All writers first lock the singleton `error_email_state` row (SQLite uses
`BEGIN IMMEDIATE`). This preserves the existing shared lease and budget
serialization; it does not add a seller/draft job gate. Each report reads its
own group. Status aggregates counts in one consistent statement. A tick selects
at most the existing 20-group email batch after checking budget/lease/backoff.
SMTP runs after commit and before a separate completion transaction. Completion
rechecks the lease token and subtracts only the sent counts, retaining adds made
during SMTP. Failed or uncertain delivery retains diagnostics and consumes the
reserved attempt. Consecutive transient failures double the pause from five
minutes up to one hour, shared by both outbox workers. A successful outbox
send clears the backoff. Daily quota rejections pause delivery for 24 hours.
Each SMTP exchange has a 60-second deadline within the 120-second send lease.

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

### Lifecycle rollout

1. Apply the additive lifecycle revision and roll out the new binary across
   both web and worker processes with `ANTEROOM_ERROR_EMAIL_LIFECYCLE` unset.
   The migration preserves the queue, ordering, last-send history,
   deadlines, attempt budget, and lease. PostgreSQL normalizes NUL
   characters in both copies of the queue on upgrade and on subsequent
   writes from old senders. It preserves report order and accounting.
   Indexes build after the additive DDL commits.
   During this phase, upgraded and old senders share the hourly warning gate.
   The operator group endpoints return 503 until activation.
2. Verify all pre-lifecycle web and worker processes have stopped, including
   any old send in progress. Then set `ANTEROOM_ERROR_EMAIL_LIFECYCLE=1` for
   the upgraded senders. The first opted-in sender seeds issue summaries from
   the pending queue under the control lock and records activation in
   `error_email_state`. All upgraded senders honor the
   active policy from that point, including processes awaiting the config
   restart. The first daily warning window carries forward the prior hourly
   window's anchor.

Issue counts are authoritative for occurrences processed after activation.
Pending reports queued by old writers during rollout are included in the
initial summaries. Activation is persistent and removing the environment flag does
not undo it. After activation, keep every sender on a lifecycle-aware build.

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

## First alerts and repeat digests

Error/critical fingerprints without retained send history are eligible
immediately, ahead of notified repeats. They still obey the shared daily/hourly
attempt budgets, SMTP pause and lease; continuous distinct errors can exhaust
those budgets. There is no artificial batching delay for a new critical error.

Previously notified errors wait at least one hour after their last reservation
that completed successfully. They share one durable `error_digest_after` gate:
successful repeat reservations are at least one hour apart across processes and
restarts, with up to 20 due groups in insertion order. Staggered first alerts can
make a repeat miss a digest and wait for the following hour. Extra groups remain
queued for later digests. New first-alert successes initialize, but never slide, the shared
deadline. After activation, warning digests keep their own 24-hour gate and run before repeats when
due and below the existing warning budget (daily limit minus one quarter of the
limit rounded down, with at least one attempt reserved for errors). First alerts
still precede both digest types, so warning delivery cannot be guaranteed during
a novel-error flood.

For three stable, staggered fingerprints, the old per-fingerprint 15-minute
policy could use 12 repeat messages/hour. The shared gate allows one repeat
message/hour; warning digests and first alerts are additional. This is a policy
bound, not a measurement of production mail volume or Google's actual quota.
Warning and repeat gates are independent, so both digests can send back-to-back;
new first alerts can also send in a burst within the existing attempt ceilings.
The 60/hour ceiling and configured rolling-24-hour budget still count every
reserved SMTP attempt, including failures and uncertain crash outcomes.

Each queued group keeps its unsent count, first timestamp and latest full
report and trace. Issue history stores aggregate counts and timestamps.
Arrivals during SMTP survive exact reserved-count subtraction. Failures retain
the whole batch and existing backoff/quota pause; they do not advance a successful
digest deadline. A failed digest can retry after backoff, consuming another
budgeted attempt. An expired lease after a process stall can also cause an
uncertain retry; SMTP delivery is not exactly once. Unknown control metadata and
the rolling JSON mirror remain intact. All sending processes must be updated
before relying on the new digest bound; old binaries do not honor the new gate.

The existing logging handler still has a 500-report in-memory ingress buffer;
overflow or a crash before durable insertion can lose those unstored events.
New reports replace NULs and unpaired surrogates before storage. The upgrade
repairs already-queued Postgres reports in both copies of the outbox under
the control lock. If storing a batch fails while the database is reachable,
the handler retries each report individually and counts invalid reports as
dropped. On connection failures, it retries the reports still in memory.

The operator groups API includes `impact_7d` for the preceding 168 hours. Counts begin
when upgraded writers collect reports with lifecycle tracking active. A null summary
means no retained observations. Missing seller attribution is counted separately.
Release endpoints follow occurrence time. Stack samples contain at most eight code
locations from one report; unsupported stack formats have no sample. Delivery, muting,
and reopening do not erase samples. Rows expire from queries at the cutoff and are
pruned every 30 minutes by the running reporter, including when email has no budget. Seller digests are scoped to one fingerprint and
never returned by the API. A rollout or dropped report can leave partial coverage.
