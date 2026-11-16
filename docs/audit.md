# Daily audit

A scheduled agent runs this audit on the seller's Mac every day at 3 AM. It
tests the web app (local and hosted), the iPhone app and the Mac app end to end,
checks every feature merged since the last run, files one report, and opens fix
PRs for the bugs it finds. It never merges.

This file holds **instructions only**. Results go in the daily GitHub issue.
Change this file through a PR (see **Keeping this file current**).

## Safety rules

These override everything below.

- Sandboxes only: `./sandbox.sh`, `./sandbox.sh --hosted`,
  `anteroom-ios/test-sandbox.sh`, and the Mac app with `ANTEROOM_SANDBOX=1`
  and a temporary data folder. Never touch the seller's real data folder
  (`vendoo-studio/data/`, `~/Library/Application Support/Anteroom`), the real
  app on port 4318, production Supabase or Fly.
- Never publish, delist, update or delete a live listing. The sandbox refuses
  those routes, and the audit confirms that it does. Never call a real
  marketplace, eBay or Etsy API.
- Never deploy, release, bump the version or edit `CHANGELOG.md`. Merge
  only through the rule in **Fixing bugs**; leave every other PR for the
  seller.
- Work in your own worktree off a fresh `origin/main`:
  `git fetch origin && git worktree add -b audit/<YYYY-MM-DD> ../anteroom-audit-<YYYY-MM-DD> origin/main`.
  Other sessions share the main checkout. Remove the worktree at the end unless
  a PR branch still needs it.
- Never start the installed app's binary yourself, not even with `--version`:
  without `ANTEROOM_SANDBOX=1` it opens the seller's real data, connects their
  Chrome and the relay, and reads marketplaces. Read the installed version
  with `defaults read /Applications/Anteroom.app/Contents/Info.plist CFBundleShortVersionString`,
  and start the app only through `smoke-packaged-sandbox.py`.
- Run one sandbox at a time, or give each its own ports
  (`VENDOO_STUDIO_PORT`, `VENDOO_STUDIO_WEB_PORT`). The iPhone script always uses
  4320, so stop the hosted sandbox before running it. If a developer's sandbox
  already holds a port, pick other ports; do not stop their process.
- Run heavy suites one at a time. On 2026-11-12, four fix branches running
  `./check.sh --fast` alongside `./check.sh --all` pushed the load past 19:
  Playwright's web server missed its 120 s start, every iPhone test timed
  out, and the backend run took two hours. Finish the smoke first, then
  check fix branches one after another. Keep fix worktrees on a short path
  (`../anteroom-fix-<name>`), not the scratchpad: macOS caps Unix socket
  paths at 104 bytes.

## Each run

1. **Find the starting point.** The newest open or closed issue labelled
   `audit` records the commit it audited. Run `git log <that sha>..origin/main`
   and read each merged PR (`gh pr list --state merged --search "merged:>=<date>"`).
   With no earlier issue, treat the last 7 days as new.
2. **Smoke.** Do this every day (see **Smoke**).
3. **New features.** Test each feature merged since the last run (see
   **New features**).
4. **Deep rotation.** Test today's area from **Rotation** (weekday by local
   date).
5. **Review.** Read the diff of every PR merged since the last run, split by
   area among subagents when there are many. This finds more bugs than the
   hand checks. Confirm each suspected bug with a failing test or a sandbox
   reproduction before reporting it, and give its confidence.
6. **Fix.** Open a PR for each bug you can fix (see **Fixing bugs**).
7. **Grow the tests.** Turn one hand check from today into a Playwright,
   XCUITest or pytest test in CI, and fix one test from **Flaky tests**. Each
   goes in its own PR.
8. **Report.** File one issue, and one `audit-finding` issue per unfixed bug
   (see **Report**).
9. **Update this file** when a run teaches something (see **Keeping this file
   current**).

If a step cannot run (Xcode missing, Simulator broken, sandbox will not start),
record it as **not run** with the error and keep going. Never report a step as
passing when it did not run.

## Smoke

Every run, in this order:

1. `./check.sh --all` in the audit worktree after setup
   (`cd vendoo-studio && python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]" && npm ci`).
   Use Python 3.12, CI's version: a newer `python3` (3.14 on the seller's Mac)
   cannot install `pgserver`. Put `vendoo-studio/.venv/bin` first on `PATH`.
   This also runs `npm run e2e` (local web, desktop and phone),
   `npm run e2e:hosted` (hosted web, two sellers) and, on macOS,
   `./anteroom-ios/test-sandbox.sh`, so steps 2 to 4 need no separate run
   when it covered them. The whole run takes about 40 minutes on the
   seller's Mac. `hosted-e2e/signin.e2e.ts` "in the Mac app, password resets
   open in the browser" fails about one run in three at its Account menu step
   (the menu is not open); rerun it alone before reporting it.
   On macOS, **Anteroom types** fails on PyObjC symbols
   in `desktop.py`, `macos_keychain.py` and `photo_edits.py` that CI's Linux
   job never sees; report any other type error. Do not run `basedpyright` on
   single files: it rewrites `.basedpyright/baseline.json`.
