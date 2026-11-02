# Anteroom Bridge Troubleshooting

## Reload the extension

1. Go to `chrome://extensions/` and enable **Developer mode**.
2. Click **Load unpacked** and select the repo's `vendoo-extension/` folder, or click the refresh icon on its card after a code change.
3. Keep Anteroom running and use **Connect Chrome**. The extension pairs on its own.

## Anteroom says Chrome is not connected

- Make sure Anteroom is running on `127.0.0.1:4318`.
- Open the service worker's DevTools from the extension card and look for `[BG-Studio]` lines. A `Reconnecting in ...` line means Anteroom did not answer.
- `Another copy of the Anteroom Bridge is connected` means two copies are loaded. Disable one in `chrome://extensions/`.

## A marketplace action failed

1. Read the result in Anteroom. Each failure names the step and the marketplace's answer.
2. Check that Chrome is signed in to that marketplace. Anteroom's sign-in check reports it without reading cookie values.
3. In the service worker's DevTools, filter by the marketplace prefix (`[POSHMARK]`, `[MERCARI]`, `[DEPOP]`, `[SELLWILD]`, `[VINTED]`). Each request logs its status, error code and latency.
4. The same lines reach Anteroom's log: `GET /api/settings/app-logs?grep=[POSHMARK]`.

## The marketplace changed its sell page

Run that marketplace's form sweep in `tools/form-sweep/<marketplace>/` on its sell page, then its `compare.js`. Each script's header says how. The compare exits 1 on any difference.
