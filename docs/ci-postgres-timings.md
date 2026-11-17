# Postgres shard timing observations

CI retains one `pg-duration-observations-<run>-<attempt>-<group>` artifact for
seven days after each successful Postgres shard test step. Its only payload is
a JSON object mapping pytest nodeids to measured seconds (setup + call + teardown),
written by the pinned pytest-split plugin from xdist controller reports. No test
output, exceptions, request data or credentials are attached.

These are observations, not an approved balancing profile. Failed, interrupted
and cancelled test steps do not upload them. Missing artifacts must never be
interpreted as zero duration or successful coverage. A successful individual
shard is not a complete suite profile.

Before a later rebalance, require both successful Postgres shards from the same
run, attempt, tested commit and dependency lock. Verify their nodeid union against
the exact collected suite, with no overlap, missing nodes or collection errors;
validate finite nonnegative durations and inspect skips/failures from CI results.
Keep the source run/attempt/commit as provenance outside the nodeid/duration payload.
Do not substitute SQLite measurements. Test new/unknown-node inclusion and stable
collection with xdist, preserve ordering and fixture isolation, and compare actual
same-head A/B runs before claiming an improvement. Summed per-test durations are
not runner minutes or a prediction of parallel wall time.

This instrumentation changes neither shard selection nor pytest worker count.
The duration path is unique per run/attempt/shard, outside the checkout, and has
no input profile. CI never downloads or consumes these artifacts as a cache.
