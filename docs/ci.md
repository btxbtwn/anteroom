# CI and merging

The required `CI` check aggregates the jobs in `.github/workflows/ci.yml`.
All selected jobs must succeed. Jobs for unchanged inputs may skip; a failed
selector, failed job or cancelled job prevents `CI` from passing.

`hosted-image-validation.yml` separately builds the exact PR head's hosted
Dockerfile on a GitHub-hosted AMD64 runner. It checks native dependencies,
nonroot execution, packaged assets and actual server startup against a private
throwaway Postgres database. It uses synthetic public configuration, a read-only
token and no production environment, host mounts, published ports, image push
or deployment. Build/runtime logs and source/image identifiers are retained as
the `hosted-image-validation` artifact. Its check is reviewed alongside `CI`;
it is not included in the existing aggregate or production deploy triggers.

`.github/scripts/changed-components.py` selects jobs from the actual merge's
first parent on PRs and from the push's previous commit on `main`. `check.sh`
uses the same selector for committed and local changes. Renames count both
the old and new paths. An unavailable base runs every check group.

| Changed input | Required check groups |
| --- | --- |
| Backend runtime | Backend on SQLite and Postgres, code health, both browser suites |
| Backend tests only | Backend on SQLite and Postgres, code health |
| Frontend runtime | Frontend tests and both builds, backend, code health, both browser suites |
| Frontend tests only | Frontend tests and both builds, backend, code health |
| Extension or listing skills | Backend, code health, browser suites; extension checks for extension changes |
| Skill references (`skills/list-this/references/`) or the extension manifest | Also frontend and extension checks: the editor imports the field list and the manifest's version, and the extension's value matrix reads the option lists |
| iPhone tool installer or `ios.yml` | Backend, code health (a backend test reads them) |
| Python dependencies or their installer | Backend, code health, browser suites, dependency audit |
| npm dependencies | Frontend, backend, code health, browser suites, extension checks |
| Relay | Relay checks |
| CI workflow, selector or local check driver | All check groups |

Backend tests inspect frontend, extension and skill sources, so those changes
must keep backend coverage. Browser-test inputs also run frontend checks because
knip scans e2e imports. Generated API contracts keep their backend verification.
Unknown inputs conservatively run every Studio suite. Repo checks and changelog
validation always run. Native iPhone checks remain in their separate workflow.

On PRs, standalone `*.e2e.ts` specs at the top of `anteroom/e2e/` or
`anteroom/hosted-e2e/` select their owning browser suite. Treat shared helpers
as inputs to both. For example, hosted specs import `e2e/fixtures.ts`. A rename
across suites selects both, as does a change to runtime code or shared inputs.
Main pushes and manual full verification retain both suites when browser tests
are selected. The local check driver exercises the push coverage.

The reusable `.github/workflows/dependency-audit.yml` audits the locked Python
packages for dev, hosted and Mac builds when their dependency inputs change.
It also runs daily at 08:23 UTC and supports manual dispatch, so newly reported
vulnerabilities are detected even when the lockfile has not changed. Dependency
PRs must pass the audit before merging; unrelated PRs do not wait on it.

The repository rules require passing `CI`, without requiring the PR branch to
be up to date with `main`. Update a PR for actual conflicts, failed checks or
a needed dependency, not solely because another PR merged. Claude's conflict
auto-fix follows that rule: it runs only when GitHub reports a real conflict
(`mergeable_state=dirty`), merging `origin/main` into the PR — it does not
refresh merely-behind branches. A passing PR can still interact badly with
another change; the checks on `main` verify the merged result. Pushes to
`main` never cancel each other's CI, and the hosted deploy waits for CI to
pass on every merge since the commit production runs
(`.github/scripts/wait-for-tested-code.py`), so a broken combination stops
before production even when a later merge's narrower checks pass. The
automatic branch-update workflow stays disabled.

## Corrected-descendant verification (evidence only)

`verify-corrected-descendant.yml` is a new, manual **Descendant verification**
workflow. It calls the same CI jobs with `full_verification: true`: every
Linux CI component runs, even for a release-only or narrowly selected target.
Ordinary PR/push CI selection is unchanged. The opt-in pytest collector captures
full collection before pytest-split and selected items afterward, hashes test
IDs, and validates both shard unions against full collection for SQLite and
Postgres. The historical assistant regression must actually pass on Postgres.
Other declared skips/expected failures remain visible in the evidence.