2. `cd vendoo-studio && npm run e2e` (local web, desktop and phone sizes).
3. `cd vendoo-studio && npm run e2e:hosted` (hosted web, two sellers).
4. `./anteroom-ios/test-sandbox.sh` (iPhone XCUITests in the Simulator). Run
   it while nothing else heavy runs: under load its 5 to 30 second waits time
   out on different tests each run. Before reporting a failure, rerun it alone
   and compare with the latest iPhone job on `main` (`gh run list --workflow ios.yml --branch main`).
   If several tests fail at their first step and the UI hierarchy shows the
   app still on the Assistant tab, the launch helper's tab tap was dropped
   (fixed by #1417); check that fix is on `main` before reporting them.
5. Mac app:
   `vendoo-studio/.venv/bin/python vendoo-studio/scripts/smoke-packaged-sandbox.py /Applications/Anteroom.app`.
   This checks the installed release in sandbox mode on a temporary data
   folder. Note its version. The release may be behind `main`; that is expected.
   Before and after, check that
   `~/Library/Application Support/Anteroom/logs/Anteroom.log` was not created
   or updated (sandbox must keep logs under `VENDOO_STUDIO_DATA_DIR`). The
   seller's own app may be running and writing that log, so compare its size
   and time with `stat` and look for sandbox paths in any new lines. Unset
   any leftover `VENDOO_STUDIO_PORT` / `ANTEROOM_SANDBOX` from that run before
   starting `./sandbox.sh --hosted` (hosted defaults to 4320; an inherited
   `4321` makes it fail to bind).
6. One core journey on each surface: create a draft, add photos, generate,
   edit a field, save, reopen, and try to publish (it must be refused).
   `node docs/audit/journey.mjs <local|hosted> <desktop|phone> <out-dir>` runs
   it on web against the running sandbox. It prints one JSON line (edit kept,
   publish statuses, Bob's isolation on hosted, page errors) and saves a
   screenshot per step. Run it from a worktree with `vendoo-studio/node_modules`
   installed. Fix the script when the UI changes, in the same PR as the
   change if you can. Drive by hand only what it doesn't cover. Surfaces:
   - local web (`./sandbox.sh`, UI 5174) at desktop and phone sizes. If the
     browser tools cannot connect, drive it with a Playwright script kept
     outside `e2e/` (a running suite would pick it up) and its own config;
     `e2e/fixtures.ts` shows the selectors. On a phone the editor stages
     photos in the composer and its send button starts generation.
     Wait for generation to finish (the chat says "Listing generated." and
     nothing is still filling) before editing: an edit made while it is still
     saving gets "Edits not saved: This listing changed", which is the
     stale-edit guard working. Check for that banner before a reload, because
     a scripted reload skips the unsaved-edits prompt and drops the edit.
     The sandbox's fake model can't answer the marketplace field fill, so
     "The agent returned an unreadable marketplace field response" in the
     chat is expected there. Find the New listing button with an exact name:
     a blank "New Listing" draft's row menu also matches it. After a reload
     the editor's fields are empty until the listing loads, so poll the Title
     box for the edited value before calling the edit lost, and confirm with
     `GET /api/conversations/<id>/listing`. The journey reports
     `listingPhotos`: 3 is expected; 0 on a phone is #1517. On a phone the first
     `input[type=file]` on the page is the chat composer's (6 images at
     most), not the photo tray's;
   - hosted web (`./sandbox.sh --hosted`, UI 5175, API 4320, sign in as
     `alice@sandbox.test` / `sandbox`). On a phone a new draft opens on its
     Listing tab; switch to Chat before staging photos in the composer.
     Hosted API calls need a bearer token: a `fetch` from the page has none
     and gets 401. Get one with `POST /local/auth/v1/token?grant_type=password`
     and the same email and password, then call the publish routes at
     `/api/conversations/<id>/native-marketplaces/<m>/list|update` and
     `/marketplace-listings/delist` (403 expected). Sign in as
     `bob@sandbox.test` the same way to check he gets 404 on Alice's draft;
   - iPhone in the Simulator against the hosted sandbox, driven with the T3
     device tools (`device_list`, `device_open`, `device_screenshot`), never
     raw `simctl` input. If `device_list` reports `simctl` missing even when
     `xcode-select -p` points at Xcode.app, record the hand-driven Simulator
     journey as **not run** with that error, and ask the seller to fix the
     device hub. Run the scripted iPhone journey instead:
     `ANTEROOM_TEST_ONLY=AnteroomUITests/CoreFlowSandboxTests ANTEROOM_TEST_RESULTS=<out>/iphone.xcresult ./anteroom-ios/test-sandbox.sh`,
     then export its screenshots with
     `xcrun xcresulttool export attachments --path <out>/iphone.xcresult --output-path <out>/iphone`
     and link them in the report.

## New features

For each PR merged since the last run:

- Read the PR, its changelog note and its diff. Work out which platforms the
  feature exists on (web, hosted web, iPhone, Mac app).
- Test it in the sandbox on each of those platforms, including at phone size
  for web. Try the edge cases the PR names, and empty, error and cancel paths.
- If the feature should exist on a platform and does not (check
  `docs/iphone-parity.md` and `docs/cloud-parity.md`), report the gap.
- If the PR added no e2e test and the behavior can be tested in Playwright or
  XCUITest, add one in a PR.
- Add a line for it under **Feature checks** if it needs ongoing checking that
  CI does not cover.

## Rotation

One deep area per day. Cover it on every platform it exists on. Read the
code under each path before testing so you test what is there, not what this
list remembers.

| Day | Area | Where to look |
|---|---|---|
| Monday | Listing editor, every marketplace tab (the list is `FORM_ORDER` in `src/components/editorTabs.ts`), category selection, item details, variations. Neither sandbox can load eBay's category fields once a listing has a category (they come from eBay's API), so the eBay tab shows "could not be loaded" there; record eBay item specifics and variations as **not run** until the sandbox seeds them | `src/components/ListingEditor.tsx`, `NativeInventoryEditor.tsx`, `editorTabs.ts`, `anteroom-ios/Sources/Editor/` |
| Tuesday | Bulk actions (edit, copy, delete, price drop, regenerate, upload), templates, inventory filters and search, import. Closing any bulk dialog, Cancel included, leaves selection mode, so select again before the next dialog. Hosted web has no bulk Marketplaces button | `src/components/Bulk*`, `ListingTemplatesDialog.tsx`, `anteroom-ios/Sources/Bulk/`, `Templates/`, `Inventory/` |
| Wednesday | Photos: upload, drop, reorder, crop and edit, thumbnails, camera on iPhone, measurements from ruler photos | `src/components/Photo*`, `anteroom-ios/Sources/Photos/` |
| Thursday | Assistant and listing chat, automations, suggestions, similar items, previous versions and history | `src/components/Assistant*`, `Automations*`, `ListingHistory.tsx`, `anteroom-ios/Sources/Assistant/`, `Automations/`, `History/` |
| Friday | Settings, providers, labels, sign-in, sign-up, password reset, account menu, analytics, sales records, mark sold; Mac app first run, backups and restore, migration of a database from the previous release | `src/components/SettingsPage.tsx`, `src/components/settings/`, `src/cloud/`, `anteroom-ios/Sources/Settings/`, `Session/`, `Analytics/`, `services/backups.py` |
| Saturday | Concurrency and speed: several sellers generating several drafts at once while navigating and typing. Record p50/p95 input and navigation latency and errors against the targets in `docs/responsiveness.md`. Run the hosted load test (`vendoo-studio/README.md`, **Hosted load test**) with the sandbox log redirected to a file, then search that log for `QueuePool`, `Traceback` and `TaskStopped`. "Etsy isn't connected" tracebacks are expected there; lease-ended lines are expected only after the harness starts cancelling (`generate/cancel` in the log) | `e2e/concurrentGeneration.e2e.ts`, `scripts/hosted-load-test.py`, `docs/responsiveness.md` |
| Sunday | Safety sweep: every publish, delist, update and save-draft route without `confirm` and without named marketplaces must be refused; search for any code path where automation, retries, queues or bulk actions could reach them. Seller isolation: Alice and Bob never see each other's drafts or photos. Live updates and stale-edit protection between web and iPhone on the same draft | `routes/native_marketplaces.py`, `routes/marketplace_listings.py`, `routes/listing_commands.py` (set-price push), `cli/catalog.py` and `cli/execute.py` (the CLI's confirm gate), `docs/seller-isolation.md` |

