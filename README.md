# Anteroom

Anteroom is a cross-listing app for resellers. Photograph an item once; Anteroom writes the listing, fills each marketplace's own form, and keeps it as a draft for eBay, Poshmark, Mercari, Depop and Etsy. Listing, updating and delisting happen only when the seller confirms which marketplaces.

## Components

Directories still carry the product's old `vendoo-*` names; a rename is planned separately.

| Directory | What it is |
|---|---|
| `vendoo-studio/` | Anteroom itself: FastAPI backend (`server/`) and React web UI (`src/`). Runs as the hosted web app (Fly.io + Supabase) and as the Mac desktop app. |
| `anteroom-ios/` | Native SwiftUI iPhone app on the hosted API. Builds only on macOS. |
| `vendoo-extension/` | Bridge, the Chrome extension that fills marketplace forms and runs the marketplace actions the seller confirms. |
| `webhook-relay/` | Cloudflare Worker that receives eBay and Etsy webhooks and brokers their sign-in. |
| `skills/` | Listing rules, templates and research loops used for generation. |
| `deploy/` | Fly.io configuration for the hosted API. |
| `docs/` | Architecture, contracts and operating notes. |

## Quickstart

```bash
./setup.sh     # install the backend venv and frontend packages
./sandbox.sh   # whole app offline on sample data, fake AI, publishing refused (UI :5174)
./start.sh     # the real local app (UI :5173, API :4318, loopback only)
./check.sh     # what CI checks for the paths you changed
```

## Further reading

- [`AGENTS.md`](AGENTS.md): rules, invariants and the working loop for every change.
- [`CONTRIBUTING.md`](CONTRIBUTING.md): pull request steps and checks.
- [`SECURITY.md`](SECURITY.md): reporting vulnerabilities.
- [`VENDOO_STUDIO_SPEC.md`](VENDOO_STUDIO_SPEC.md) and [`DESIGN.md`](DESIGN.md): product spec and web UI design rules.
- [`docs/performance.md`](docs/performance.md): speed and responsiveness goals for every change across web, Mac and iPhone; measured results live in [`docs/responsiveness.md`](docs/responsiveness.md).
- [`docs/`](docs/): cloud architecture, JSON listing contract, hosted production guide.
