# Browser CI comparison — initial evidence

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

This initial investigation is complete; the live three-runner pilot has not
run. No active workflow has been changed, no further PR is armed, and this
experiment is not a staging gate for the existing PR backlog.
