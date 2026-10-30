# Hosted database isolation (cloud step 1d)

Revision `f1c84a9d7b32` follows the ownership revision. On Postgres it enables
row-level security on all 45 seller tables and gives the restricted
`anteroom_seller` role SELECT, INSERT, UPDATE and DELETE privileges. A
restrictive policy requires `user_id` to match the transaction's verified
seller UUID in `request.jwt.claims`. Missing claims and the reserved local
owner match no records. The two public taxonomy tables have read-only policies
and the restricted role can only SELECT them. SQLite schema and local sessions
are unchanged.

The design follows [PostgreSQL row security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)
and [transaction-local role selection](https://www.postgresql.org/docs/current/sql-set-role.html).
Restrictive policies are ANDed with permissive ones, so another permissive
policy cannot widen the owner check for a normal database role.

## How hosted code uses it

Future hosted routes must use `get_seller_db`, which takes only the verified
identity placed in request state by the session middleware. Hosted workers
must construct `SellerDatabaseSession(user_id=verified_account_uuid, bind=engine)`
explicitly for each job. Never use desktop `SessionLocal` in hosted work.

Every transaction switches to `anteroom_seller` and sets the seller claim on
that transaction's connection. Both settings disappear on commit or rollback,
including when the pool gives the connection to a different seller. ORM
inserts receive that seller's ID; explicit owner changes and writes through
raw SQL are checked by the database too. Bulk SQL inserts must supply the
seller ID: their local-owner default is rejected.

The session refuses SQLite, the local owner, and a database role with
superuser/BYPASSRLS powers or ownership of public database objects. It also
checks that all seller tables have active restrictive row policies before
running a seller query, refusing an incomplete migration. This is a
guard against an incorrect deployment, not a sandbox for untrusted SQL. The
server must continue to use parameterized queries and never accept raw SQL
from clients.

## Roles and deployment

The migration account needs permission to create a NOLOGIN role, grant role
membership, manage policies and grant table privileges. An existing
`anteroom_seller` role must already be unprivileged with NOLOGIN and NOINHERIT;
the migration refuses an
unsafe role instead of silently altering a role used by another database.
No production database or credentials are provisioned by this change.

Use a separate runtime login which can SET ROLE to `anteroom_seller` and has no
direct seller-table privileges; keep migration credentials out of the hosted
runtime. The migration grants membership to its operator; runtime membership
must also be provisioned before deployment. Table owners and superusers bypass
RLS for migrations and local administration, so the restricted transaction role
is mandatory. Direct Supabase client access stays denied unless an explicit
permissive policy is added; Realtime authorization is still pending.

The role is cluster-wide and may serve more than one database. Downgrade removes
this database's policies and grants, but keeps the role and its memberships.

## Validation and remaining work

Postgres tests use two sellers and the local owner, exercising ORM reads,
relationship loads, raw SQL without filters, writes, owner spoofing, missing
claims, pool reuse, savepoints, and refusal of a table-owning runtime role.
They also upgrade populated ownership-era data and check every seller table's
policy. SQLite ownership and snapshot tests still run on the Mac path.

This increment adds no hosted listing routes and starts no cloud jobs. Existing
desktop requests and background timers remain local. Before exposing hosted
writes, parent/child references must also enforce matching owners, with tests
for cross-seller references. Photo/settings file scoping,
account import, Realtime policies and client sign-in also remain to be built.

## Seller-specific keys (cloud step 1e)

Revision `a4a9eee52599` scopes external and seller-chosen unique keys to
`user_id`: observed category schemas and nodes, diagnostic observations,
eBay offer rules, offer floors, the field registry and marketplace order
lines. Copy-request receipts and photo fingerprints use composite primary
keys `(user_id, request_id)` and `(user_id, checksum)`. Receipt lookups query
inside the existing seller session, so its row policy selects the seller's
receipt even when another account supplied the same request ID. Generated
record IDs remain globally unique.

The partial unique index for an eBay action in `sending` state now applies
per seller. It still prevents two concurrent eBay writes from one account;
one seller no longer holds that lock for every other shop. This change does
not enable hosted marketplace actions or alter approval requirements.

The migration retains rows and replaces only constraints and the partial
index on Postgres, preserving row policies and grants. SQLite rebuilds the
affected tables. Legacy adoption follows SQLite's documented
[table-rebuild procedure](https://www.sqlite.org/lang_altertable.html#otheralter):
it temporarily disables foreign-key enforcement on the migration connection,
checks references before commit and restores enforcement before returning the
connection. The existing pre-migration snapshot remains the recovery path;
downgrading these keys would risk discarding another seller's records and is
refused.

Tests upgrade a populated database from revision `9664651bec7c`, compare every
seeded record and the resulting schema, and prove identical keys are accepted
for two owners while duplicates within an owner are rejected. Real restricted
Postgres sessions also verify independent copy receipts, fingerprints and eBay
locks. Same-owner parent/child constraints remain the next migration; hosted
listing routes must stay closed until those are in place.
