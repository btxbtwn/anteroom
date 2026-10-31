# Agent sandbox workflows

Use sample data before proposing changes to the real app. All local launchers
bind to loopback. They never use the installed app's database, account or keys.
Listing, delisting and marketplace update routes are refused.

## Desktop workspace

Run `./sandbox.sh`. The desktop API is on 4319 and UI on 5174. The existing
Playwright suite exercises inventory, editing, generation and publish refusal
at desktop and phone sizes: `cd vendoo-studio && npm run e2e`.

The packaged Mac app is also exercised after packaging in
`.github/workflows/studio-macos.yml`: the bundle starts with
`ANTEROOM_SANDBOX=1`, a temporary data folder and port 4321, serves its bundled
UI, and refuses listing and delisting. This checks the executable that ships,
not only the source server.

## Hosted workspace

After the normal developer setup, run `./sandbox.sh --hosted`. It starts:

- the real hosted API on 4320 with a fresh local Postgres server;
- the cloud UI on 5175 (open `/`);
- local session and photo-storage protocol stand-ins under `/local`.

Choose Alice or Bob on the local sign-in screen. Their drafts are isolated by
the same Postgres roles and RLS policies used in production. The real hosted
middleware verifies signed, expiring JWTs using an ephemeral local signing key.
The stand-ins do not replace or disable production authentication. Session
refresh and photo reads/uploads/deletes use the normal clients and routes.
Alice starts with the sample listings/photos; Bob starts empty.

The launcher ignores inherited database URLs, creates its own temporary
Postgres database, migrates it through Alembic and imports the bundled category
trees. It blocks outbound networking and uses the deterministic fake provider.
Only the hosted draft worker runs; it cannot publish. Ctrl+C stops the servers
and discards their data. No Docker, cloud account, email or credentials are needed.

Run `cd vendoo-studio && npm run e2e:hosted` for desktop/phone hosted tests.
The tests cover signed-in inventory, photo ownership across two sellers,
publish refusal and generation through the real durable draft worker.

This is a protocol stand-in for local reproduction, not a test of the live
Supabase Auth, Storage or Realtime service. Real service integration belongs to
an isolated staging project; never run it against production seller data.

## Native iPhone app

On a Mac, run `./sandbox.sh --hosted`, then launch the Debug app in Xcode with
the scheme environment variable `ANTEROOM_SANDBOX_URL=http://localhost:4320`.
It signs into the sample Alice account automatically. No publishable key or
Keychain account is needed. The environment override accepts only loopback
HTTP URLs and is compiled out of Release builds.

Run `./anteroom-ios/test-sandbox.sh` on a Mac with Xcode, XcodeGen and the
normal backend developer dependencies installed. It starts its own disposable
hosted API and runs XCUITest, creating a draft through the native UI and
checking it survives an app relaunch. It uses the iPhone 17 Simulator by
default; `ANTEROOM_TEST_DESTINATION` selects another Xcode test destination.
`.github/workflows/ios.yml` runs the same command on pull requests. No signing
or TestFlight credentials are available to that test job.

## Hosted staging preparation

No paid staging resources are provisioned automatically. Prepare a public
configuration for a *separate* Fly app and Supabase project with:

```sh
python3 deploy/fly/prepare-staging.py \
  --app anteroom-example-staging \
  --supabase-url https://example-staging-project.supabase.co
```

This only writes ignored `deploy/fly/staging.generated.toml`; it makes no
network calls and never deploys. It refuses the production Fly app, production
Supabase project and URLs containing credentials. The configuration starts no
minimum Machines and permits idle shutdown, but running it can still incur
charges. Resource creation and deployment remain outside the no-paid-resources
scope.

Before any future deployment, use separate staging migration/runtime database
logins, apply the canonical Alembic migrations and category seed, configure
Supabase Auth's redirect to the staging origin, and configure private Storage
and Vault as documented in `deploy/fly/README.md` and
`docs/cloud-architecture.md`. Supply secrets through Fly's secret store;
never copy production data or secrets. Real hosted entry points always require
Supabase sessions; the local sandbox entry point refuses to run on Fly.
