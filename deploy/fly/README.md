# Anteroom hosted API on Fly.io

For current production requirements and known deployment gaps, follow
[the Fly.io and Supabase operating guide](../../docs/hosted-production.md).
The incremental feature descriptions below include earlier rollout stages;
the hosted entry point's registered routes define the current API surface.

This deploys `vendoo_studio.hosted:app`, not the desktop API. The cloud UI provides email-and-password accounts (sign in, create account, password reset) and private draft inventory, General listing fields and photos. `GET /api/health` is public; `/api/account` and every `/api/drafts` route
require a verified seller session. Job and marketplace routes are not exposed. Chrome, Keychain
and desktop timers do not run in this container.

## Automatic deploys

`.github/workflows/deploy-hosted.yml` deploys every push to `main` once CI
passes on it. It ships the newest commit on `main`'s first-parent line, from
the commit whose CI started it up to the tip, that passed CI and has no notes
left in `changelog.d/` (the `Release Anteroom` commit the Version workflow
pushes next ships that code with its version). A later run never picks an
older commit, so CI runs that finish out of order or a re-run can never put
older code over newer code, and a busy stream of merges still deploys the
newest tested one (`.github/scripts/deploy-gate.py`). A manual run checks
CI on the tip the same way. When no such commit exists it skips, successfully
and with a notice. Only runs that will deploy queue for the one-at-a-time
deploy slot, so a failed CI on `main` never displaces a waiting deploy.

Then, in order, it:

1. waits for CI to pass on every merge on `main` since the commit production
   runs, since CI on `main` checks only what each merge changed
   (`.github/scripts/wait-for-tested-code.py`). Production's commit is the
   last successful run's `Deployed commit` notice; without one it checks the
   nearest merge only. A failed merge stops every deploy until its CI passes
   on a re-run or the failure is fixed;
2. builds the image on Fly's remote builder and pushes it with the label
   `v<version>-<commit>` (`flyctl deploy --build-only --push`);
3. applies pending Alembic revisions with the migration login
   (`alembic upgrade head`, `sslmode=verify-full` against the bundled CA),
   retrying the first connection for a couple of minutes when the session
   pooler is full;
4. re-imports the shared category trees unless the database already holds
   exactly the rows of `vendoo-studio/data/category-trees-seed.json.gz`
   (`category-trees.sh import --if-changed` compares them row for row);
5. deploys the image built in step 2 (`flyctl deploy --image`);
6. waits for `https://myanteroom.com/api/health` to report the new version
   and `/api/ready` to confirm the schema;
7. checks that every Machine in every process group (`flyctl machines list`)
   is started on the new image label, since the public URL reaches only the
   web Machines (`.github/scripts/check-fly-machines.py`);
8. records the commit it shipped as a `Deployed commit` notice.

Failed CI on any of those merges or a failed build stops the run before the database
changes, and a failed migration stops it before Fly is touched. Run it by hand from the Actions tab
(**Deploy hosted → Run workflow**) to redeploy `main`.

It uses the `production` GitHub environment, which only `main` may deploy
from:

| Name | Kind | Value |
| --- | --- | --- |
| `FLY_API_TOKEN` | environment secret | Fly deploy token scoped to this app (`fly tokens create deploy`) |
| `ANTEROOM_MIGRATION_DATABASE_URL` | environment secret | Migration login, session pooler (port 5432) URI |
| `SUPABASE_PUBLISHABLE_KEY` | repository variable | Public publishable key baked into the web build |

The migration URI stays in GitHub; Fly never receives it. Revisions that need
a drained database (see `docs/hosted-production.md`, section 4) still apply
automatically, so check for active work before merging one.

## Rollback and expand/contract

Migrations run before the new image starts, and the image before it keeps
serving until the new one is healthy, so every revision must work with the
code one release behind it. Change the schema in two releases:

1. **Expand.** Add the new table, column or index (nullable or with a
   default) and ship the code that writes and reads it. Keep the old one in
   place.
2. **Contract.** Drop or rename the old column, table or constraint only in a
   later release, after the release that stopped using it is live. A
   destructive change always waits one release behind the code that stops
   using it.

To roll back the code, redeploy the previous image by its label. Labels are
`v<version>-<first 8 characters of the commit>`, as the **Build the image**
step prints (`flyctl releases --image --config deploy/fly/fly.toml` lists
recent ones):

