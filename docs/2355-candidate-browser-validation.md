# PR #2355 — full isolated browser validation

## Disposition

Full three-project sandbox execution passed at candidate
`0b453698515e90c651707dd1f38bd498d9db95b9` in non-required
[run 46173614538](https://github.com/btxbtwn/anteroom/actions/runs/46173614538).
Keep the PR a draft and unarmed: independent review is missing, and the cost
tradeoff needs comparable repeat measurement before adoption. No production
deployment, billing/runner provisioning, repository settings or required-check
settings were changed. No second heavy run or cohort PR was started.

## Exact identity and scope

The isolated wrapper is commit `2a1fc3db5a373054ac89376e00baeb98cc47bd7a`
on `2348-browser-ci-comparison`. Each runner checked out the exact candidate
separately. Metadata records the actual tested SHA and wrapper event SHA as
distinct fields; GitHub's event SHA was not overridden. The wrapper is branch
only, has a distinct workflow name and read-only contents permission, and
references no secrets or production environment. It cannot enter the normal
main-push CI-to-deployment path.

All projects and the aggregate agree on source tree
`ac636b5d17f5ebe9e1ff052ee3018dd9a5ba1bf6`, candidate CI workflow blob
`e8280e5bfa0ef557c8e48b346c38515d0018b716`, both raw lock hashes, run and
attempt, Node v22.23.5, Playwright 1.65.1, x64 architecture and runner image
`ubuntu26 / 20261122.9.1`. Lock hashes were independently checked against the
local exact candidate. Runtime and runner image match the older baseline.

This is full sandbox browser execution through an isolated wrapper, not a
required/full-CI pass. The wrapper invokes the candidate's exported collection
and evidence validators, including its strict historical WebKit binding.
It does not invoke the candidate's CI workflow, its hosted suite or backend
suites. It does not exercise the normal CI CLI's event-SHA check in a GitHub
run; that CLI was covered by the earlier exact-head local integration probe.
The published receipt explicitly says `release_evidence: false`; workflow,
branch, event and artifact provenance also prevent release reuse.

## Complete results and diagnostics

| Project | Collected | Passed | Expected skips | Retried |
| --- | ---: | ---: | ---: | ---: |
| Desktop Chromium | 120 | 115 | 5 | 0 |
| Phone Chromium | 120 | 103 | 17 | 0 |
| Phone WebKit | 120 | 103 | 17 | 0 |
| Total | 360 | 321 | 39 | 0 |

The candidate validator verified the complete expected inventory against all
three executed project partitions with no missing, duplicated or foreign
cases. Each project kept its private identity key on its runner; no key was
published. The historical phone-WebKit word-by-word streaming regression
passed on its first attempt without skip or retry.

Downloaded and checked all seven artifact ZIPs for integrity: three project
evidence bundles, three HTML report bundles, and the aggregate bundle containing
the summary and non-release browser inventory. Every report includes an HTML
index and screenshot entries. Traces retain their normal failure-only policy;
no failure/retry trace was generated in this passing run.

Reproduced the published browser inventory locally using the exact candidate's
`verifyProjectEvidence` and `buildVerificationInventory`. It matched exactly.
On the full downloaded inventories, 18 mutations were rejected: failed,
cancelled, skipped or absent dependency success; missing project/case; duplicate
case; wrong project; missing or unsuccessful terminal attempt; altered source,
tree, workflow, npm/Python locks, run, attempt or wrapper identity.

## Observed timings and runner consumption

| Sandbox path | Elapsed including queue/setup/final evidence | Total runner time |
| --- | ---: | ---: |
| Older two-process baseline 46147855712 | 21m22s | 21m10s |
| Earlier isolated three-project trial 46165858577 | 10m59s | 28m47s |
| Exact candidate 46173614538 | 15m32s | 37m16s |

| Candidate job | Runner queue | Setup | Browser execution | Upload/cleanup | Runner time |
| --- | ---: | ---: | ---: | ---: | ---: |
| Desktop | 2s | 96s | 597s | 6s | 699s |
| Phone | 2s | 89s | 511s | 8s | 608s |
| Phone WebKit | 2s | 86s | 819s | 9s | 914s |
| Aggregate evidence | 1s | Included in job total | N/A | Included in job total | 15s |

Against the older baseline, observed elapsed decreased by 5m50s (27.3%) and
runner time increased by 16m06s (76.1%). These are job seconds, not rounded
invoiced billing minutes. Elapsed runs from workflow event creation to the
last evidence-job finish. Baseline elapsed includes 10s of preceding selector
work and 2s of runner queue; its setup/browser/tail were 129s/1130s/11s.
Candidate runner totals include all three projects and the final evidence job.
Neither comparison includes unrelated whole-CI or deployment work.

The application and browser-test source matches the older baseline. The raw
lock hashes differ: both locks changed only their root Anteroom version, not
the resolved dependency graph. Candidate CI/helper/test source differs and
the earlier trial used a different evidence layout. These differences prohibit
result reuse and prevent calling this an exact-input causal benchmark. One
candidate run also does not establish median/p95 or stable cost. The two
three-project trials show substantial variation; retain the draft pending
repeat comparable timing and explicit assessment of the higher runner cost.

## Review findings and remaining work

- The same-attempt fence deliberately rejects partial GitHub reruns that reuse
  other projects' earlier-attempt artifacts. A successful retry needs all three
  project inventories from one attempt. Playwright's in-project retry remains
  unchanged. Do not relax this fence without exact-input proof.
- No independent review is available: the read-only stored PR guide is empty,
  there are no review comments, and draft review bots were skipped. Local
  review and successful execution do not substitute for independent review.
- The actual required-CI dependency graph, hosted execution and remaining full
  CI checks have not been run by this isolated browser wrapper. Keep required
  protection and source identity intact when doing that validation later.

PR #2355 remains at `0b4536985`, draft/unmerged with auto-merge off. This
verification is not a staging gate for another PR or permission to merge it.
