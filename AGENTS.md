# Repository Guidelines

## Repository Overview

Monorepo combining the `list-this` skill family, the Vendoo Chrome extension, and Anteroom. The skills generate marketplace-ready listings from product photos; the extension fills six platforms from a single JSON payload; Anteroom provides a local interface for listing creation and automation.

**Invariant:** Automation must never publish listings. Generation, category
resolution and draft creation run unattended and stop at a saved draft.
Publishing and delisting happen only when the seller asks for them: the
`vendoo-api/list` and `vendoo-api/delist` routes require the marketplaces to be
named explicitly and the call to carry `confirm`, and nothing in Anteroom calls
them on its own. No code path may publish everywhere by default, on a queue, on
a retry, or as a side effect of any other action.

## Source of Truth

- **`skills/`** — Canonical source for listing rules, templates, research loops, and optimizer tooling.
- **`VENDOO_STUDIO_SPEC.md`** — Product and architecture specification for Anteroom.
- **`DESIGN.md`** — Visual design rules and review checklist for Anteroom's web UI.
- Do not duplicate listing rules across components.

## Code Search (Semble)

**Cursor / coding agents:** Prefer `semble search` over broad grep+read when locating implementation. The index builds and caches on first use.

```bash
semble search "verified category selection" . --max-snippet-lines 10
semble search "schema probe category fields" . --top-k 10
semble search "category_path marketplace_categories" . --content all
semble find-related vendoo-studio/server/vendoo_studio/services/category_selection.py 10 .
```

Use `--content docs` for specs/skills prose, `--content config` for JSON/YAML/TOML, or `--content all` when categories/schemas may live in code *and* references (e.g. `skills/list-this/references/`, `VENDOO_STUDIO_SPEC.md`). Navigate straight to returned paths/lines; use grep only for exact string sweeps (error messages, renames). If `semble` is missing from `$PATH`, use `uvx --from "semble[mcp]" semble`.

**Anteroom listing agent:** Uses the in-app catalog index (`vendoo_studio.services.catalog_index`), not Cursor MCP. It materializes SQLite category leaves, observed schemas, skill-reference dropdown options, `skills/list-this` rule chunks, and extension fill helpers into local docs, searches them with Semble, and only accepts hits that verify. `GET /api/catalog/search?kind=category|schema|option|skill|fill|all` exposes the same search. Generation pulls relevant skill chunks; repair enriches gap dropdowns; fill-log reports include related helpers. It does not search listings, photos, or secrets.

## Engineering Principles

- Study how established products solve the problem before designing a solution. Adopt their proven patterns and conventions rather than inventing an approach from scratch.
- Do not preserve backward compatibility in code. Remove obsolete paths instead of adding compatibility layers or fallbacks. This does not extend to the database: Anteroom runs on more than one machine, and a schema change has to reach a database nobody can afford to delete. Schema changes go through an Alembic revision (see **Database schema** below).
- Choose the simplest implementation that fully meets the current requirements. Avoid speculative abstractions, configuration, and indirection.
- Grow the system in layers. Start from the smallest version that works end to end, and add each new capability on top of a product that already works. Never trade a working product for unfinished complexity.
- Keep components modular and concerns clearly separated.
- Prefer established, well-maintained libraries when they reduce overall complexity or improve reliability. Do not reimplement common functionality without a clear reason.
- Lean on the dependencies already in the project before writing your own implementation or adding packages. Do not assume a library lacks a capability without checking its documentation and types.
- Make architectural decisions for the long term. Do not accept a stopgap that only works for now and is meant to be replaced later.

## How to Work on a Change

Every bug fix, feature and refactor follows the same loop. Component details
(where logs live, how to drive the app, traps) are in the `AGENTS.md` inside
`vendoo-studio/`, `vendoo-extension/` and `webhook-relay/`.

1. **Start clean.** Work in your own worktree off a fresh `origin/main`:
   `git fetch origin && git worktree add -b <branch> ../<name> origin/main`.
   Other sessions share the main checkout; it may be on another branch, behind
   `origin/main`, or switched under you. In a new worktree, install with
   `cd vendoo-studio && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" && npm ci`.
2. **Diagnose before changing anything.** Read the logs, job events and fill
   logs for the failure, and find the code with `semble search`. State the root
   cause in one sentence before you write a fix. A fix you cannot explain is a
   guess.
