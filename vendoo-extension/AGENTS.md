# Extension: notes for agents

The root `AGENTS.md` has the rules and the working loop. This file covers
diagnosing and testing the extension.

## Diagnosing

- Anteroom keeps a field-by-field report of every fill: `GET /api/jobs/{id}/fill-log`
  on the Anteroom API, or `<data>/fill-logs/<job_id>.md`. Start there before
  opening a browser.
- Extension log lines reach Anteroom's log too:
  `GET /api/settings/app-logs?grep=[EBAY]` (or `anteroom logs --grep ...`).
  Keep the platform prefixes (`[EBAY]`, `[POSHMARK]`, ...) on new log lines so
  this keeps working.
- For live form structure, use the DevTools diagnostic tool
  (`diagnostic-tool.js`, see `TROUBLESHOOTING.md`).
- A marketplace may have changed its sell page. Run that marketplace's form
  sweep (`tools/form-sweep/<marketplace>/`, see the root `AGENTS.md`,
  **Verification**).

## Reproducing

- Unit tests: `node --test 'vendoo-extension/tests/*.test.js'`.
  `tests/worker-context.js` loads the MV3 service worker into one VM context
  with a stubbed `chrome` API, the way Chrome shares globals across
  `importScripts()`. Tests call the worker's functions directly. They replace
  its network helpers (for example `c.mercariRequest`) with recorded
  marketplace responses, written inline or kept in `tests/fixtures/`.
- Reproduce a fill bug as a test: record what the marketplace answered, stub
  the request helper with it, and check the field that came out wrong.
- Nothing in CI opens a real marketplace. When a change needs the live form,
  load the unpacked extension in a signed-in Chrome, fill a draft, and stop at
  the draft. Never list.

## Traps

- The service worker talks to `http://127.0.0.1:4318`, the real Anteroom, unless
  `STUDIO_API_ORIGIN` is set (tests set it through `loadWorker({ apiOrigin })`).
  The sandbox runs on another port, so it never receives the extension.
- Some Anteroom backend tests read files in this folder. After renaming or
  moving one, grep `vendoo-studio/server/tests` and run that suite too.
