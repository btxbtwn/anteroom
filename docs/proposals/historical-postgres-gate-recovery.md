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


## Consumer precursor review — 2026-11-20 (not implemented)

This section records the current implementation boundary. It creates no
executable recovery path, authorization record, deployment request or settings
change. Ordinary push-CI selection, all-merges-since-live checks, release
serialization and historical failed results remain unchanged. Opening a
review PR does not authorize merging it or activating this proposal.

### Refreshed evidence

- Candidate main is `b28ba4b361071373731b93bc49c12b4bf78326e9` (0.1.1046),
  tree `07ff8975695a4182c00c2605b5679d56e493c67b`.
  [Exact push CI 44660122274](https://github.com/btxbtwn/anteroom/actions/runs/44660122274)
  succeeded. This is a snapshot, not authority to select a newer target.
- Recorded production base is
  `b73c534707b9ef9bd70e4dc589b9c4b10863a852` (0.1.1011).
  [Successful deploy 44257516865](https://github.com/btxbtwn/anteroom/actions/runs/44257516865)
  binds that full commit to image digest
  `sha256:9a4b1bccee97d53d903e23d5ec0a8cbf6bb71b043cbb35a1ed88c165947c60ac`.
  Independent authorized read-only metadata at 02:31 UTC confirmed both web
  and worker healthy on that digest, release 320. Production Alembic
  `d0e4f3a92b68` was independently reported and matches the base's sole repo
  head. This observation must be refreshed inside any eventual deployment
  attempt; an old report is not runtime reconciliation.
- The current source span includes migration `a70c21f496e8` after
  `d0e4f3a92b68`, adding nullable `marketplace_listings.price_recorded_at`.
  The older no-migration observation above applies only to its dated span.
  Previous-release fixture upgrades on disposable SQLite and Postgres,
  existing-row preservation, migration locking, old/new application behavior
  and backup/restore readiness require pinned evidence before recovery.
- [Descendant verification 44662061597](https://github.com/btxbtwn/anteroom/actions/runs/44662061597),
  attempt 1, was dispatched once with the exact candidate and base above.
  It is evidence-only and was still running when this section was prepared.
  No successful final receipt is asserted here. Native validation remains
  separate, including the merged #1631 and other accumulated native changes.

### Blocking enforcement gaps

Read-only GitHub metadata shows that the existing `production` environment
has a branch-policy rule, no required-reviewer rule, and permits administrator
bypass. Repository workflow-token permissions default to read. The hosted
workflow also explicitly requests only read permissions. These observations
are not requests to change protection or permissions.

Neither a main-only environment, an Actions actor name, dispatch access nor a
JSON `approved: true` field authenticates Cristian's approval of an exact
recovery attempt. A checked-in record must have an independently verifiable
operator authorization source bound to its immutable bytes; that verifier
and trusted operator identity policy do not currently exist. No caller-supplied
`authenticated` boolean is acceptable.

The proposed Deployments API ledger also has no established atomic claim
contract here. Serial concurrency alone does not prove safe behavior after a
lost create response, crash or later rerun. Its API creates deployment events
that external integrations can consume; creating a ledger entry must not be
assumed to have no external side effects. No ledger entry was created to test
this. See [GitHub's Deployments API documentation](https://docs.github.com/en/rest/deployments/deployments).

**Stop production-capable implementation until operator authentication and
one-use enforcement have a reviewed, testable contract.** A source-only
validator that accepts unverified dictionaries would conceal these gaps,
so none is introduced by this precursor.

### Candidate one-attempt design to review

A potentially smaller alternative to a new mutable approval ledger is authority
bound to one GitHub workflow run identity. GitHub documents that a workflow's
run number is unique and remains unchanged on rerun, while the attempt number
increments. See [the Actions variables reference](https://docs.github.com/en/actions/reference/workflows-and-actions/variables).
This is a candidate design, not an implemented or independently validated
single-use guarantee:

1. Before a future dispatch, an authenticated operator authorizes an immutable
   record binding repository ID, recovery workflow ID/path and reviewed
   workflow revision, one specific future run number, attempt 1, exact target
   and tree, live base and expected base schema/image, diagnosed failed run/job,
   correcting merge, expiry, and a unique verification receipt identity.
   The receipt binding includes run ID/attempt, artifact IDs and digests, source
   and CI blobs, inventories and independently confirmed final workflow success.
2. The consumer verifies the record's immutable commit/blob and independently
   authenticated operator identity. Record membership on main alone is
   insufficient. Do not invent an operator allowlist or silently add reviewers.
   Choosing this authentication mechanism needs explicit review/authorization.
3. Obtain execution identity from GitHub's authoritative run/attempt records
   and trusted workflow context, not dispatch inputs. Require the exact
   repository/workflow identity and authorized run number, attempt 1, a single
   designated side-effect job, manual event, and unchanged trusted implementation.
   Resolve its unique run ID and bind the audit result to it. Reject every
   rerun, duplicate dispatch, changed workflow, replacement repository or reused
   record for a different run. An intervening run consumes the nominated
   number and causes a stop, not selection of the next available number.
4. Prove the complete side-effect job executes at most once for that identity,
   including partial-job reruns and cancellation/restart. Do not rely on an
   upstream validation job's cached success: all checks must execute in the
   side-effect job. No automatic resume or retry is permitted after failure.
   The unique run is consumed even if it fails before migration. This proof
   and the authenticated record adapter are prerequisites, not TODOs to ship
   behind a permissive flag.
5. Reconcile all live Machine images, expected base revision, authoritative
   schema and incomplete deployment attempts inside the same non-cancelling
   `deploy-hosted` concurrency before any image push, migration or rollout.
   Any drift, missing observation, failed API call or ambiguous previous effect
   stops execution. Validate native, migration, hosted-image and operational
   evidence separately; the Linux receipt cannot attest to these.
6. Reuse the existing deployment stages and their ordering for only the exact
   authorized target. Keep implementation validation tied to its reviewed
   revision when checking out the application target. Record success only after
   web readiness/version and every Machine image check pass. Preserve an
   immutable audit result; that result is evidence, never ongoing permission.

No new workflow, run-number reservation, operator record, API permission or
production hook is installed here. Binding an authorization to a future run
number can fail safely under races, but it does not reserve that number or
solve operator authentication. Integration must demonstrate these properties
before this alternative can replace the proposed durable claim ledger.

### Required negative tests before an executable consumer PR

Use stdlib mocks/fixtures first, with no network, application, database or
production command execution. Every rejection must occur before the fake
production-effect recorder receives any call. Include one fully bound positive
fixture, then mutate each binding independently.

| Boundary | Required fail-closed cases |
| --- | --- |
| Operator | Missing proof, default approval, forged actor, untrusted operator, mismatched record bytes, expired or revoked authorization |
| One attempt | Wrong workflow/repository/run number, duplicate dispatch, attempt 2, partial-job rerun reusing validation outputs, crash/restart, replay after failure |
| Immutable evidence | Wrong target/tree/base/CI blob/workflow blob, mismatched receipt/artifact IDs/digests, edited archive contents, expired/missing/duplicate artifacts |
| GitHub records | Wrong repository/event/path/attempt, unfinished or failed final run, truncated/repeated API pages, ambiguous attempt lookup, unavailable API |
| Test completeness | Missing, skipped, cancelled or failed required jobs; missing/overlapping shards; disagreement with full collection; skipped historical Postgres regression |
| Live reconciliation | Changed/unknown base, mismatched worker/web images, schema drift, partial prior migration/rollout, old observation substituted for an in-attempt probe |
| Release behavior | New main cannot change target; ordinary triggers cannot consume recovery authority; no record leaves ordinary gate decisions byte-for-byte unchanged |
| Completion | Build failure prevents migration; readiness/worker failure prevents success marker; interrupted attempt cannot auto-resume; failed historical CI is never edited |

A mock suite establishes rejection logic, not platform enforcement. Before
activation, separately validate authenticated operator proof, GitHub run/attempt
semantics and the whole side-effect job's retry behavior in a nonproduction
harness. If the proof fails, stop and review a durable atomic claim mechanism;
do not substitute a mutable pointer or a blanket SHA exception.

### Implementation and activation sequence

1. Review this boundary and choose the authenticated operator proof and either
   demonstrated run-bound authority or a demonstrated durable atomic claim.
   Any permissions or protection changes need separate explicit authorization.
2. Only then prepare disconnected source validators and negative fixtures in
   an unmerged PR. Reserve any expensive local validation with the coordinator;
   do not duplicate the running evidence workflow. Require independent review
   of authentication, replay resistance and the consumer/target trust boundary.
3. Complete the current evidence attempt and archive immutable artifacts plus
   final job results. Pin native, migration and operational validation separately.
   A later application target needs refreshed span review and exact verification.
4. Review the executable integration separately. Merging is a release, so
   merging even a dormant precursor requires its own authorization. Do not
   merge or dispatch merely to obtain evidence or an approval identity.
5. Before activation, Cristian must choose continued ordinary automatic
   deployment after recovery or a separately approved hold covering both
   entrypoints. Once recovery records a new live base, later ordinary deploys
   no longer traverse the old failure and queued runs may ship newer code.
   No persistent historical exception is needed or allowed.

This precursor has been checked against current repository workflows, the
read-only protection metadata and the linked GitHub documentation. It is not
an independent security approval; the contract and platform enforcement still
need independent review. No production action is authorized by this text.