3. **Reproduce it as a failing test.** Pick the narrowest test that shows the
   bug: pytest for backend behavior, Vitest for frontend logic, `node --test`
   for the extension, Playwright (`vendoo-studio/e2e/`) for anything that needs
   the running app. Run it and watch it fail for the reason you stated. When a
   test truly cannot reproduce it (a live marketplace page, a real model's
   output), reproduce it in the sandbox and write the steps into the PR.
4. **Fix the root cause** with the smallest change that does it. Watch the test
   pass, and keep it.
5. **Verify like CI.** `./check.sh` runs CI's checks for every component you
   changed, browser tests included (`--fast` skips those while iterating). For
   a visible change, look at it in the sandbox at desktop and phone sizes and
   put screenshots in the PR.
6. **Open the PR** (see **Pull requests** below) and stay with it until CI is
   green.

The type check in `./check.sh` uses CI's locked baseline mode. After fixing a
recorded type error, regenerate the baseline with `basedpyright --writebaseline`,
check that it only shrinks, and commit that change with the fix. A locally green
check must not depend on an uncommitted baseline update.

A feature follows the same loop: the first test describes the behavior the
seller asked for, and the feature is done when it passes in the sandbox.

### Sandbox

`./sandbox.sh` runs the whole of Anteroom offline on throwaway data. Use it for
anything you would otherwise try on the seller's real app.

- Fresh data folder each run (`--keep` reuses it), seeded with sample listings
  and photos (`--empty` skips them). API on 4319, UI on 5174.
- A fake AI provider (`providers/sandbox.py`) answers the same way every time,
  so generation, chat and the editor run end to end without a model or key.
- Secrets stay in memory. Outbound connections to anything but loopback are
  refused in-process. Routes that would publish, delist, update or delete a
  live listing answer 403. So do updates, Connect Chrome, Tailscale and
  provider sign-ins, because they start programs that reach outside.
- It never runs on 4318, the real app's port, so the extension never reaches it.
- Drive it from a browser (`npm run e2e`, Playwright at desktop and iPhone
  sizes) or the CLI: `anteroom --base-url http://127.0.0.1:4319 <command>`.

The sandbox does not cover:

- real marketplace forms (form sweeps, below);
- real model quality (`vendoo-studio/scripts/evaluate-generation.py`, see
  `docs/listing-generation-evaluations.md`);
- live Supabase services (the local hosted sandbox supplies protocol stand-ins).

`./sandbox.sh --hosted` runs the hosted API and web UI on throwaway Postgres,
with signed local sessions, private photo storage and the fake provider. The
native iPhone Debug app connects to that API; XCUITest runs on macOS. The
packaged Mac app also has a sandbox smoke check. See
[`docs/agent-sandboxes.md`](docs/agent-sandboxes.md) for commands, ports and
staging preparation. No paid staging resources are provisioned automatically.

Never point a test, script or sandbox at the seller's real data folder
(`vendoo-studio/data/` or `~/Library/Application Support/Anteroom`).

### Refactoring

A refactor changes structure, never behavior, and ships in its own PR. Never
mix it with a fix or a feature.

- Before moving code, make sure tests pin what it does today. Where they do
  not, add them first, in their own commit, and watch them pass on the old
  code.
- Change in small steps, one commit each, with `./check.sh` green after every
  step. The tests from the first step stay unchanged throughout.
- Delete what becomes unused: old names, wrappers, re-exports. Do not keep a
  shim for old callers (see **Engineering Principles**).
- Move files with `git mv`, then grep `server/tests` and `vendoo-extension/tests`
  for the old path: some backend tests read frontend and extension source
  files.
- Run the browser tests before and after. The same tests must pass with the
  same screens.
- The code-health checks in `./check.sh` and CI catch what a refactor commonly
  breaks: type errors (basedpyright), imports that go the wrong way between
  layers (import-linter), and code left unused (knip for the frontend,
  `scripts/check-dead-code.py` for the backend). Each one records the problems
  that existed when it was added and fails only on new ones. See
  `vendoo-studio/AGENTS.md`, **Code health checks**. A refactor that fixes
  recorded problems removes them from the record in the same PR.

## Repository Structure

