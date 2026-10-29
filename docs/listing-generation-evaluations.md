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
