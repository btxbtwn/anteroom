# Postgres shard timing observations

CI retains one `pg-duration-observations-<run>-<attempt>-<group>` artifact for
seven days after each successful Postgres shard test step. Treat these artifacts
as public-readable to signed-in users: the repository is public.

The pinned pytest-split writer aggregates setup/call/teardown seconds from xdist
controller reports into a raw temporary nodeid/duration file. Nodeids include
parametrized test labels and can contain parameter values. JSON-only output is
not credential redaction. Before upload, a sanitizer replaces each full nodeid
with its SHA-256 hex digest and accepts only finite nonnegative numeric durations.
The artifact contains only digest/duration pairs. Raw files, test output,
exceptions and request logs are never uploaded by these steps. Invalid/empty
payloads fail sanitization without echoing their contents.

Source review at base `4f74c3d4966019d2735244ebcd1aa264232b3830` inspected 226
parametrize decorators, two parametrized fixtures and their computed ID sources.
No runtime-secret-derived IDs or custom parameter-ID/collection-generation or
nodeid-rewriting hooks were found. Computed sources included fixed test mappings,
helpers and checked-in category/option fixtures and deployment configuration.
Some IDs include fake credential literals. This source review is not a guarantee
about future tests. Unsalted hashes are pseudonymization, not secrecy: predictable
labels can be recovered by dictionary guesses. Future tests must not derive IDs
from sensitive runtime values or PII, and such IDs must not be published even
hashed. Review parameter/ID sources before publishing new observations.

These are observations, not an approved balancing profile. Failed, interrupted
and cancelled test steps do not upload them. Missing artifacts must never be
interpreted as zero duration or successful coverage. A successful individual
shard is not a complete suite profile.

Before a later rebalance, require both successful Postgres shards from the same
run, attempt, tested commit and dependency lock. Hash each nodeid from the exact
collected suite with SHA-256 over its UTF-8 bytes to map observations back to tests.
Verify exact union with no overlap, missing nodes or collection errors; inspect
skips/failures from CI results. Keep source run/attempt/commit provenance outside
the digest/duration payload. Do not substitute SQLite measurements. Verify
new/unknown-node inclusion and stable collection with xdist, preserve ordering
and fixture isolation, and compare actual same-head A/B runs before claiming an
improvement. Summed test durations are not runner minutes or parallel wall time.

Instrumentation changes neither shard selection nor worker count. The raw path
is unique per run/attempt/shard, outside the checkout, and supplies no input
profile; the test step fails if it already exists. CI never downloads or consumes
these artifacts as a cache. Retention is requested as seven days; inspect the
artifact API's created_at/expires_at to verify observed expiry after collection.