## Feature checks

Checks for specific features that CI does not cover yet. Each line says what
to check, on which platforms, and the date it was added. Remove a line once a
test in CI covers it.

- (none yet)

## Flaky tests

Tests that fail some runs and pass on rerun. Each line says the test, how it
fails and the likely cause. Fix one per run (see **Each run**), then delete
its line. Before calling a failure flaky, rerun it alone and check it fails in
code the PR didn't change.

- `CoreFlowSandboxTests.testGenerateFromTheEditorShowsASummary` at the
  `staticTexts.matching(...)` check after the reply: "Timed out while
  evaluating UI query" on GitHub's macOS runners (2026-11-10, 2026-11-11).
  Query a narrower element.
- `PolishScreenshotTests.testPolishedScreensInLightAndDark`: the Inventory
  list didn't appear within 30 s after a theme change, on CI (2026-11-11).
- `e2e/photoReorder.e2e.ts` "photos on a marketplace form reorder by
  dragging" [desktop]: hit the 120 s test timeout before the order check,
  on two of four fix-branch `./check.sh` runs that did not touch photos;
  passed 6 of 6 alone (2026-11-14). Cause unknown; read the trace in
  `e2e/results` the next time it fails.
- `hosted-e2e/signin.e2e.ts` "in the Mac app, password resets open in the
  browser": the Account menu isn't open when the test looks for Sign out,
  about one run in two (2026-11-11). Likely the Assistant composer's
  `autoFocus` (`AssistantPage.tsx`) takes focus after the click and closes the
  popover; unproven.

