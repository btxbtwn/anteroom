# Performance goals

Every fix, feature, refactor and update should move Anteroom toward the speed
and responsiveness exemplified by [McMaster-Carr](https://www.mcmaster.com/)
and [NextMaster](https://github.com/stratolark/NextMaster). This applies across
the web, Mac and native iPhone apps, including the APIs, storage and background
work behind them. Performance is part of correctness, not a final polish pass.

These are **aspirational performance references, not visual design references**.
The goal is to keep the seller moving with immediate feedback, fast navigation,
search and filtering, and useful content that stays available while other work
finishes. Do not copy their colors, layout, framework or infrastructure merely
to resemble them. [DESIGN.md](../DESIGN.md) remains the authority for the web
UI's visual and interaction conventions. Its web components, CSS tokens and
layout rules do not automatically prescribe native SwiftUI styling; use the
[native app's conventions](../anteroom-ios/README.md#interface) and platform
guidance while pursuing the same cross-platform responsiveness goals.

## How to use the references

McMaster-Carr is the behavioral reference: browsing and narrowing a large
catalog should feel direct, without repeatedly taking away the workspace.
The dated browsing observations in [responsiveness.md](responsiveness.md#references)
describe what was actually observed; they are not a benchmark or guarantee.

NextMaster is an implementation study. Its
[README at the inspected commit](https://github.com/stratolark/NextMaster/blob/6db286afbe57c19b7d0be2cb97e929b3f99d58b8/README.md)
describes precomputed page shells with dynamic data streamed separately.
Translate the useful principle into Anteroom's existing stack: make the usable
screen available independently of slower data. Evaluate caching and selective
prefetching where they remove waits. Neither that README nor a fast demo proves
Anteroom's production performance, native behavior or concurrent capacity.
Retain our build checks and architecture constraints.

## Apply this to every change

- Identify the affected seller journey and its critical path before changing
  it: opening an app or draft, searching, scrolling inventory, editing, saving,
  switching marketplace forms, uploading photos or running concurrent jobs.
  Backend-only and dependency changes can slow these paths too.
- Keep already loaded content and unsaved input available during refresh.
  Acknowledge input without waiting for remote work; distinguish an accepted
  action or optimistic edit from a confirmed durable save. Never show a
  marketplace action as successful before it is confirmed.
- Keep expensive work off the browser or native main thread and shared server
  event loops. Async syntax alone does not make blocking work nonblocking.
  Follow the database-session, external-call deadline and cancellation rules
  in [AGENTS.md](../AGENTS.md#engineering-principles). Unrelated drafts and
  sellers must remain usable while model, photo or network work runs.
- Reduce work on the critical path before increasing capacity: measure query
  counts and round trips, payload and image sizes, repeated parsing, redundant
  fetches and render work. Reuse safe cached data and prefetch likely next
  reads selectively; do not download every possible form or start mutations
  speculatively. Bound cache and prefetch resource use.
- Preserve seller isolation, cache invalidation, sign-out cleanup, atomic
  saves, stale-edit protection and explicit marketplace approval. Faster stale
  data, cross-account cache leaks or lost edits are not improvements.
- Test slow and disconnected paths where relevant. Keep available local or
  cached content usable, preserve edits, and explain what needs connectivity.
  Do not imply that an offline save has synced or add persistent queues for
  marketplace actions as a performance shortcut. This is not a claim that
  all app features currently work offline.

## Measure improvement and prevent regressions

[AGENTS.md](../AGENTS.md#responsiveness-and-concurrent-work) owns the shared
numeric targets; [responsiveness.md](responsiveness.md#targets-and-verification)
owns the measurement details, dated results and remaining gaps. Do not invent
a second set of thresholds here. Those targets concern input acknowledgement
and cached navigation, not a promise that a full model job, upload or cold
network request finishes in the same time.

For a change that can affect runtime performance:

1. Capture a before/after baseline for the affected journey using the same
   device or simulator, build mode, data size, network conditions and cache
   state. Separate cold and warm runs; include representative slower hardware
   or network conditions. A fast development machine alone is insufficient.
2. Measure input-to-visible-feedback and time-to-usable-content separately
   from API latency, queue wait, background work and durable completion.
   Report sample counts, p50/p95 where samples support them, errors/timeouts
   and tested load. Small samples are observations, not reliable tail-latency
   claims. Record instrumentation gaps rather than treating missing samples
   as zero latency.
3. Exercise the affected path while independent work is running, including
   multiple drafts and sellers when the backend is involved. Check that
   navigation and edits remain responsive and survive reload; cover relevant
   cancellation, retry, stale responses and recovery after interruption.
   Verify native changes in the native runtime, not only a phone-sized browser.
4. Preserve or add the narrowest repeatable regression check for the actual
   cause: for example a query-count limit, bounded payload, cache reuse check,
   or a concurrent-work test. Choose timing budgets from the shared targets
   and a repeatable baseline, allowing for measured test noise rather than
   asserting fragile timings on shared CI hardware.
5. Do not silently accept a reproducible slowdown because an existing path
   already misses its target. Fix it or explicitly document the tradeoff,
   evidence and remaining work for review. A missing baseline is a gap, not
   proof of no regression. Documentation-only changes can state that no
   runtime path changed without manufacturing benchmark results.

Include the affected platforms and journey, baseline and after results,
conditions, checks run and unresolved gaps in the change summary. Keep lasting
measurements and follow-up findings in [responsiveness.md](responsiveness.md),
not duplicated here. Hosted capacity changes additionally follow
[hosted-production.md](hosted-production.md). Green functional tests establish
neither production latency nor unlimited concurrency.
