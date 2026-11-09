# Contributing

This monorepo holds Anteroom (hosted web app, Mac app and iPhone app), the Bridge Chrome extension, the webhook relay and the listing skills. See [`README.md`](README.md) for the layout. Product rules live in [`AGENTS.md`](AGENTS.md).

## Working on a change

1. Use a branch. Keep unrelated work out of the same PR.
2. Keep each PR to one change. A change that spans components (for example a hosted API field and the iPhone app that reads it) belongs in one PR when splitting it would break a client.
3. Touching `vendoo-studio/`? Add a note, `changelog.d/<branch-slug>.md`, with a bullet describing the change for sellers (see [`changelog.d/README.md`](changelog.d/README.md)). Do not bump `VERSION` or edit [`CHANGELOG.md`](CHANGELOG.md): after merge, the Version workflow bumps the version and moves the note into `CHANGELOG.md`, which Anteroom shows under Settings → General → About → What's new. CI's Changelog job rejects pull requests that edit either file.
4. Try the change in `./sandbox.sh`: Anteroom offline on sample data, with a fake AI provider, and publishing refused.
5. Run `./check.sh`: the same checks as CI for the paths you touched, browser tests included.
6. Open a pull request with the template checklist completed.

The full loop (diagnose, reproduce as a failing test, fix, verify) and the rules for refactors are in [`AGENTS.md`](AGENTS.md) under **How to Work on a Change**.

## Checks

| Path | Command |
|---|---|
| `vendoo-studio/` frontend | `cd vendoo-studio && npm ci && npm run lint && npm test && npm run build` |
| `vendoo-studio/` backend | `cd vendoo-studio && ruff check server && python -m pytest -q -n auto` |
| `vendoo-studio/` in a browser | `cd vendoo-studio && npm run e2e` (desktop and phone, against the sandbox) |
| `anteroom-ios/` (macOS only) | `cd anteroom-ios && xcodegen`, then `./test-sandbox.sh`; CI's iPhone app workflow builds and tests it |
| `webhook-relay/` | `cd webhook-relay && npm ci && npm test` |
| `vendoo-extension/` | `node --check` on changed JS files; `node --test 'vendoo-extension/tests/*.test.js'`; `python -m json.tool vendoo-extension/manifest.json` |
| `skills/list-this/` | Confirm `SKILL.md` and reference JSON still parse |
| `changelog.d/` notes | `python vendoo-studio/scripts/check-changelog.py` |
| Shared API contract (`contracts/`, generated client models) | `python vendoo-studio/scripts/api-contract.py --check` (also run by the backend suite) |

CI on every pull request and push to `main` runs these jobs and a secret scan. Merging to `main` releases: it bumps the version, builds the macOS app, uploads the iPhone app to TestFlight when it changed, and migrates and deploys the hosted API to production. Merge only when the seller asks.

## Invariants

The product and security invariants (marketplace actions only on the seller's explicit, per-marketplace confirmation; seller isolation; where secrets live; loopback binding on the Mac; approved cloud services) are in [`AGENTS.md`](AGENTS.md) under **Security and Product Invariants**, and are not repeated here. Keep listing rules in `skills/`; do not copy them into the extension or the apps.