The operator must provide full `expected_target` and `production_base` SHAs.
Target must equal the workflow event SHA and checkout, contain the historical
failure and correcting merge #1653, and follow the base on main's first-parent
line. Select a main ref resolving to that exact SHA; moving main causes a
mismatch and failure, not an implicit update to a newer target. This workflow
must first be present on the default branch before GitHub permits its initial
manual dispatch. Do not merge this PR merely to obtain a verification run:
merging is a release and requires separate authorization.

Artifacts retained for 30 days bind repository, target/tree, declared production
base, workflow/CI blobs, run/attempt, actual expanded GitHub jobs and backend
shard inventories. API job pagination must be complete; skips, failed or
cancelled jobs, missing shards, mismatched run attempts and a skipped historical
regression fail closed. Archive the run and immutable artifact IDs/digests when
using the evidence in a later review; expired/missing artifacts cannot serve as
verification. The receipt is generated while its evidence job is still running,
so future users must also confirm the **whole workflow's final success** via
GitHub rather than treat the existence of a receipt as success.

This is **Linux verification evidence, not a deployment approval/checkpoint**.
The production base is supplied for the reviewed span, not independently
verified as live. Detected native changes are listed; the list is not exhaustive for shared
Python/packaging changes. Native impact assessment and pinned native receipts
remain separate requirements; hosted image validation, live schema/Machine reconciliation, backup
readiness and production load measurements are not established by this run.
This standalone workflow itself has no approval consumer, historical-CI
override, or deploy trigger (see
[Hosted deploy gate recovery checkpoint](#hosted-deploy-gate-recovery-checkpoint)
for how `Deploy hosted` consumes a pinned receipt).
No production secrets, write permissions, migration, image push or rollout are
available to this workflow. Its name is different from CI, so it does not match
Deploy hosted's workflow_run trigger. A CI workflow_dispatch also does not
satisfy the existing push-only historical gate.

Opening this implementation PR runs ordinary PR CI; its completion cannot
execute deployment because Deploy hosted requires a successful **push** CI.
Pushing this branch does not run Version, which listens to main. Merging is a
separate consequential action and is not authorized by verification work.
Historical `fa824f9a` remains failed and is outside today's deploy span.
See [Hosted deploy gate recovery checkpoint](#hosted-deploy-gate-recovery-checkpoint)
below for the current `d19adbff` recovery (spent `fa824f9a` pin removed),
and the earlier [recovery proposal](proposals/historical-postgres-gate-recovery.md)
for historical context.

Normal PR CI also exercises the optional collector against tiny standalone
pytest fixtures with real xdist/split, skips, expected failures and subtests.
Failed tests, failed subtests, collection errors and worker loss must not
publish successful inventories. These fixtures do not access app or seller data.


## Executable recovery-evidence inspection

`.github/scripts/recovery_evidence.py` itself remains a read-only receipt
validator (`deployment_authorized: false`, `require_runtime_authority` still
disabled). For how `Deploy hosted` consumes a pinned Descendant verification
receipt (pinned `bbb4c8fc` / `a1a55df0` for `d19adbff`), see
[Hosted deploy gate recovery checkpoint](#hosted-deploy-gate-recovery-checkpoint)
below. When invoked standalone, `recovery_evidence.py` fetches GitHub metadata
and artifact archives through bounded GET-only `gh api` calls and reads local
Git objects; it never fetches/checks out a different application revision, reads
production secrets, authenticates to Fly/Supabase, claims approval, creates a
Deployment object, dispatches workflows or deploys. A failed read has no
alternate transport or retry. Run from a complete repo with an already refreshed
`origin/main`; the inspector does not change refs.

Every binding is mandatory: repository name/ID, trusted verification workflow
ID, original target/tree, production base, workflow/CI blobs, verification
run/attempt, unique receipt artifact ID and archive SHA-256 digest. The checker
requires both the latest run and pinned attempt to be finally successful,
reconstructs source coverage from the original target's Git objects, validates
all exact-attempt jobs, downloads all six evidence artifacts, checks each
archive's provenance/digest and bounded single-file structure, recomputes both
database shard unions and requires the historical Postgres regression to pass.
It compares the receipt with those independent observations and rechecks the
latest run after downloads. Missing/expired artifacts, unknown extra evidence
artifacts, incomplete pagination, later reruns, wrong IDs/blobs/trees or altered
receipt/archives fail closed. Unrelated screenshot/report artifacts are ignored.

The exact green receipt observed November 20 is run `44662061597`, attempt 1,
artifact `14596571781`, digest
`sha256:4d0f38c8b8125c78810c0eecfb9301e74bd73a8e3f2df148cb38879c328ed791`.
It covers only target `b28ba4b361071373731b93bc49c12b4bf78326e9`, tree
`07ff8975695a4182c00c2605b5679d56e493c67b`, base
`b73c534707b9ef9bd70e4dc589b9c4b10863a852`. The docs-only #1659 merge
`644fa65095840f5662fa606baa68bf19ac1eceba` is a different SHA/tree and cannot
use that receipt as its own verification. No later main is selected implicitly.
Pins for a read-only inspection of that evidence are:

```bash
python3 .github/scripts/recovery_evidence.py \
  --repository btxbtwn/anteroom --repository-id 1183824317 \
  --workflow-id 422931456 \
  --target b28ba4b361071373731b93bc49c12b4bf78326e9 \
  --tree 07ff8975695a4182c00c2605b5679d56e493c67b \
  --production-base b73c534707b9ef9bd70e4dc589b9c4b10863a852 \
  --historical-failure fa824f9af17c7676458894df4b12c7af046b3e6c \
  --correcting-merge 5cc7fea11067baf1b93d1519b57b0fd93be2db19 \
  --workflow-blob a0a097906554252a26f35c957169ec6d0e3c93db \
  --ci-blob 3ee3a2df9cda1261e08aa55a2ee639ab9075c380 \
  --run-id 44662061597 --attempt 1 --receipt-artifact-id 14596571781 \
  --receipt-digest sha256:4d0f38c8b8125c78810c0eecfb9301e74bd73a8e3f2df148cb38879c328ed791
```

The pin carries its own `historical_failure` / `correcting_merge` pair (here the
spent `fa824f9a` / `#1653` pair). Module-level `verification_evidence.HISTORICAL`
/ `CORRECTED` name the next recovery (`d19adbff` / `6d72c5e3`) and must not
replace the archived pair when inspecting this receipt.

A successful standalone inspection explicitly returns `deployment_authorized: false`,
`runtime_authority: disabled` and `live_reconciliation: not performed`. On its
own, `recovery_evidence.py` does not authorize deployment, validate live
schema/Machines, or prove native, backup, or operational readiness;
`require_runtime_authority` rejects every input unconditionally.

The offline negative suite is `.github/scripts/test_recovery_evidence.py`; the
existing stdlib evidence-contract CI entry point also collects these tests.
It uses synthetic API/Git/ZIP fixtures and makes no network or application calls.
The CLI transport bounds output, memory-sized archives and wall time per command.

Earlier multi-reviewer approval ideas (dedicated approval environment,
two-person `prevent_self_review`, durable claim refs) remain archived in
[the recovery proposal](proposals/historical-postgres-gate-recovery.md) for
future multi-maintainer use and are not installed on this single-owner
repository.


## Hosted deploy gate recovery checkpoint

For single-owner recovery on `btxbtwn/anteroom`,
`.github/scripts/wait-for-tested-code.py` live-verifies the pinned
`Descendant verification` receipt (`bbb4c8fc560da5f81bedfd349714ff30a4c28a1d`,
run `44992383513`, attempt `1`, artifact `14743680528`) via
`recovery_evidence.verify` only when all of the following hold inside
`Deploy hosted`'s serialized `deploy-hosted` concurrency group:

- Recorded `production_commit` equals `a1a55df0789371ad271e443897b72c2fd6ff0c3e`,
  corroborated by HTTPS `HEALTH_URL` reporting `status: ok`,
  `service: anteroom-hosted`, and version `0.1.1091`.
- The only non-green historical commit is `d19adbff` (`conclusion == failure`),
  preceding correcting merge `#1728` (`6d72c5e3`) and exact checkpoint `bbb4c8fc`
  on `origin/main` first-parent history at or before the target `HEAD`.
- Every other non-release commit in `production..HEAD` — including every commit
  merged after `bbb4c8fc` — passes its own push `CI` first; the receipt is never
  applied to any other SHA.
- Before any build push, migration, category-tree write, or Fly rollout,
  `Reconcile live production base for recovery` (`--reconcile-live-base`)
  re-verifies that recorded `production_commit` is still `a1a55df0`, live
  `HEALTH_URL` still reports `0.1.1091`, **both** Fly `app` (web) and `worker`
  Machines are `started` on exact image tag `v0.1.1091-a1a55df0` and digest
  `sha256:cdc7323a7eb2f8ac00f2a9f3e96bd737abec9ce16c0aa2de7cdb3a77e14ebd7e`, and
  the live Postgres Alembic revision (`python -m alembic current` over
  `verify-full` TLS) is still `85dab8d3327c`.
- Workflow permissions remain unchanged (`contents: read`, `actions: read`,
  `checks: read`); no new environments, rulesets, or tokens are added.

Once a single deploy mutates the database past `85dab8d3327c`, moves either
Fly `app` or `worker` Machine off `v0.1.1091-a1a55df0`, or records a newer
`Deployed commit` notice, the pinned checkpoint fails closed on any subsequent
run and cannot be reused against partially mutated state. Post-rollout,
`Confirm every Machine runs the new image` passes `--require-group app
--require-group worker` to `check-fly-machines.py`, so both web and worker
Machines must be `started` on the new image before `Deployed commit` is
recorded. Remove `HISTORICAL_RECOVERY_CHECKPOINT` in a cleanup PR once
production is verified past `a1a55df0` (artifact `14743680528` expires about
30 days after 2026-11-22). The offline gate-recovery tests live in
`.github/scripts/test_wait_for_tested_code.py` and run in `Repo checks`.

## CI timing and coverage

In a November 23 sample of eight successful CI runs that selected Studio
browser tests, median elapsed time from run creation to the last job finish
was 36.2 minutes. Completed jobs used a median of 73.2 runner-minutes per run.
The sandbox browser job took a median of 31.9 minutes and was the longest job
in all eight. Runner-minutes are summed job start-to-finish times from
GitHub Actions. The sample covers successful jobs in runs 45197680891,
45195598029, 45187548996, 45187288443, 45186367430, 45185833788, 45184199851
and 45183254721.

Install the engines each browser suite uses. The hosted job runs desktop and
phone Chromium. The offline sandbox also runs phone WebKit. The sandbox
assistant's slow-stream fixture applies to the current user turn so a test can
exercise cancellation and queueing without paying for a second slow reply
on an ordinary follow-up.

One local before/after pair ran the same 12 cases from
`e2e/chatStreaming.e2e.ts` across all three projects. Summed successful case
durations fell from 152.6 to 81.5 seconds. The baseline also needed two retries,
and the updated run passed on its first attempts. Compare job timestamps again
on GitHub-hosted runners to measure the effect on CI.

The two sandbox shards run on one GitHub machine with one browser worker and
one disposable sandbox each. They share dependency installation and use
distinct API ports, web ports, data folders, Vite caches and report paths.
Each shard must pass. The combined executed inventory must cover the full
collection exactly once across desktop Chromium, phone Chromium and phone
WebKit. `check.sh` runs the same shards serially on the local machine.
The local validation matched all 312 cases exactly once, with 276 passes,
36 skips and zero retries. The two runs took 8.0 and 8.3 minutes on the Mac.

The seven-day profile records keyed hashes of Playwright case and file identities,
project names, retry outcomes, skips, expected failures, attempt durations and
checkout, lock and runner provenance. The private key stays on the runner,
and identities match within one job. Runtime-derived sensitive titles must stay
out of public evidence even when hashed. Install steps expose Python, npm,
browser OS dependencies and browser download timings separately for both jobs.

Postgres job medians in the same baseline were 13.0 and 6.9 minutes. The
checked-in `pg-file-durations.json` contains per-file average case durations
from run 45197680891. Expanding the averages to the current collection let
pytest-split redistribute the recorded work from 1,680.9 and 571.7 test-seconds
to 1,128.1 and 1,124.5 in a replay. These sums include parallel test work and
are a planning estimate. Unknown files get pytest-split's average estimate.
The updated collection contained 6,032 distinct tests split across 2,786 and
3,246 items.
Every execution replaces the seed with fresh timings before artifact hashing
and exact union validation. Refresh the seed from a complete successful
profile by matching its digests to collected test IDs and averaging setup,
call and teardown seconds by test file.

The frontend job pays for the TypeScript check once, then builds both bundles.
The Postgres jobs reuse the npm cache for the locked cloud runner dependencies.

For a later hosted A/B, use the same frozen source, locks, toolchain and runner
image. Compare the full one-worker run with two isolated one-worker shards.
Verify the exact disjoint case union on the executing runners and report skips,
expected failures, retries and missing cases. Measure time to green from run
creation including queue, and sum job start-to-finish seconds across all
executed jobs and attempts, including setup and validation. Report any rounded
runner minutes as estimates. Billing data is needed for monetary claims.
