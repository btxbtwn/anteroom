# Listing generation evaluations

The synthetic cases in `vendoo-studio/server/tests/fixtures/generation_cases.json`
cover readable and unreadable tags, seller flaws and measurements, men's bottoms,
marketplace choices, deliberate clears, a seller's price, and separate category IDs.
They contain no seller photos or private exports.

For each case, capture a complete generated listing using its `evidence` and optional
`seller_notes`. Store a JSON object keyed by case ID, with the listing object as the
value. Keep output artifacts outside the repository. Record the provider, model,
reasoning setting, prompt revision, latency, and input/output token usage alongside
the artifact when the provider exposes them. Use identical cases when comparing runs.

From `vendoo-studio`, with the project's environment active:

```bash
python scripts/evaluate-generation.py --outputs /path/to/captured-outputs.json
```

The command reports per-case checks and aggregate rates for supported facts,
unsupported facts omitted, seller details preserved, allowed options, buyer-facing
copy, and title/description formulas. Missing cases count as failures. Draft
completeness is separate: a truthful unknown can be correct while the listing still
needs evidence. Exit status is nonzero if any case fails its quality checks.

The backend test suite exercises the evaluator with labelled synthetic outputs and
deliberately corrupted answers. Those tests validate the scoring and application
safeguards; they do not establish a model's accuracy. Capture and score actual model
outputs before claiming a model or prompt improves listing quality. Have a seller
review the cases and outputs when expanding this corpus.

Context now uses saved item details, current seller-owned fields (including clears),
and recorded corrections on each turn. Recent seller messages and relevant older
messages share a 10,000-character evidence budget; recent assistant replies and
save confirmations have a separate 10,000-character budget. Messages remain verbatim
and in chronological order. This is bounded retrieval from durable records, not a
lossless summary of an arbitrarily long conversation. Listing rules use a separate
20,000-character budget with canonical formulas, evidence policy, input rules, and the JSON field reference
pinned first; remaining space holds complete relevant sections ranked with BM25.

Validation repairs accept only add/replace JSON Patch operations on failed,
unlocked field paths. A reply that tries to change an unrelated field is rejected;
a repair must remove validation errors without introducing new ones. Unsupported
facts and invalid seller-owned values remain blockers. Generation timing reports
`preparing` until marketplace fields finish, then `done`, `incomplete`, `failed`,
or `superseded`. Cancelled streams report `cancelled`.

## Live runs and request metrics

Run the cases with the listing provider already connected in Anteroom:

```bash
python scripts/evaluate-generation.py --run --output-dir /path/to/new-evaluation-directory
```

The output directory must be new. The runner creates an isolated, migrated SQLite
store there, supplies each case's curated evidence and authoritative form inputs,
and runs the application's listing prompts, repair, and local draft-save logic.
It writes `outputs.json`, `report.json`, and the isolated draft database. It never
calls a Send, List, Update, or Delist route. These artifacts are private and must
stay outside the repository. The provider's normal model/subscription usage applies.

A Claude SDK query is one Anteroom model call; its reported usage can include
several internal model turns. The report includes per-case observed latency and
input, output,
cache-read, cache-write and reasoning token counts when the provider reports them.
Unknown counters and costs remain `null`. Anthropic's uncached input, cache-read
and cache-write counts are combined into total input once; OpenAI's input already
includes cache reads and is not added twice. `cost_source: sdk_estimate` labels
Claude SDK estimates; `provider_reported` labels a cost returned in an API usage
response. Subscription estimates are not charges or authoritative billing data.
Prompts, responses, account IDs, headers, credentials and provider error text are
never included in the request metrics.

Normal generation also stores these call records with its timing row. Read them
through the existing generation-timing API; background field completion appends
the final call records. Existing timing rows receive an empty calls list through
an additive Alembic migration, on both SQLite and Postgres.

Scoped validation repairs use native schemas for the Responses API, Anthropic
Messages, Claude Agent SDK and compatible Chat Completions providers. The schema
restricts add/replace operations to the failed, unlocked paths. An API rejection
or refusal leaves the draft unresolved; it does not retry without the schema.
Providers without a native structured-output method retain server-validated text
patches. These schemas cover repair responses; open-ended chat and the initial
listing-writing response still use the normal generation contract and validation.

On 2026-10-29, all eight synthetic cases passed a live run on the configured Claude
`sonnet` provider after the input fixtures supplied their native form and verified
category context. This is a small regression sample, not a general accuracy claim.
A separate private smoke test read five real product photos and saved a draft in
an isolated database, recovered an older seller instruction, preserved a price and
deliberate clear, invalidated analysis after photo reordering, and saved fresh
analysis. It made zero marketplace actions and opened the source inventory only
through a read-only connection. No photos, source listings or model outputs are
part of the checked-in evidence.

References:

- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Anthropic structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
- [Claude SDK structured outputs](https://code.claude.com/docs/en/agent-sdk/structured-outputs)
- [Claude SDK cost and usage accounting](https://code.claude.com/docs/en/agent-sdk/cost-tracking)
