# Seller ownership schema (cloud step 1c)

Every seller-data model now inherits `SellerOwned`, which adds an indexed,
non-null `user_id` (a UUID string). The 45 tables include listings and their
history, photos and video metadata, jobs and approvals, sales, sourcing,
assistant history, diagnostics, and observed category/form caches. Observed
field options belong to the seller because a marketplace can return
account-specific values. Only the two bundled public taxonomy tables,
`category_trees` and `category_tree_nodes`, remain shared.

This uses [SQLAlchemy's column mixin pattern](https://docs.sqlalchemy.org/en/20/orm/declarative_mixins.html).
A coverage test requires every model to opt into ownership or be one of the
explicit public tables.

## Existing Mac data

The reserved UUID `00000000-0000-0000-0000-000000000000` means the local seller
in a desktop database. Existing records receive this owner during migration.
Both ORM and SQL inserts default to that owner so today's single-seller app
continues to work. It is a local ownership marker, not a Supabase account, and
does not identify a person across different Mac installations.

Linking or importing a Mac into a cloud account must explicitly map that local
owner to the verified account UUID. Signing in must never automatically claim
unclaimed records in a shared database. That claim/import operation is still
to be implemented; this migration does not upload or reassign records.

Alembic revision `d8b6f2a19c40` upgrades both SQLite and Postgres from
`3ec056c582ed` (the photo-fingerprint revision). It adds columns and indexes in place, without copying or
rebuilding seller tables. Database contents, existing indexes and foreign keys
are preserved. Mac startup still snapshots before the migration. A database
from before Alembic uses the same additive revision during adoption, is checked
for schema gaps, and is stamped only after that check succeeds.

Tests upgrade a database made by the previous revision containing drafts,
photos, messages, revisions, jobs, sales and assistant history. They compare
the records and constraints before and after, check the new schema against all
models, verify repeat startup, exercise explicit account ownership and SQL
defaults, and restore the pre-upgrade snapshot. The upgrade tests use Postgres
when the backend suite runs with `ANTEROOM_TEST_DATABASE_URL`.

## What this does not enable yet

Ownership columns alone do not enforce isolation. The desktop repositories and
background services still operate as one seller. The hosted API continues to
expose only health and verified identity; no seller-data routes are added.

The next increment must scope reads and writes to the verified identity, make
natural-key uniqueness and parent/child references seller-specific, and enforce
[Supabase row-level security](https://supabase.com/docs/guides/database/postgres/row-level-security).
Hosted writes must supply the authenticated account UUID and reject the local
owner default. Then file-backed state, account linking/import and client
sign-in can be connected. Two-seller access tests are required before any
seller-data route is exposed publicly.