- **`skills/`** — Agent skills for listing generation (`list-this`), sourcing (`box-scout`) and reading measurements from ruler photos (`measure-this`).
- **`vendoo-extension/`** — Chrome MV3 extension that consumes listing JSON and fills marketplace forms. Content scripts under `content-scripts/` handle Vendoo, eBay, Poshmark, Mercari, Depop, and Etsy. `background.js` routes messages and loads the worker modules in `background/`; the extension runs without its own interface, with review and approved Sends handled in Anteroom. Node unit tests live in `tests/`.
- **`webhook-relay/`** — Cloudflare Worker that receives eBay and Etsy webhooks, verifies their signatures, keeps only IDs, and holds them in a per-device inbox until that Mac's Anteroom pulls them over an outbound connection. It also brokers eBay and Etsy sign-in and forwards Etsy API calls, so eBay's Cert ID and Etsy's shared secret never ship in the app. See its `README.md`.
- **`vendoo-studio/`** — React + TypeScript frontend (`src/`) and FastAPI Python backend (`server/vendoo_studio/`). Frontend API clients live under `src/api/`. Backend routes live under `server/vendoo_studio/routes/`; domain behavior lives in `server/vendoo_studio/services/` and `server/vendoo_studio/repositories/`.
- **`anteroom-ios/`** — Native SwiftUI iPhone app on the hosted API and Supabase. The Xcode project is generated from `project.yml` with XcodeGen and not tracked; it builds only on macOS.

## Development Commands

- **Anteroom setup:** `./setup.sh` (from the repo root) or `cd vendoo-studio && ./scripts/setup.sh`
- **Anteroom start:** `./start.sh` or `cd vendoo-studio && ./scripts/dev.sh`
- **Anteroom sandbox:** `./sandbox.sh` (see **Sandbox** above)
- **Anteroom browser tests:** `cd vendoo-studio && npm run e2e` (starts its own sandbox on 4329/5184)
- **Anteroom code health:** `cd vendoo-studio && npm run knip && basedpyright && lint-imports && python scripts/check-dead-code.py`
- **Everything CI checks, for what you changed:** `./check.sh` (`--fast` skips browser tests, `--all` runs every component)
- **Anteroom doctor:** `cd vendoo-studio && ./scripts/doctor.sh`
- **Anteroom schema check:** `cd vendoo-studio && ./scripts/check-db-schema.sh [db]`
- **Anteroom restore:** `cd vendoo-studio && ./scripts/restore-db.sh <snapshot.db>` (quit Anteroom first)
- **Anteroom new migration:** `cd vendoo-studio && .venv/bin/alembic revision --autogenerate -m "..."`
- **Anteroom frontend build:** `cd vendoo-studio && npm run build`
- **Anteroom frontend lint and tests:** `cd vendoo-studio && npm run lint && npm test`
- **Anteroom backend lint and tests:** `cd vendoo-studio && ruff check server && python -m pytest -q -n auto` (install with `pip install -e ".[dev]"`)
- **Anteroom backend tests on Postgres:** set `ANTEROOM_TEST_DATABASE_URL` to any database on a Postgres 16 server (each test worker creates its own). Without a local server, `pip install pgserver` gives one. Mark tests that need the SQLite file `@pytest.mark.sqlite_only("why")`.
- **iPhone app (macOS only):** `cd anteroom-ios && xcodegen && open Anteroom.xcodeproj`. CI builds it for the Simulator (`.github/workflows/ios.yml`).
- **Extension tests:** `node --test 'vendoo-extension/tests/*.test.js'`
- **Webhook relay tests and deploy:** `cd webhook-relay && npm test`; `npx wrangler deploy` only when the seller asks.
- **Extension:** **Connect Chrome** opens everyday Chrome. Load unpacked from `vendoo-extension/` in `chrome://extensions/` with Developer mode enabled.

## Coding Conventions

- Follow the established style within each component. Do not refactor unrelated files.
- Any visible change to the Anteroom web UI follows `DESIGN.md`: shared components, theme tokens, the type and spacing scales, and its pre-ship checklist.
- Keep skill instructions in canonical `SKILL.md` files; keep browser-specific logic in the corresponding extension content script.
- Keep frontend API calls under `vendoo-studio/src/api/`; keep backend routes thin and place domain behavior in services and repositories.
- A direct marketplace tab (eBay, Poshmark, Mercari, Depop, Sellwild) is a 1:1 replica of that marketplace's own listing form: the same fields, labels and order, no more and no fewer, under the same section headings and row layout (each native field carries the page's `section`, and `width: "half"` where the page puts fields side by side). Its rows come only from the native form the server or extension builds; the editor never adds reference, hand-kept or fallback rows to it. A field the marketplace has goes into that native form builder, not the editor.
- Maintain the JSON listing contract across skills, Anteroom, and the extension. The extension expects the structure documented in `docs/json-contract.md`.

