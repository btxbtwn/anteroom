# Security

## Reporting a vulnerability

Report vulnerabilities privately through GitHub Security Advisories on this repository (**Security → Report a vulnerability**). Do not open a public issue, pull request or discussion for a security report. Include the component, steps to reproduce and the impact you observed. Do not test against other sellers' accounts or data.

## Secrets

Never commit API keys, OAuth credentials, listing photos, generated diagnostics, or private exports; CI scans the tree with gitleaks. Keys and sign-in tokens must not enter the database in plain text, logs, frontend responses, or extension messages. On the Mac they stay in macOS Keychain; for hosted accounts they stay in Supabase Vault.

## Marketplace actions

Automation stops at saved drafts. Listing, updating and delisting run only when the seller names the marketplaces and confirms. See **Security and Product Invariants** in [`AGENTS.md`](AGENTS.md).

## Mac app

Anteroom binds only to `127.0.0.1`. Optional Tailscale Serve may proxy the seller's private tailnet to that port; Funnel is never used. Requests through Serve are accepted only from the Tailscale login that owns the Mac. Only Anteroom Bridge's extension IDs (the store item and the unpacked folders Anteroom knows, plus `ANTEROOM_BRIDGE_EXTENSION_IDS`) count as a trusted extension origin.

Release builds of the app and the subscription connector are signed with Anteroom's Developer ID Application certificate (team `JWN57LH9PU`) with the hardened runtime, notarized by Apple and stapled, so Gatekeeper opens them without workarounds. Entitlements are limited to what the bundled Python and Node runtimes need (see `vendoo-studio/scripts/sign_macos_bundle.py`). The in-app updater installs only a download whose SHA-256 matches the release and whose signature satisfies a fixed requirement: Anteroom's bundle identifier signed by that team, or, until 2027-02-12, by the self-signed certificate earlier releases used. Moving from that certificate to Developer ID makes macOS ask once for the Mac password before Anteroom can read its Keychain items again.

The extension pairs with the local app through a token served at `GET /api/extension/pairing-token`. That endpoint has no authentication. Browser pages on other origins cannot read it because of CORS, but any process running as the same user on the Mac can. The token only lets a client act as the extension over the loopback websocket on port 4318. It never grants access to API keys.

## Hosted API

The hosted API runs on Fly.io behind `https://myanteroom.com` and stores data in Supabase. Every request needs a Supabase session except health and readiness checks, the sign-in configuration, the web app's static files and marketplace webhooks. Machine-to-machine `/internal/` routes authenticate with a shared process token and must not be reachable from the public edge. Every seller table has row-level security keyed to the seller's `user_id`, and the runtime uses an unprivileged database login; migrations use a separate one. See [`docs/hosted-auth.md`](docs/hosted-auth.md) and [`docs/seller-isolation.md`](docs/seller-isolation.md).

## Chrome extension

The extension's only page-facing content script runs on `https://myanteroom.com`, top frame only, and accepts messages only from that origin. Connecting Chrome to a hosted account requires the seller to approve it in the extension's own `cloud-connect.html` window. With the Mac app, it talks to `http://127.0.0.1:4318` using the pairing token above.
