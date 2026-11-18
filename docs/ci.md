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
verified as live. Native changes are listed and require separate pinned native
receipts; hosted image validation, live schema/Machine reconciliation, backup
readiness and production load measurements are not established by this run.
There is no approval consumer, historical-CI override or deploy integration.
No production secrets, write permissions, migration, image push or rollout are
available to this workflow. Its name is different from CI, so it does not match
Deploy hosted's workflow_run trigger. A CI workflow_dispatch also does not
satisfy the existing push-only historical gate.

Opening this implementation PR runs ordinary PR CI; its completion cannot
execute deployment because Deploy hosted requires a successful **push** CI.
Pushing this branch does not run Version, which listens to main. Merging is a
separate consequential action and is not authorized by verification work.
Historical `fa824f9a` remains failed. Clearing that gate, any production recovery,
and the policy for subsequent queued automatic deploys require separate review
and approval; see the [recovery proposal](proposals/historical-postgres-gate-recovery.md).
