# Browser CI comparison — completed bounded trial

Tracks #2348; one-active-job coordination follows #2349. This investigation
does not change required checks, deployment, production settings, or CI reuse.

## Release prerequisite

PR #2238 merged as `c86a1f2845d0837ab0891d47395afef45de3a215`.
Main CI run 46161541761 succeeded. Normal Deploy hosted run 46162531985
succeeded and shipped that exact commit as Fly release 488, image
`v0.1.1293-c86a1f28`. Both web and worker checks passed, public `/api/health`
reported 0.1.1293 and `/api/ready` returned ok. No manual release was started.
The merge changed only `anteroom-ios/` and `docs/iphone-parity.md`;
`.github/workflows/version.yml` triggers on `changelog.d/**`, so no version
wrapper commit was expected. The existing version number was retained.

## Observed baseline

Recovered the published `sandbox-browser-profile-attempt-1` artifacts and
GitHub run/job metadata for these four runs. Every profile contains 360 unique
case IDs across desktop, phone Chromium and phone WebKit, with zero retries.
Case identifiers are salted per run: matching counts across different source
commits does **not** prove identical case unions or reusable test results.

| Run | Setup seconds | Browser step seconds | Tail seconds | Runner minutes |
| --- | ---: | ---: | ---: | ---: |
| [46134980299](https://github.com/btxbtwn/anteroom/actions/runs/46134980299) | 85 | 1458 | 7 | 25.83 |
| [46137055365](https://github.com/btxbtwn/anteroom/actions/runs/46137055365) | 120 | 1081 | 11 | 20.20 |
| [46145912579](https://github.com/btxbtwn/anteroom/actions/runs/46145912579) | 89 | 1308 | 9 | 23.43 |
| [46147855712](https://github.com/btxbtwn/anteroom/actions/runs/46147855712) | 129 | 1130 | 11 | 21.17 |

Setup runs from job start to browser-step start, including Bridge checks.
Tail includes artifact uploads and cleanup; upload steps themselves take
4–6 seconds in these runs. Runner minutes are job elapsed minutes, not a
billing statement. Run creation to browser-job start is 12–16 seconds and
includes prerequisite jobs; it must not be labelled pure runner queue time.

The current `check-browser-shards.sh` starts two processes concurrently on
one runner, each with one worker and a separate sandbox. It is not a single
Playwright process configured with two workers.

## Estimates, not experiment results

Using the sum of all recorded attempts per shard, ideal equal balancing saves
63–85 seconds on these four profiles. This is a lower-bound scheduling model:
it ignores indivisible file groups, sandbox startup, contention and retries.
It is not a measured improvement, and need not match a wall-clock balancing
estimate from another analysis.

Replaying each project's recorded case durations on three independent runners,
and charging each the baseline setup and tail, gives roughly 15–18 minutes
elapsed and 40–49 total runner minutes, versus the measured 20–26 elapsed
minutes on one runner. These estimates assume simultaneous starts and unchanged
case durations. Independent runners may change contention, setup and queue
time. They do not establish a performance or cost benefit.

## Isolated pilot acceptance criteria

1. Compare sequential experiments on one exact source tree, lockfiles,
   Playwright version and workflow inputs. Record runner image and hardware.
   Run the existing two-process topology first, then desktop, phone and
   phone-webkit on three independent runners. Do not overlap another suite.
2. Enumerate the full case inventory at that source. Verify the observed union
   equals it exactly with no duplicate or missing cases, including expected
   skips. Existing per-run salted IDs cannot be joined across runs without
   establishing a common verified inventory. Keep Chromium/WebKit and hosted
   coverage; a sandbox comparison cannot substitute for hosted CI.
3. Retain retry attempts, GitHub annotations, HTML reports, traces and
   screenshots with distinct project and attempt artifact names. Aggregate
   verification must fail for any failed, cancelled, missing or incomplete
   project or evidence artifact. Do not grant reuse or modify release gates.
4. Measure each job's queue, setup, browser execution and upload/cleanup from
   timestamps; report elapsed critical path and total runner minutes. Repeat
   sequentially before drawing median/p95 conclusions. Four historical samples
   do not establish a reliable p95 or an empirical before/after comparison.

## Trial result and decision

**Recommend the three-project topology as the next reviewed CI optimization.**
The bounded experiment demonstrated a shorter wait at a higher runner cost.
It does not authorize reusing results or changing required CI in this branch.

[Trial run 46165858577](https://github.com/btxbtwn/anteroom/actions/runs/46165858577)
passed at wrapper commit `d41138adcdfd24582e98dcefbc8e035113364ff5`.
Its three independent runners checked out the exact latest baseline source,
`7f81013122e8e50636c3d3e516757536408e2dcc`, rather than testing the wrapper's
application code. SHA, tree, original CI workflow blob, both lock hashes,
Playwright 1.65.1, Node v22.23.5, x64 architecture and runner image
`ubuntu26 / 20261122.9.1` matched the baseline and every project artifact.
Job logs also confirm Python 3.12.16 on the baseline and all three trial jobs.

| Observed metric | Baseline 46147855712 | Trial 46165858577 |
| --- | ---: | ---: |
| Elapsed, event creation to complete sandbox evidence | 21m22s | 10m59s |
| Total runner time, including trial evidence job | 21m10s | 28m47s |
| Runner queue | 2s | 1s per project; 2s for evidence job |
| Setup before browser execution | 129s | 70–77s per project |
| Browser execution, critical path | 1130s | 556s |
| Upload/cleanup after execution | 11s | 4–8s per project |

Baseline elapsed includes 10s of prerequisite work before the browser job was
created. Trial elapsed includes project scheduling, all setup, execution,
uploads/cleanup and final aggregate evidence validation. Baseline excludes
unrelated CI jobs finishing later; this compares the sandbox browser path,
not the whole CI or hosted deployment. Trial evidence validation used another
14 runner seconds. These are observed job minutes, not invoiced rounded minutes.

The observed elapsed reduction is 10m23s (48.6%), with 7m37s (36.0%) more
runner time. Setup was also faster, so not all of the elapsed difference can
be attributed to topology. This is one historical baseline and one trial;
it does not establish median/p95, repeatability or a causal benchmark.
No runner hardware benchmark was available for the original baseline.

### Coverage and retained diagnostics

- The source validator verified the exact full inventory against the union of
  the three executed project inventories: 120 cases each, 360 total, with no
  omissions or duplicates. Each project's unfiltered full inventory also
  matched the other two exactly within this run's common identity namespace.
- Outcomes match the baseline: desktop has 5 expected skips, phone and
  phone-webkit have 17 each; the other 321 cases passed. All projects had zero
  retries and no expected failures. Skips were retained, not counted as passes.
- Downloaded and checked all seven artifact ZIPs for integrity: three evidence
  bundles, three HTML report bundles and the aggregate receipt. Each HTML
  report contains `index.html`; the bundles contain 125 desktop, 126 phone and
  127 phone-webkit PNG entries. There were no trace ZIPs because no failure
  or retry occurred; the existing retain-on-failure setting remains intact.
- Reproduced the GitHub receipt locally from the downloaded inventories using
  the pinned source's validator; the result exactly matched the published
  receipt. Targeted validator/safety tests passed (19 tests), and actionlint
  and JavaScript syntax checks passed. Actual local Playwright enumeration
  separately proved the three project selections partition the 360 cases.

### Scope and disposition

Only this experiment ran; no second heavy suite, rerun, PR, merge or auto-merge
was started. The trial workflow is branch-only, separately named, read-only,
has fixed job/test timeouts, and references no production environment or
secrets. It uses ordinary existing GitHub-hosted runners, with no billing or
runner provisioning change. Required CI, release gates, hosted coverage and
production settings remain unchanged. This trial cannot supply release evidence.

The experiment is complete and no heavy workflow remains active. A future
reviewed adoption should preserve the three-project exact-union gate,
per-project retries/annotations/reports and the existing hosted suite, and
accept the measured cost tradeoff explicitly. This work is not a staging gate
for the existing PR backlog.
