# Anteroom hosted API on Fly.io

This deploys `vendoo_studio.hosted:app`, not the desktop API. The cloud UI provides email-and-password accounts (sign in, create account, password reset) and private draft inventory, General listing fields and photos. `GET /api/health` is public; `/api/account` and every `/api/drafts` route
require a verified seller session. Job and marketplace routes are not exposed. Chrome, Keychain
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

The configuration uses one shared CPU, 2 GB memory, HTTPS, a health check,
automatic start and stop, and zero minimum running Machines. Supabase's project
URL is public configuration. No database password, service-role key, provider
key or seller data is needed for this first deployment. Later hosted data
routes require the remaining cloud work in `docs/cloud-architecture.md`.

The root URL and built `/assets/` files serve the public sign-in UI. All data
routes require bearer authentication. Open `/api/health` to check the service.
The cloud build is a separate entry point, not the desktop interface.

## Shared category trees

The six category trees are shared reference data, not seller rows. The runtime
role can only read them. Load or refresh them from the bundled seed with the
migration login, after migrations and whenever `data/category-trees-seed.json.gz`
changes:

```sh
export ANTEROOM_MIGRATION_DATABASE_URL='<migration login session-pooler URI>'
cd vendoo-studio && ./scripts/category-trees.sh import --database-url-env ANTEROOM_MIGRATION_DATABASE_URL
```

The command takes the URL from that variable so it never appears in arguments or
output. It replaces both tables in one transaction. The web app offers category
pickers only once all six trees are complete.

Each seller's category search index is built in the background on first use and
kept in memory (about 250 MB on disk and up to roughly 575 MB while building).
Until it is ready, search uses the verified whole-word fallback. The Machine has
2 GB for this. One shared category index is needed before many sellers use the
web app at once.

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

The hosted API exposes only draft inventory and its General fields/photos. Full Mac inventory import and marketplace actions remain separate increments.

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
Sellers sign in with an email and password. New accounts confirm their email
first (Supabase's **Confirm email** stays on); a confirmation link only has to
be opened, after which the seller signs in with the password anywhere. Password
reset links use PKCE: open the link in the browser that asked for it, which is
why the Mac app sends resets to the default browser. Supabase
manages session storage and refresh; the backend independently verifies every
access token. API responses are not cached, and signed-out account UI state is
discarded. Uvicorn access logs are disabled so callback codes do not enter logs.
Supabase's default mail service is limited; public onboarding requires the
project's production email delivery setup.

Signed-in sellers can create a draft, edit its title and notes, explicitly save,
and reopen it. Inventory starts with the 100 most recently updated drafts and supports search and Load more. Saves
include the last server timestamp; a stale edit returns 409 and retains the
unsaved text until the seller reloads. Non-draft records are not exposed.

Draft text uses the seller-owned `conversations` table; General fields and photos use the canonical listing revisions and photo metadata. The request
contract accepts no owner, client-selected record ID, status, parent ID, file
path, history pointer or marketplace action. It starts no timers and does not expose settings or operational JSON. Only the current, same-owner, same-draft revision is used for General values. The remaining file and historical-reference audit still gates other capabilities.

The container's runtime directory is under the non-root user's home, not the
root-owned application source. It holds only ephemeral runtime directories in
this increment; draft data lives in Postgres. The API dependency refuses local
SQLite if the cloud database is missing.


## Private draft photos and General fields

Apply the canonical Alembic head before deploying. Revision `c25ff7e26196`
creates a private `anteroom-photos` bucket on Supabase and restrictive owner
policies on `storage.objects`. On SQLite and ordinary Postgres, which do not
have Supabase's managed `storage` schema, the revision leaves storage unchanged.
Do not expose this bucket publicly or add service-role credentials to Fly.

Supply `SUPABASE_PUBLISHABLE_KEY` to the hosted process (the same public key
used by the browser build, through Fly secrets or deployment environment).
Storage calls use that public key and the request's verified seller JWT.
No elevated storage token is used. Objects live at
`<verified-user-id>/<server-created-draft-id>/<random-id>.jpg`.

Uploads are streamed with a 10 MB limit and accept JPEG, PNG and WebP up to
25 megapixels. The server normalizes orientation, strips metadata and resizes
to at most 2400 pixels before storing a JPEG. Each draft accepts 30 photos.
The parent draft is locked during attachment, so simultaneous requests cannot
exceed the count. A failed metadata commit triggers best-effort object cleanup;
rare failed cleanup can leave a private unreferenced object for later maintenance.
Photos save independently of the explicit Save draft action. Reads and removals
check both the seller and parent draft; image bytes require bearer authentication
and reach the browser as temporary blob URLs, revoked when the view closes.

General edits create canonical `ListingRevision` records and update `Listing`
inside the same optimistic timestamp transaction as title and notes. The API
accepts only typed General fields, never arbitrary listing JSON or history IDs.
Legacy revision pointers must match the same seller and draft before reading.
Desktop and cloud General editors share the field definition and section rules.
Search runs on seller-owned draft titles; Load more pages through 100 rows.
Active/sold inventory, importing Mac data, generation and marketplace tabs are
not enabled by this increment.

Before accepting public users, test in two real browser accounts: upload a photo,
save fields, reopen after reload, then confirm the other account cannot load or
remove the first account's photo. Automated tests exercise the same ownership
boundaries with real Postgres sessions; Storage HTTP transport is replaced locally.


## Desktop Chrome connection

The hosted header offers Connect Chrome for Bridge 0.2.38 or later. This is a
browser-local consent flow, not a server credential or a marketplace job. Users
must load/reload the updated extension and refresh the page before connecting.
See [the protocol and manual test](../../docs/cloud-extension.md).

Save draft on Poshmark, Mercari and Depop needs the command-signing key as the
Fly secret `ANTEROOM_COMMAND_SIGNING_KEY`, with its public key in the Bridge
(`vendoo-extension/cloud-command-key.js`). Create both at once and stage the
secret for the next deploy:

```bash
cd vendoo-studio && .venv/bin/python scripts/command-signing-key.py | fly secrets import --stage -a peaceful-meadowstone-6944
```

Commit the rewritten `cloud-command-key.js`. Without the secret the web app
offers no Save draft; without the public key, the Bridge does not.
