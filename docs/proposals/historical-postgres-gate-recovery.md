# Historical Postgres gate recovery — proposal only

Observed 2026-11-18 UTC. No executable gate/workflow change, CI rerun,
dispatch, merge, deployment, production query or setting change is authorized
or performed by this proposal. Implementation and activation need separate
approval. No tests were run: the merge coordinator owns serial capacity.

## Verified state

- Public health: version **0.1.1011**, status ok; readiness: status ok.
- Latest deploy failure [44305392664](https://github.com/btxbtwn/anteroom/actions/runs/44305392664)
  records production commit `b73c5347` and rejects `fa824f9a` before secrets,
  image build, migrations or rollout. This is the workflow's recorded revision,
  corroborated by its VERSION and public health, not a direct Fly image or
  production Alembic inspection.
- Main at observation: `ffe66259d0694a9cecb2fab192d8ee6492c2c0e1`, 0.1.1033.
- Historical [CI 44249641639](https://github.com/btxbtwn/anteroom/actions/runs/44249641639),
  commit `fa824f9af17c7676458894df4b12c7af046b3e6c`, remains failure, attempt 1.
  Exact failed [job 134626336152](https://github.com/btxbtwn/anteroom/actions/runs/44249641639/job/134626336152):
  `Studio backend Postgres (1/2)`; 1 failed, 2381 passed, 48 skipped,
  1131 subtests passed, 490.06 seconds.
- Failure: `test_hosted_assistant.py::test_shop_snapshot_survives_calculation_longer_than_the_idle_transaction_deadline`,
  `IdleInTransactionSessionTimeout` during `sales_records.read_sale_lines`.
  The test sets a 150 ms idle deadline. Failure occurs before the intentional
  250 ms size-audit sleep: the snapshot retains an idle read transaction
  across calculations/archive-lock waiting, then queries the killed connection.
  The calendar pluralization change in #1607 is unrelated to that code path;
  this was a genuine existing defect, not evidence to discard as a flaky check.

## Corrected descendant evidence and limits

[PR #1653](https://github.com/btxbtwn/anteroom/pull/1653) merged as
`5cc7fea11067baf1b93d1519b57b0fd93be2db19`. It ends analytics, ledger and
box read transactions before calculation, checks the sales archive before
opening range reads, reuses analytics rows, and indexes eBay sale suffixes to
remove quadratic duplicate work before transaction release. It preserves the
timeout and seller scope; no schema revision was added.

Independently retrieved [merge CI 44302196619](https://github.com/btxbtwn/anteroom/actions/runs/44302196619)
is successful, including both SQLite/Postgres shards, both browser suites and
code health. Postgres shard 1 [job 134794561736](https://github.com/btxbtwn/anteroom/actions/runs/44302196619/job/134794561736)
reports 2420 passed, 48 skipped, 1133 subtests passed in 534.94 seconds.
Frontend, extension and relay jobs skipped by change selection: this is not
an all-components checkpoint. Release CI 44302236663 is successful too, but
release-only selection is not cumulative verification.

Coordinator and #1653 PR report old-code reproduction at test-only `b6ceef4a`
with the same failure frame; affected-suite green at `1f362400` (SQLite 98,
Postgres 113); and a full local check at `1f362400`, exit 0 at 12:10:37 UTC
(SQLite 4692, Postgres 4855, browser 190 + 67, health/types). The subsequent
`31d77ddc` changes only the release note. These local receipts were not rerun
in this investigation and are not an exact `ffe66259` all-components receipt.
#1653 explicitly retains later calculation/second-session timeout windows and
coverage gaps; corrected historical cause does not prove every timeout gone.

## Why main stays blocked

`wait-for-tested-code.py` requires a successful latest **push** CI run for
each non-release first-parent commit after recorded production through the
chosen target. It has no notion of a verified corrected descendant. A failed
ancestor stays failed after its fix merges; its error text's “fix main; its
passing run deploys” is therefore misleading. A green dispatch alone is
ignored. `deploy-gate.py` choosing a newer green release does not alter this
second gate. This gate is doing its present documented job; accepting a new
verification form is a policy change requiring explicit review/approval.

## Backlog and rerun consequences

Read-only GitHub API audit through `ffe66259`: 28 non-release first-parent
commits since `b73c5347`; 27 latest push CI successes and only `fa824f9a`
failed. There are 115 changed files, including backend, web, extension, iOS,
credential handling, marketplace/delist guards, read transactions, photo
loading and CI required-job enforcement. There are **no migration-directory
or deploy/ file changes** in that range; no new Alembic revisions are pending
*from this source diff*. Live schema, migration history and backup readiness
were not inspected. The workflow still executes `alembic upgrade head` and
category refresh. No guarantee of a no-op production database step follows
from a source diff alone. Runtime background jobs can resume under new code;
explicit marketplace publication/delisting confirmation must remain intact.

A historical rerun checks out old `fa824f9a`, without #1653. It may reproduce
the defect or pass by timing luck. Neither tests the corrected descendant.
If it succeeds, its push CI completion automatically triggers Deploy hosted.
That deploy chooses the newest eligible tested release from the trigger up
at execution time, potentially **beyond 0.1.1033 as more PRs merge**. With the
only observed barrier cleared, it can build, migrate, refresh categories and
roll out the whole eligible backlog without another manual deploy action.
Do not rerun historical CI as a substitute for recovery approval.

## Proposed recovery contract (disabled until separately approved)

1. Keep ordinary per-merge push CI gates, required PR checks and failed
   historical results intact. No SHA allowlist, invented green check,
   weakened timeout, “any newer green run”, or nearest-merge fallback as the
   recovery mechanism.
2. Add a separate **verification-only** workflow, with no production secrets,
   production environment, Fly token, migration credentials, image push,
   deployment step or triggering workflow name `CI`. Pin an immutable target
   SHA on main's first-parent line and recorded production SHA. Checkout must
   match target exactly. The trusted verifier definition must itself be
   reviewed; never accept an arbitrary workflow or caller-supplied success.
3. Run the union of required component checks for the entire production-to-
   target span (or every component), regardless of the target's narrow diff
   or release-only selection. Include both SQLite/Postgres shards, inventory
   completeness checks, code health, frontend/builds, both browser suites,
   extension/skills/relay checks and applicable native checks or separately
   pinned native receipts. Retain the historical regression and transaction,
   RLS, cancellation and marketplace-confirmation tests. Shard completeness
   must be proved; an aggregate success with skipped required jobs is invalid.
4. Produce a durable, auditable receipt bound to repository, production SHA,
   exact target SHA/tree, verifier workflow identity/revision, run ID/attempt,
   successful required jobs and shard inventories. Validate it against GitHub
   records, not just a user-editable JSON claim. Missing/expired artifacts,
   incomplete pagination, unexpected jobs, skipped/cancelled/failed checks,
   mismatched SHAs and unavailable API evidence fail closed.
5. Separate approval to **consume** a receipt and deploy this backlog from
   permission to run verification. A reviewed recovery record identifies the
   failed run/job and root cause, correcting PR, receipt, pinned target,
   operator approval, migration/backlog assessment and one-use scope. The
   proposed approval record is a reviewed manifest committed on protected main,
   identified by its immutable commit SHA. It must carry the complete evidence
   bindings; a bare target-SHA exception is insufficient. Runtime consumption
   state must live in a durable deployment-attempt ledger (proposed: GitHub
   Deployments API), with a unique approval ID and pending/in-progress/succeeded/
   failed outcomes. Validate the API permissions and atomic claiming strategy
   before implementing this production-capable consumer; serial deploy
   concurrency alone does not prove crash-safe consumption. Do not
   mark the historical run successful: report its defect as superseded by
   full successful verification of that exact descendant's shipped tree.
6. Recovery must ship **only the approved target**, never opportunistically
   select newer main. If a later target is wanted, re-assess the expanded span
   and verify/approve it separately. Recheck recorded production, ancestry,
   successful receipt attempt and unchanged approved inputs inside existing
   serial deploy concurrency before any production action. If production has
   advanced or ancestry changed, stop and reassess; never roll back or reuse
   the approval for another span. No assumption that an environment approval
   control is already configured; choose and review its actual enforcement
   before activation. Verification initially requires target = workflow run
   head_sha = checkout SHA, with checkout fixed by the trusted workflow rather
   than an arbitrary ref input. Require a run from this repository, the expected
   workflow path/ID, event and reviewed workflow blob at that SHA; verify
   provenance of selection and inventories generated by that definition. A
   workflow head_sha without these checkout/provenance checks is insufficient.
7. Keep image-build-before-migration, migration credentials isolation,
   category refresh, readiness/version checks and every-Machine image check.
   Record the actually deployed commit only after all succeed. Retain the
   audit record after it is consumed. Once any production step starts, an
   interrupted/failed attempt is not automatically retryable: reconcile actual
   Alembic revision and all Machine images with the intended target, record
   partial completion, and obtain explicit retry/recovery authorization. The
   last successful marker alone cannot describe a partial migration or rollout.
8. Clearing the barrier also unlocks queued/new ordinary automatic deployments.
   After recovery succeeds, they can immediately ship newer green code, even
   though the recovery job itself was pinned. Before activation, obtain explicit
   approval for that continued automatic backlog rollout, or separately design,
   approve and verify an enforceable deploy hold covering ordinary and recovery
   entrypoints under the same concurrency. No hold or production setting is
   changed by this document. The operator must choose this before recovery;
   otherwise a promise to deploy only one approved target would be false.

This replaces evidence for the corrected *shipped tree* with stronger,
complete verification; it does not attest that the old tree passed. Merely
ignoring the old failure because #1653 is green would circumvent the gate's
purpose, because per-change CI may omit other backlog components.

## Implementation tests and approval sequence

Before a production-capable change, approve this contract and its enforcement
mechanism. Then implement in an unmerged review PR; reserve coordinator test
capacity using exact head, command selection, disk/RAM/Postgres/port needs and
runtime estimate. No capacity is granted for this investigation.

Required negative tests: arbitrary successful narrow/release CI cannot
supersede failure; unrelated PR/dispatch/repository/workflow cannot; stale
production, wrong target/tree, non-first-parent ancestry, missing inventories,
partial API pages and cancelled/latest failing attempts cannot; one receipt
cannot authorize a newer target or replay after production advances. Positive
test: an approved complete receipt covers exactly its diagnosed failing span,
while later merges still need their own verification. Race tests must cover
new merges, concurrent production advancement, failures after migration,
partial Machine rollout, crash/restart after claiming approval, and queued
ordinary deployment continuation. Normal deployment behavior
must remain unchanged without explicit recovery activation.

Next safe step: review this proposal and approve only implementation of the
verification-only path first. Actual verification execution, enabling receipt
consumption, and deployment each remain separately authorized actions. Refresh
live/main/backlog evidence at every boundary. No PR has been opened or pushed
because this is an unactivated design and no local test capacity is available.

## Independent design review

An independent read-only review of the scripts/workflows found the diagnosis
and existing-gate description consistent with code. It required explicit
partial-rollout recovery state, disclosure of later automatic deployments,
and concrete receipt/approval provenance. Those requirements are incorporated
above. This review approves no policy activation; API atomicity and approval
enforcement remain implementation-review blockers. No executable fix or PR
has been created, and no test pass is claimed for this document.

## Verification-only implementation scope (subsequently authorized)

Cristian subsequently authorized an isolated, unmerged verification-only PR,
robust tests and independent review. The implementation reuses CI with all
Linux component checks enabled and retains exact-attempt job/collection evidence.
It does **not** implement receipt consumption, supersede the failed historical
run, establish native validation or authorize production. See docs/ci.md for
execution restrictions and artifact interpretation. Original observations above
remain scoped to the recorded ffe66259 snapshot; later merges, especially new
migrations, require a fresh backlog assessment.