## Database schema

- Anteroom's schema is owned by Alembic, on the Mac's SQLite and on the hosted Postgres (`ANTEROOM_DATABASE_URL`) alike. A revision must upgrade both; CI runs the backend suite on each. Revisions live in `vendoo-studio/server/vendoo_studio/migrations/versions/` and ship inside the Mac app.
- **Every model change needs a revision.** Editing a model without one leaves every existing database short of the change, and the mismatch only surfaces later as a query error. Generate one with `alembic revision --autogenerate` and read what it produced before committing it.
- Revisions are forward-only in practice. Test a new one against a copy of a database made by the previous release, not only against a fresh one.
- `init_db` migrates on startup, snapshots first, and refuses to open a database stamped with a revision this build does not know, which is what stops an older build from writing into a newer database.
- `BACKFILLED_COLUMNS` in `database.py` exists only to carry databases from before migrations were introduced. Do not add to it; write a revision instead.
- Snapshots are written by `services/backups.py` to `<data>/backups/`, before updates and migrations and on a timer. Never copy `vendoo_studio.db` as a file to back it up: use `VACUUM INTO`, or the `-wal` beside it makes the copy inconsistent.

## Verification

CI (`.github/workflows/ci.yml`) runs these on every pull request and push to `main`. Run the same commands locally for the paths you touched.

- **Anteroom frontend:** `cd vendoo-studio && npm ci && npm run lint && npm test && npm run build`
- **Anteroom backend:** `cd vendoo-studio && ruff check server && python -m pytest -q -n auto` (install with `pip install -e ".[dev]"`)
- **Anteroom code health:** knip (in the frontend job) and basedpyright, import-linter and the dead-code check (in **Studio code health**). Commands above; details in `vendoo-studio/AGENTS.md`.
- **Anteroom browser tests:** `cd vendoo-studio && npx playwright install chromium && npm run e2e`. They run against the sandbox at desktop and phone sizes; on failure, the report, screenshots and traces are in `vendoo-studio/e2e/report` and `e2e/results`, and CI uploads them as an artifact.
- **Extension:** `node --check` on changed JS files, `node --test 'vendoo-extension/tests/*.test.js'`, and `python -m json.tool vendoo-extension/manifest.json`. Then reload the unpacked extension and test affected marketplace flows manually. Use platform-prefixed console logs (`[EBAY]`, `[POSHMARK]`, etc.), the DevTools diagnostic tool for live form structure, and Anteroom’s fill results for per-field outcomes.
- **Skills:** Confirm `skills/list-this/SKILL.md` exists and `python -m json.tool skills/list-this/references/vendoo-dropdown-options.json` succeeds.
- **Marketplace forms (not in CI, needs a signed-in browser):** after changing a native form builder, or to check whether a marketplace changed its sell page, run `vendoo-extension/tools/form-sweep/<marketplace>/sweep.js` on that marketplace's sell page (Depop, Mercari, Poshmark, Sellwild), then `node vendoo-extension/tools/form-sweep/<marketplace>/compare.js <downloads folder>`. Each script's header says how; the compare exits 1 on any difference.
- **Secrets:** Do not track `.env`, key files, photos, or private exports. CI scans the working tree with gitleaks.

**Pull requests:** After creating or updating a PR, always check CI (`gh pr checks`) and fix failures before considering the work done. Do not stop while required checks are pending or red. PRs never bump the version or edit `CHANGELOG.md`; do not run `bump-version.sh` in a PR. A PR that changes `vendoo-studio/` adds one note file, `changelog.d/<branch-slug>.md`, holding `- ` bullets in the seller's words (`<slug>.minor.md` or `<slug>.major.md` only when the seller asks for that bump; see `changelog.d/README.md`). After merge, the **Version** workflow turns each merged PR's notes into one patch bump and a `## <version> — <YYYY-MM-DD>` section at the top of `CHANGELOG.md`, which the app shows under Settings → General → About → What's new, and then starts the macOS release build. The UI reads the version from the API; never hardcode it. CI's **Changelog** job (`vendoo-studio/scripts/check-changelog.py`) fails when a PR changes `VERSION` or `CHANGELOG.md`, or when a note is malformed; run it locally with `python vendoo-studio/scripts/check-changelog.py`.

