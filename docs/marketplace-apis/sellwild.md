# Sellwild

Marketplace listing page: https://sellwild.com/sell

No official public marketplace listing API contract was located in this pass.
`sdk.sellwild.com` documents an advertising SDK, not the seller listing interface.
The repository's signed-in web client integration is the local reference.

| Purpose | Observed interface |
| --- | --- |
| Import listings | `GET /api/search/listings?userId=...` |
| Read listing | `GET /api/listings/{id}` |
| Create listing | `POST /api/listings/create` |
| Confirm uploaded photos and activate | `POST /api/listings/{id}/confirm-photos` |
| Update listing | `PUT /api/listings/{id}/edit` |

The integration uploads photos through presigned URLs returned by the site. It
does not expose a remote saved-draft workflow: local preparation must stop before
the approved create/upload/activation sequence. Do not retry an uncertain
activation automatically. Keep session material and presigned URLs out of docs.

Implementation: [native requests](../../vendoo-extension/background/sellwild-native.js),
[listing preparation](../../vendoo-studio/server/vendoo_studio/services/sellwild_listing.py),
[browser service](../../vendoo-studio/server/vendoo_studio/services/sellwild_browser.py).
Coverage: [native tests](../../vendoo-extension/tests/sellwild-native.test.js).