```sh
fly deploy . --config deploy/fly/fly.toml --ha=false \
  --image registry.fly.io/peaceful-meadowstone-6944:v<previous version>-<commit>
```

Alembic revisions are not rolled back. The older image serves the expanded
schema because of the rule above, but `/api/ready` answers
`schema_mismatch` until a fixed release (a new commit on `main`) deploys, so
fix forward rather than downgrading the database. Pause automatic deploys
(disable the **Deploy hosted** workflow) while production runs a rolled-back
image, or the next push to `main` replaces it.

## Manual deploy

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

The configuration runs two process groups from one image. `app` (role `web`,
one performance CPU, 2 GB) serves HTTPS with a health check and is kept running
(`auto_stop_machines = 'off'`, `min_machines_running = 1`). `worker` (role
`worker`, one shared CPU, 2 GB) has no public service and runs the draft worker,
the scheduled automations, offer checks and Sourcing refreshes. Machines without
a service are never auto-stopped. Supabase's project URL is
public configuration. No database password, service-role key, provider key or
seller data is needed for this first deployment. Later hosted data routes
require the remaining cloud work in `docs/cloud-architecture.md`.

The root URL and built `/assets/` files serve the public sign-in UI. All data
routes require bearer authentication. Open `/api/health` to check the service.
The cloud build is a separate entry point, not the desktop interface.

## Region

The API runs in Fly's supported `sjc` (San Jose) region. Supabase is in AWS
us-west-2 (Oregon); a production `SELECT 1` measured about 27 ms on 2026-11-02.
Fly rejects new Seattle Machines because `sea` is deprecated, so do not set
`primary_region` to `sea` or try to clone the running Machine there.

Before changing regions, confirm the destination accepts new Machines and
measure database latency there. Keep the existing healthy Machine until a
replacement passes health checks. Changing `primary_region` alone does not
move a running Machine.

## Shutdown during deployments

Fly sends SIGTERM with a 300-second grace period. Uvicorn allows active requests
120 seconds to finish; the draft worker then stops claiming tasks and has up to
120 seconds to save its current draft before the background loop is stopped.
This also drains draft work whose browser has disconnected. Work that exceeds
the grace period remains protected by its expiring lease and is never marked
complete or replayed automatically. The seller must review the saved draft and
retry interrupted work. A new web version offers a Reload app button instead
of automatically reloading an open editor.

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

Use the transaction-pooler runtime URI (port `6543`) with `sslmode=verify-full` and
`sslrootcert=/app/supabase-prod-ca-2021.crt` on Fly. The image includes Supabase's
public root CA, downloaded over HTTPS from the same URL used by its dashboard:
https://supabase-downloads.s3-ap-southeast-1.amazonaws.com/prod/ssl/prod-ca-2021.crt
This is a public certificate, not a private key. Renew it if Supabase rotates
the issuer. Supply the runtime URI via Fly secrets as `ANTEROOM_DATABASE_URL`;
never put the URI or its password in fly.toml, source, logs or the frontend.

Keep the migration login on the session pooler (port `5432`).

The hosted process keeps a small SQLAlchemy pool (8 connections, 4 overflow)
to the transaction pooler and disables psycopg prepared statements. A new
client connection measured about 800 ms from San Jose; reusing one measured
about 70 ms, which was faster than session mode or a direct connection on
2026-11-03. The pool waits at most three seconds for a free connection. Desktop
and test processes still use `NullPool`. Supabase still enforces its own
connection limit. On that date the database reported 60 `max_connections` and
about 40 sessions already in `pg_stat_activity`, so this process stays within a
budget of 12. A second process needs its own budget before it is added.

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


## Account deletion

Revision `7c3e9a1d5b20` adds the private functions that let a seller delete
their own account (see [account deletion](../../docs/account-deletion.md)).
They delete from `auth.users` and `vault.secrets` and read `storage.objects` as
the migration account, so run migrations as `postgres` on Supabase as before. No
new secret is needed. If that account lacks those privileges, deletion answers
503 and deletes nothing; check with
`SELECT has_table_privilege('auth.users', 'DELETE')` as the migration account.


## Desktop Chrome connection

The hosted header offers Connect Chrome for Bridge 0.2.38 or later. This is a
browser-local consent flow, not a server credential or a marketplace job. Users
must load/reload the updated extension and refresh the page before connecting.
See [the protocol and manual test](../../docs/cloud-extension.md).
