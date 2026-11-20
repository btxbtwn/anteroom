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

The reusable `.github/workflows/dependency-audit.yml` audits the locked Python
packages for dev, hosted and Mac builds when their dependency inputs change.
It also runs daily at 08:23 UTC and supports manual dispatch, so newly reported
vulnerabilities are detected even when the lockfile has not changed. Dependency
PRs must pass the audit before merging; unrelated PRs do not wait on it.

The repository rules require passing `CI`, without requiring the PR branch to
be up to date with `main`. Update a PR for actual conflicts, failed checks or
a needed dependency, not solely because another PR merged. A passing PR can
still interact badly with another change; the checks on `main` verify the
merged result. Pushes to `main` never cancel each other's CI, and the hosted
deploy waits for CI to pass on every merge since the commit production runs
(`.github/scripts/wait-for-tested-code.py`), so a broken combination stops
before production even when a later merge's narrower checks pass. The automatic branch-update workflow is disabled.

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
Historical `fa824f9a` remains failed. See
[Hosted deploy gate recovery checkpoint](#hosted-deploy-gate-recovery-checkpoint)
below for the reviewed single-owner checkpoint now used by `Deploy hosted`,
and the earlier [recovery proposal](proposals/historical-postgres-gate-recovery.md)
for historical context.

Normal PR CI also exercises the optional collector against tiny standalone
pytest fixtures with real xdist/split, skips, expected failures and subtests.
Failed tests, failed subtests, collection errors and worker loss must not
publish successful inventories. These fixtures do not access app or seller data.


## Executable recovery-evidence inspection

`.github/scripts/recovery_evidence.py` itself remains a read-only receipt
validator (`deployment_authorized: false`, `require_runtime_authority` still
disabled). For how `Deploy hosted` consumes its verified result for the single
pinned `b73c5347 -> b28ba4b3` checkpoint, see
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
  --workflow-blob a0a097906554252a26f35c957169ec6d0e3c93db \
  --ci-blob 3ee3a2df9cda1261e08aa55a2ee639ab9075c380 \
  --run-id 44662061597 --attempt 1 --receipt-artifact-id 14596571781 \
  --receipt-digest sha256:4d0f38c8b8125c78810c0eecfb9301e74bd73a8e3f2df148cb38879c328ed791
```

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

For single-owner recovery on `btxbtwn/anteroom` (whose active protections are
ruleset `main: passing CI required` and `production`/`release` branch policies
restricted to `main`, with no two-person review requirement),
`.github/scripts/wait-for-tested-code.py` live-verifies the pinned
`Descendant verification` receipt (`b28ba4b361071373731b93bc49c12b4bf78326e9`,
run `44662061597`, attempt `1`, artifact `14596571781`) via
`recovery_evidence.verify` only when all of the following hold inside
`Deploy hosted`'s serialized `deploy-hosted` concurrency group:

- Recorded `production_commit` equals `b73c534707b9ef9bd70e4dc589b9c4b10863a852`,
  corroborated as a live backup by HTTPS `HEALTH_URL` reporting `status: ok`,
  `service: anteroom-hosted`, and version `0.1.1011`.
- The only non-green historical commit is `fa824f9a` (`conclusion == failure`),
  preceding correcting merge `#1653` (`5cc7fea1`) and exact checkpoint `b28ba4b3`
  on `origin/main` first-parent history at or before the target `HEAD`.
- Every other non-release commit in `production..HEAD` — including every commit
  merged after `b28ba4b3` — passes its own push `CI` first; the receipt is never
  applied to any other SHA.
- Live verification runs immediately before build, migration, and rollout, using
  only the existing read-only workflow permissions (`contents: read`,
  `actions: read`, `checks: read`).

Once a single deploy succeeds and records a newer `Deployed commit` notice (or
live `/api/health` moves off `0.1.1011`), the pinned checkpoint never matches
again and ordinary gating applies unchanged. If a recovery deploy advances live
web Machines off `0.1.1011` and then fails before emitting `Deployed commit`,
subsequent deploys intentionally fail closed until an operator inspects Fly
Machines and DB schema state and completes/records the rollout. Remove
`HISTORICAL_RECOVERY_CHECKPOINT` in a cleanup PR once production is verified
past `b73c5347` (artifact `14596571781` expires `2026-12-20`). The offline
gate-recovery tests live in `.github/scripts/test_wait_for_tested_code.py` and
run in `Repo checks`.
