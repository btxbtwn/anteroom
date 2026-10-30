# Anteroom hosted API on Fly.io

This deploys `vendoo_studio.hosted:app`, not the desktop API. The initial
service provides public `GET /api/health` and authenticated `GET /api/account`.
Listing, photo, job and marketplace routes are not exposed. Chrome, Keychain
and desktop timers do not run in this container.

From the repository root, after signing in to Fly:

```sh
fly deploy . --config deploy/fly/fly.toml --remote-only --ha=false
fly status --config deploy/fly/fly.toml
fly apps open /api/health --config deploy/fly/fly.toml
```

The Dockerfile path is relative to the configuration directory; the build
context is the repository root. The allowlist in `.dockerignore` excludes
local data, photos, databases, credentials, tests and the extension.

The configuration uses one shared CPU, 512 MB memory, HTTPS, a health check,
automatic start and stop, and zero minimum running Machines. Supabase's project
URL is public configuration. No database password, service-role key, provider
key or seller data is needed for this first deployment. Later hosted data
routes require the remaining cloud work in `docs/cloud-architecture.md`.

The root URL requires authentication. Open `/api/health` to verify the initial
service; this is the API foundation, not the complete seller web interface.
