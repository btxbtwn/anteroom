# Hosted seller sessions (cloud step 1b)

The first accounts increment is a separate hosted API entry point,
`vendoo_studio.hosted:app`. It verifies seller identity without importing the
desktop API, opening its database, or starting its Chrome and automation services.
The desktop app continues to use `vendoo_studio.main:app` on loopback.

## What works

- `GET /api/health` is public and contains only service health and version.
- `GET /api/account` requires an `Authorization: Bearer` Supabase access token
  and returns only the verified `user_id`. It does not create a database account.
- Every other HTTP request requires the same verification, including unknown
  routes. WebSocket connections are rejected until a hosted protocol is added.
- Verification checks the signature, expiration, issue time, project issuer,
  `authenticated` audience and role, seller UUID and session UUID. Anonymous
  sessions and shared-secret/API-key tokens are rejected.
- Public signing keys are cached for ten minutes. PyJWT refreshes for an unknown
  key ID after its thirty-second refresh cooldown. A key-server outage fails
  closed with 503; invalid credentials receive 401. Errors never return tokens
  or upstream diagnostics.

Use an asymmetric Supabase signing key (ES256 or RS256). No Supabase secret key,
service-role key or JWT shared secret is needed. This follows
[Supabase's JWT verification guidance](https://supabase.com/docs/guides/auth/jwts)
using the existing [PyJWT library](https://pyjwt.readthedocs.io/en/stable/usage.html).
PyJWT's minimum version is 2.15.1 for its JWKS refresh cooldown, serialized key
fetching and redirect protections.

Verification establishes the identity in the access token. It does not check
session revocation on every request; a signed-out session's access token can
remain valid until it expires. Configure the project's access-token lifetime
accordingly. Immediate revocation checks can be added when account lifecycle
requirements are defined.

## Run locally

From `vendoo-studio/`, after installing the backend dependencies:

```sh
SUPABASE_URL=https://YOUR_PROJECT.supabase.co \
  python -m uvicorn vendoo_studio.hosted:app --host 127.0.0.1 --port 4319
```

`SUPABASE_URL` is the public project origin, with no path, query or credentials.
Startup refuses an absent or invalid origin. This setting applies to the hosted
entry point only.

The automated tests generate real signing keys and access tokens, exercise the
HTTP endpoints, and replace only the public-key HTTP transport. They cover two
seller identities, forged and expired tokens, another project's issuer,
required claims, both supported signing algorithms, key rotation, outage
recovery, and absence of desktop routes.

Before connecting a real client, also test an actual Supabase sign-in against
`/api/account`. That live test needs a Supabase project and is not represented
by the generated-token tests. Keep access tokens out of shell history and logs.

## Next increments

1. The [seller ownership schema](seller-ownership.md) adds `user_id` through
   Alembic on SQLite and Postgres. Safe account claim/import of the existing
   Mac owner's records is still needed.
2. Enforce ownership in database sessions and Supabase row-level security;
   prove cross-seller reads and writes fail, including background jobs.
3. Scope photos, settings and other file-backed state to a seller, then expose
   seller data routes through the hosted app.
4. Add client sign-in and session refresh, then the deployment and migration
   work in cloud step 2.

This increment is an identity foundation, not the complete accounts feature.
The hosted app deliberately has no listing, photo, job, settings or marketplace
routes yet. Do not deploy the desktop API as a substitute: it still assumes
one local seller and does not enforce hosted authentication or tenant isolation.
