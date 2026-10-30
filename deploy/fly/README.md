# Anteroom hosted API on Fly.io

This deploys `vendoo_studio.hosted:app`, not the desktop API. The cloud UI provides email-link sign-in and private draft shells (title and
notes). `GET /api/health` is public; `/api/account` and every `/api/drafts` route
require a verified seller session. Photo, job and marketplace routes are not exposed. Chrome, Keychain
and desktop timers do not run in this container.

From the repository root, after signing in to Fly:

```sh
fly deploy . --config deploy/fly/fly.toml --remote-only --ha=false \
  --build-arg VITE_SUPABASE_PUBLISHABLE_KEY="$VITE_SUPABASE_PUBLISHABLE_KEY"
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

The root URL and built `/assets/` files serve the public sign-in UI. All data
routes require bearer authentication. Open `/api/health` to check the service.
The cloud build is a separate entry point, not the desktop interface.

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

## Cloud sign-in and drafts

The Docker build embeds Supabase's project URL from `[build.args]` and the
publishable key passed with `--build-arg`. Set `VITE_SUPABASE_PUBLISHABLE_KEY`
in your shell before deploying. The key is public browser configuration, but
keys are still kept out of the repository. Never use a service-role or secret
key there. The final image serves the cloud Vite bundle from `/app/web`.
Desktop builds still use `index.html`; cloud builds select `cloud.html` with
`ANTEROOM_HOSTED_BUILD=1`.

Configure Supabase Auth's Site URL and allowed Redirect URL to the Fly origin.
Email links use PKCE: open the link in the browser that requested it. Supabase
manages session storage and refresh; the backend independently verifies every
access token. API responses are not cached, and signed-out account UI state is
discarded. Uvicorn access logs are disabled so callback codes do not enter logs.
Supabase's default mail service is limited; public onboarding requires the
project's production email delivery setup.

Signed-in sellers can create a draft, edit its title and notes, explicitly save,
and reopen it. The list shows the 100 most recently updated draft shells. Saves
include the last server timestamp; a stale edit returns 409 and retains the
unsaved text until the seller reloads. Non-draft records are not exposed.

This increment uses only the already-owned `conversations` table. The request
contract accepts no owner, client-selected record ID, status, parent ID, file
path, history pointer or marketplace action. It does not read settings, photos,
listing history or operational JSON, and starts no timers. The remaining file
and historical-reference audit still gates those later capabilities.

The container's runtime directory is under the non-root user's home, not the
root-owned application source. It holds only ephemeral runtime directories in
this increment; draft data lives in Postgres. The API dependency refuses local
SQLite if the cloud database is missing.
