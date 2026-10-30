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

## Postgres connection

Use separate migration and runtime logins. Apply the canonical Alembic history
with the migration login; never stamp a freshly created Supabase database or
replace the history with SQL copied into the dashboard.

The runtime login must be unprivileged with NOINHERIT, have no direct table
privileges, and be able to SET ROLE to `anteroom_seller`. Hosted work must use
`SellerDatabaseSession`, which sets the transaction role and verified owner.
Do not give Fly the migration account's password.

Use a session-pooler connection URI with `sslmode=verify-full` and
`sslrootcert=/app/supabase-prod-ca-2021.crt` on Fly. The image includes Supabase's
public root CA, downloaded over HTTPS from the same URL used by its dashboard:
https://supabase-downloads.s3-ap-southeast-1.amazonaws.com/prod/ssl/prod-ca-2021.crt
This is a public certificate, not a private key. Renew it if Supabase rotates
the issuer. Supply the runtime URI via Fly secrets as `ANTEROOM_DATABASE_URL`;
never put the URI or its password in fly.toml, source, logs or the frontend.

The initial hosted API does not yet expose database-backed listing routes.
Provisioning this connection is a prerequisite, not completion of cloud sync.

The migration-version table intentionally has RLS enabled without a permissive
policy: only its migration owner may read or change it. Supabase may report
this as an informational [RLS enabled without policy](https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy)
notice. Do not add a client policy to silence that expected notice.
