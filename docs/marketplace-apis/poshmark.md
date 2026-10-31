# Poshmark

Marketplace: https://poshmark.com/

No official public seller OpenAPI contract was located during this documentation
pass. This is a search result, not a claim that no partner API exists. Services
calling themselves a Poshmark API are not necessarily operated by Poshmark.

The repository uses signed-in web requests. Its implementation is the local
reference for payloads, session handling, draft status, and recovery:

| Purpose | Observed interface |
| --- | --- |
| Category catalog | `GET /vm-rest/meta/catalog_v2` |
| Seller closet import | `GET /vm-rest/users/{id}/posts/filtered` |
| Read listing | `GET /vm-rest/posts/{id}` |
| Save listing fields | `POST /vm-rest/posts/{id}` |
| Upload photo | `POST /api/posts/{id}/media/scratch` |

These are private web interfaces and may change without a versioned public
contract. Authentication stays in Chrome. Read back draft/live status before
deciding whether a failed request can be repeated.

Implementation: [native requests](../../vendoo-extension/background/poshmark-native.js),
[imports](../../vendoo-extension/background/marketplace-import.js),
[native form](../../vendoo-studio/server/vendoo_studio/services/poshmark_form.py).
Coverage: [native tests](../../vendoo-extension/tests/poshmark-native.test.js).