## Fixing bugs

Follow **How to Work on a Change** in `AGENTS.md` for each bug:

- State the root cause in one sentence, write a failing test, fix the root
  cause, run `./check.sh`, then open the PR from its own branch and worktree
  (`fix/audit-<short-name>`).
- UI fixes go to web and iPhone in the same PR.
- A PR that changes `vendoo-studio/` adds `changelog.d/<branch-slug>.md`.
- Stay with each PR until CI is green or you have recorded why it is not.
- Do not open a PR for a guess. If you cannot find the root cause, report the
  bug with reproduction steps instead.
- Before opening a PR, check open PRs and issues for the same bug. Do not
  duplicate one.
- Queue a fix PR to merge itself (`gh pr merge <n> --auto --merge`) only
  when all of these hold:
  - it has a test that failed before the fix and passes after, both runs
    recorded in the PR;
  - it changes nothing in publishing, delisting or marketplace actions
    (`routes/native_marketplaces.py`, `routes/marketplace_listings.py`,
    `vendoo-extension/background/`), in deploys or releases (`.github/`,
    `deploy/`, `fly.toml`), or in the database schema (`migrations/`, models);
  - `./check.sh` passed locally.

  Leave every other PR, including `Update daily audit instructions`, for the
  seller. Never merge by hand, never bypass a check.

## Report

File one issue per run, labelled `audit`, titled `Daily audit <YYYY-MM-DD>`,
and close the previous run's issue. Keep it to about a page: details live in
the finding issues and PRs.

**Findings.** Each bug that has no merged fix gets its own issue, labelled
`audit-finding`, titled with the symptom in the seller's words. It holds the
platform, steps, expected and actual results, root cause or best guess with
confidence, `file:line`, and a screenshot path. Before filing, search open
`audit-finding` issues and update the one that matches instead. A fix PR says
`Fixes #<n>` so merging closes it. The daily issue links findings; it doesn't
repeat them or carry them over.

The daily issue contains, in this order:

- **Decisions for the seller:** each open question as one line with the
  options and a recommendation, linked to its finding. Put these first; they
  are what blocks the most work.
- **Audited:** the `origin/main` commit SHA, the installed Mac app version, and
  the rotation area.
- **Summary:** passed, failed, not run, in one or two lines.
- **New findings** and **fixed today**: links, one line each.
- **New features tested**, with their PRs and results per platform.
- **Rotation:** what was checked and what it found, linked to findings.
- **Measurements** on Saturdays only: load, p50/p95, errors.
- **Not run**, with the reason.
- **Needs the seller:** things only the seller can do (extension form sweeps
  on a signed-in Chrome, real model quality with
  `scripts/evaluate-generation.py`, live Supabase and Fly checks, merging the
  PRs the merge rule leaves out).
- **PRs from this run** and their CI state.

## Keeping this file current

This file should get shorter as the test suites grow.

- **Add** a check when a merged feature needs ongoing checking, or when the
  audit found a bug that an instruction here would have caught sooner.
- **Remove** a check once a Playwright, XCUITest or pytest test in CI covers
  it: write that test first, in a PR, then delete the line.
- **Fix** any instruction that turned out wrong (a renamed path, a changed
  port, a command that no longer exists).
- Make these edits in a PR titled `Update daily audit instructions`, separate
  from bug fixes, and say in its description why each line changed.