When a check fails, read its log (`gh run view <run-id> --log-failed`), reproduce the failure locally with the same command or `./check.sh`, and fix the cause before pushing again. Do not push guesses at CI. Several Anteroom PRs are often open at once. Because none of them touch `VERSION` or `CHANGELOG.md`, they do not conflict over versions; merge `origin/main` in only when CI or a real code conflict calls for it.

## Security and Product Invariants

- Never commit API keys, OAuth credentials, listing photos, generated diagnostics, or private exports.
- API keys and sign-in tokens never enter the database in plain text, logs, frontend responses, or extension messages. On the Mac they live in macOS Keychain. When the hosted API takes over generation (step 4 of `docs/cloud-architecture.md`), they move to Supabase Vault, encrypted at rest, and are never returned to any client.
- On the Mac, bind Anteroom only to `127.0.0.1`. The hosted API on Fly.io is the only part that accepts connections from outside, and it requires a Supabase session on every request except health checks and marketplace webhooks. The Mac worker connects to it outbound and binds nothing. Optional private Tailscale Serve (Settings → Connections) may proxy Tailnet HTTPS to that loopback port; never Funnel and never bind to `0.0.0.0`.
- Require human approval before sending to Vendoo.
- Allow one Vendoo automation job at a time. Additional approved Sends may wait in a FIFO queue and start when the current job finishes, fails, or is cancelled. Direct marketplace List, Update and Save draft actions may run side by side, one per marketplace, but never alongside a Vendoo job, a delist or a page inspection.
- Do not introduce Redis, MongoDB, Celery, or provider substitutions unless explicitly requested.
- Approved cloud services (2026-10-26, see `docs/cloud-architecture.md`):
  - **Supabase** for accounts, Postgres, photo storage, Realtime and Vault. Every seller table has row-level security keyed to the seller's `user_id`.
  - **Fly.io** for the hosted Anteroom API, built from a Dockerfile. Docker is for the hosted API only; the Mac app does not need it.
  - **`webhook-relay/` on Cloudflare Workers**, as below.
- `webhook-relay/` on Cloudflare Workers It never calls Studio and never acts on a listing. It stores only device IDs, token hashes, linked marketplace account IDs and event IDs, plus sign-in tokens for at most 15 minutes until the device collects them: no buyer names, addresses, prices or listing text. Etsy API calls pass through it unstored. Studio reaches it outbound only, so the loopback-only rule still holds.

## Cursor Cloud specific instructions

- Run `./setup.sh`, then `./start.sh`. The UI is http://127.0.0.1:5173 and the API is http://127.0.0.1:4318 (`GET /api/health`). Both stay on loopback.
- `./setup.sh` looks for Google Chrome at the macOS app path (`~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` or `/Applications/...`). On Linux, link the `chrome` binary there before setup. The Cloud Agent install does that.
- Ubuntu has no `python` on `PATH`. After setup, `python` is a wrapper for `vendoo-studio/.venv/bin/python`, and `ruff`, `pytest`, `alembic`, `semble`, and `uvicorn` on `PATH` are that venv. Backend checks from `vendoo-studio/`: `ruff check server` and `VENDOO_STUDIO_DATA_DIR=$(mktemp -d) python -m pytest -q -n auto`.
- An unpacked dev server stores data in `vendoo-studio/data/`. `scripts/doctor.sh` still looks for the macOS data folder and warns when that file is absent. On Linux, startup logs a keychain warning (`keyring` selects the macOS backend). Reads fail and provider secrets stay unset; the API still serves `/api/health` and drafts.
- For trying changes, prefer `./sandbox.sh` (UI http://127.0.0.1:5174) over `./start.sh`: it needs no provider, keys or Chrome, and it cannot publish.
- **Create a listing** on the empty inventory screen stays disabled until Settings has a provider. `POST /api/conversations` still creates a draft, and the sidebar shows it. Generating copy needs a provider. Do not publish.
- Webhook relay: `cd webhook-relay && npm test`. Extension: `node --test 'vendoo-extension/tests/*.test.js'`.
- `./setup.sh` redraws icon PNGs. The Cloud Agent install checks those tracked files back out so the worktree stays clean.
