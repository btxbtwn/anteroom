# Anteroom Connector

Anteroom Connector is the separate macOS 13+ menu-bar app for sellers who use
Anteroom on the web or iPhone and want to generate with their subscriptions.
It starts no listing server or listing database and contains no inventory,
Chrome extension, category trees, or marketplace publishing tools.

1. Download `Anteroom-Connector-macos.zip` from the macOS release, unzip it,
   and move **Anteroom Connector.app** into Applications.
2. Open it and sign into the same Anteroom account used on web or iPhone.
3. Connect ChatGPT, Claude, or both. ChatGPT opens the browser for approval.
   Claude uses the unmodified Claude Code installation and its own login
   flow. If Claude Code is absent, **Set up Claude Code** opens its official
   installation guide. The sign-in terminal runs only `claude auth login
   --claudeai`, never an arbitrary shell.
4. Select a model in the connector, then select the subscription in Anteroom's
   web or iPhone provider settings. Keep the Mac awake, online, and the
   connector running.

Closing the window leaves the connector running in the menu bar. **Connections**
opens it again. **Pause** stops new requests and lets existing requests finish;
**Resume** accepts new work. **Quit** disconnects the worker and cancels active
calls. Use only one full desktop app or connector for the same account at a
time; the connection window reports when another worker already holds it.

Provider credentials remain in macOS Keychain or Claude Code's own login.
Anteroom account sign-in is kept in the connector's private WebKit profile,
and its access token is passed only to the local native worker. Disconnecting
the Anteroom account stops that worker's account access without signing the
seller out of Claude Code. The connector's settings and WebKit profile live
under `~/Library/Application Support/Anteroom Connector`, separately from the
full desktop app's listing data. Model and photo requests use transient local
files. Automatic generation still stops at a saved draft.

## Build and verify

Run `vendoo-studio/scripts/package-connector.sh` with the same persistent
`MACOS_SIGNING_IDENTITY` used by the existing macOS release. It signs the
connector inside-out with the hardened runtime and, when an App Store Connect
API key is set (`NOTARY_API_KEY_PATH`, `NOTARY_API_KEY_ID`,
`NOTARY_API_ISSUER_ID`), notarizes and staples it, so a downloaded copy opens
without the Control-click workaround. The macOS workflow builds both apps,
requires notarization on `main`, and publishes the connector as a separate
release asset.
The connector build excludes the full listing backend and the SDK's bundled
Claude binary; it uses the seller's Claude Code install.

For local verification only, `scripts/package-connector.sh --development`
produces `release/Anteroom-Connector-macos-dev.zip` with ad-hoc signing. That
filename is deliberately different from the distribution asset. It is not a
Developer ID release. Packaging runs the executable's `--smoke` probe using
throwaway data, without reading credentials, accessing the network, or
starting a listing database. The probe can also be run directly:

```sh
.venv/bin/python -m vendoo_studio.connector --smoke
"release/Anteroom Connector.app/Contents/MacOS/Anteroom Connector" --smoke
```

The connection window uses the hosted `/?connector` page and the existing
Supabase sign-in. Ship the hosted page before distributing this app. The
native API accepts only the configured hosted origin, and the worker accepts
only the existing inference operations; it exposes no filesystem or general
command execution API. Browser tests use the hosted sandbox with a native
bridge stand-in. Worker tests verify account scoping, concurrent calls,
pause/resume, cancellation, and shutdown without seller credentials.
