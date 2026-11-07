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
- Never deploy, release, bump the version, edit `CHANGELOG.md`, or merge a PR.
- Work in your own worktree off a fresh `origin/main`:
  `git fetch origin && git worktree add -b audit/<YYYY-MM-DD> ../anteroom-audit-<YYYY-MM-DD> origin/main`.
  Other sessions share the main checkout. Remove the worktree at the end unless
  a PR branch still needs it.
- Run one sandbox at a time, or give each its own ports
  (`VENDOO_STUDIO_PORT`, `VENDOO_STUDIO_WEB_PORT`). The iPhone script always uses
  4320, so stop the hosted sandbox before running it. If a developer's sandbox
  already holds a port, pick other ports; do not stop their process.

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
5. **Fix.** Open a PR for each bug you can fix (see **Fixing bugs**).
6. **Report.** File one issue (see **Report**).
7. **Update this file** when a run teaches something (see **Keeping this file
   current**).

If a step cannot run (Xcode missing, Simulator broken, sandbox will not start),
record it as **not run** with the error and keep going. Never report a step as
passing when it did not run.

## Smoke

Every run, in this order:

1. `./check.sh --all` in the audit worktree after setup
   (`cd vendoo-studio && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" && npm ci`).
2. `cd vendoo-studio && npm run e2e` (local web, desktop and phone sizes).
3. `cd vendoo-studio && npm run e2e:hosted` (hosted web, two sellers).
4. `./anteroom-ios/test-sandbox.sh` (iPhone XCUITests in the Simulator).
5. Mac app:
   `vendoo-studio/.venv/bin/python vendoo-studio/scripts/smoke-packaged-sandbox.py /Applications/Anteroom.app`.
   This checks the installed release in sandbox mode on a temporary data
   folder. Note its version. The release may be behind `main`; that is expected.
6. One hand-driven core journey on each surface: create a draft, add photos,
   generate, edit a field, save, reopen, and try to publish (it must be
   refused). Surfaces:
   - local web (`./sandbox.sh`, UI 5174) at desktop and phone sizes;
   - hosted web (`./sandbox.sh --hosted`, UI 5175, sign in as
     `alice@sandbox.test` / `sandbox`);
   - iPhone in the Simulator against the hosted sandbox, driven with the T3
     device tools (`device_list`, `device_open`, `device_screenshot`), never
     raw `simctl` input.

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
| Monday | Listing editor, every marketplace tab (eBay, Poshmark, Mercari, Depop, Sellwild, Etsy, Vinted), category selection, item details, variations | `src/components/ListingEditor.tsx`, `NativeInventoryEditor.tsx`, `anteroom-ios/Sources/Editor/` |
| Tuesday | Bulk actions (edit, copy, delete, price drop, regenerate, upload), templates, inventory filters and search, import | `src/components/Bulk*`, `ListingTemplatesDialog.tsx`, `anteroom-ios/Sources/Bulk/`, `Templates/`, `Inventory/` |
| Wednesday | Photos: upload, drop, reorder, crop and edit, thumbnails, camera on iPhone, measurements from ruler photos | `src/components/Photo*`, `anteroom-ios/Sources/Photos/` |
| Thursday | Assistant and listing chat, automations, suggestions, similar items, previous versions and history | `src/components/Assistant*`, `Automations*`, `ListingHistory.tsx`, `anteroom-ios/Sources/Assistant/`, `Automations/`, `History/` |
| Friday | Settings, providers, labels, sign-in, sign-up, password reset, account menu, analytics, sales records, mark sold; Mac app first run, backups and restore, migration of a database from the previous release | `src/components/SettingsPage.tsx`, `src/components/settings/`, `src/cloud/`, `anteroom-ios/Sources/Settings/`, `Session/`, `Analytics/`, `services/backups.py` |
| Saturday | Concurrency and speed: several sellers generating several drafts at once while navigating and typing. Record p50/p95 input and navigation latency and errors against the targets in `docs/responsiveness.md` | `e2e/concurrentGeneration.e2e.ts`, `docs/responsiveness.md` |
| Sunday | Safety sweep: every publish, delist, update and save-draft route without `confirm` and without named marketplaces must be refused; search for any code path where automation, retries, queues or bulk actions could reach them. Seller isolation: Alice and Bob never see each other's drafts or photos. Live updates and stale-edit protection between web and iPhone on the same draft | `routes/native_marketplaces.py`, `routes/marketplace_listings.py`, `docs/seller-isolation.md` |

## Feature checks

Checks for specific features that CI does not cover yet. Each line says what
to check, on which platforms, and the date it was added. Remove a line once a
test in CI covers it.

- (none yet)

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
- Never merge.

## Report

File one issue per run, labelled `audit`, titled
`Daily audit <YYYY-MM-DD>`. Close the previous run's issue once its open items
are carried over. The issue contains:

- **Audited:** the `origin/main` commit SHA, the installed Mac app version, and
  the rotation area.
- **Summary:** passed, failed, not run, in one or two lines.
- **Failures:** each with platform, steps, expected and actual results, and a
  screenshot path or attachment, linked to its fix PR or marked "no fix yet".
- **New features tested**, with their PRs and results per platform.
- **Measurements**, when taken: load, p50/p95, errors.
- **Recommendations:** UX friction, slow screens, gaps between web and iPhone,
  missing tests. Keep each one to a sentence with where it is.
- **Not run**, with the reason.
- **Needs the seller:** things only the seller can do (extension form sweeps
  on a signed-in Chrome, real model quality with
  `scripts/evaluate-generation.py`, live Supabase and Fly checks).

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
