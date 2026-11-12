# CI and merging

The required `CI` check aggregates the jobs in `.github/workflows/ci.yml`.
All selected jobs must succeed. Jobs for unchanged inputs may skip; a failed
selector, failed job or cancelled job prevents `CI` from passing.

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
