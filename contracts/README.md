# Shared API contract

The request and response shapes the web app (`vendoo-studio/src/`) and the
iPhone app (`anteroom-ios/`) both use, generated from the FastAPI backend so
neither client can drift from the server or from each other.

## Source of truth

The Pydantic models in
[`vendoo-studio/server/vendoo_studio/routes/api_contract.py`](../vendoo-studio/server/vendoo_studio/routes/api_contract.py).
Both servers declare them on their routes:

- the hosted API (`routes/hosted_workspace.py`), which the iPhone app and the
  hosted web app call;
- the desktop app (`routes/conversations.py`, `routes/listings.py`), which the
  Mac app's web UI calls.

As `response_model`, they shape and check every response: a route that
returns something the contract does not describe fails instead of sending it.

## Generated files (never edit by hand)

| File | What it is |
|---|---|
| `contracts/anteroom-api.json` | OpenAPI 3.1 for the contract's operations, exported from the hosted app |
| `vendoo-studio/src/api/contract.gen.ts` | One TypeScript interface per schema, re-exported from `src/api/types.ts` |
| `anteroom-ios/Sources/API/Generated/APIContract.swift` | One `Codable` struct per schema, in the app's own naming (`coverPhotoURL`, `revisionID`) |

Regenerate after changing a contract model, and commit the result with the
change:

```sh
cd vendoo-studio && .venv/bin/python scripts/api-contract.py
```

`--check` only reports stale files. One small script writes both clients so
their naming and optional-field rules match, with no new runtime package:
Apple's swift-openapi-generator would add its runtime and generate a whole
client, and openapi-typescript's extra exports fail `knip`.

## What is in it

| Operation | Response | Request |
|---|---|---|
| `GET /api/runtime` (hosted only) | `WorkspaceRuntime` | |
| `GET /api/conversations` | `ConversationSummary[]` | |
| `POST /api/conversations` | `Conversation` | `ConversationInput` (hosted) |
| `GET /api/conversations/{id}` | `Conversation` | |
| `PATCH /api/conversations/{id}` | `Conversation` | `ConversationInput` (hosted) |
| `DELETE /api/conversations/{id}` | `DeleteConversationResult` | |
| `GET /api/conversations/{id}/listing` | `ListingDocument` | |
| `PUT /api/conversations/{id}/listing` | `ListingSaveResult` | `ListingSave` |
| `GET /api/conversations/{id}/photos` | `ListingPhoto[]` (with `PhotoEdits`, `CropBox`) | |

Errors: 400, 404 and 409 answer `ErrorDetail` (`{"detail": "<sentence for
the seller>"}`). Each operation lists what either server may answer: the
desktop app refuses a busy delete with 400 where the hosted app uses 409. A
request FastAPI rejects answers 422 with `HTTPValidationError`; the hosted
listing save also answers 422 with an `ErrorDetail` sentence when it refuses a
listing's values. Both clients read errors through these two types, sentence
first.

Rules both generators follow:

- A field with a default may be missing from a response. TypeScript marks it
  optional; Swift decodes the default. The server does send every default
  today, because `response_model` fills them in.
- A `Literal` is a union in TypeScript and its plain base type (`String`,
  `Int`) in Swift, so an older iPhone build never fails to decode a value a
  newer server adds.
- A free-form `dict` (the listing itself) is `Record<string, unknown>` in
  TypeScript and `[String: JSONValue]` in Swift.

## Checks

- `vendoo-studio/server/tests/test_api_contract.py` (backend suite, every CI
  run that touches Studio, `contracts/` or the generated Swift) fails when a
  generated file is stale, or when the desktop routes declare a response or
  body different from the hosted ones. `POST` and `PATCH /api/conversations`
  are the only operations whose desktop request body may differ (the hosted one
  limits lengths and refuses unknown keys).
- A contract change that breaks a client fails its build: `tsc` in the
  frontend job, the Swift compile and unit tests in the iPhone app workflow,
  which also runs for changes under `contracts/` and `vendoo-studio/server/`.

## Not in the contract yet

These stay hand-written on each client. Move one in when both clients need
the same shape, in its own change.

- `GET /api/conversations/{id}/screen`: the iPhone app decodes its
  `conversation`, `listing` and `photos` with the contract types, and the web
  app splits it into the per-resource caches those types describe; its
  `messages`, `jobs` and `activity` are still hand-written on each client.
- Streaming routes (`POST .../generate`, `.../messages`): server-sent frames,
  parsed by `src/api/sse.ts` and `Sources/API/StreamFrame.swift`. OpenAPI
  cannot describe a stream's frames usefully.
- Multipart uploads (`POST /api/drafts/{id}/photos`, measurement reads):
  their bodies are files, not JSON.
- Photo bytes and thumbnails: images, not JSON.
- Marketplace actions, native forms, settings, automations, analytics,
  revisions, templates, bulk edits and the rest: still typed separately on each
  client, most without a `response_model` on the server yet.
- The Chrome extension and the webhook relay: separate products with their own
  contracts (`docs/json-contract.md` for listing JSON the extension fills).

## Versions

The contract has no version of its own. Each product keeps its own release
cadence; the iPhone app's version continues to come from `vendoo-studio/VERSION`
as before (`anteroom-ios/tools/after-xcodegen.sh`).
