# Contributing

This is a local-first monorepo: listing skills, Anteroom, and the Chrome extension. Product rules live in [`AGENTS.md`](AGENTS.md).

## Working on a change

1. Use a branch. Keep unrelated work out of the same PR.
2. Change one component unless the JSON listing contract requires a cross-cut.
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
| `vendoo-extension/` | `node --check` on changed JS files; `node --test 'vendoo-extension/tests/*.test.js'`; `python -m json.tool vendoo-extension/manifest.json` |
| `skills/list-this/` | Confirm `SKILL.md` and reference JSON still parse |
| `changelog.d/` notes | `python vendoo-studio/scripts/check-changelog.py` |

CI on every pull request and push to `main` runs these jobs and a secret scan. The macOS packaging workflow is separate and only publishes the Anteroom app.

## Invariants

- Do not publish listings. Stop at saved drafts.
- Do not add Redis, MongoDB, Celery, Docker, or cloud hosting unless that is the task.
- Bind Anteroom to `127.0.0.1`.
- Keep listing rules in `skills/`. Do not copy them into the extension.
