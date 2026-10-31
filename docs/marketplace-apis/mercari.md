# Mercari

US marketplace: https://www.mercari.com/
Official Mercari Shops Japan reference: https://api.mercari-shops.com/docs/index.html

Mercari Shops documents a GraphQL API with production endpoint
`https://api.mercari-shops.com/v1/graphql` and shop-issued bearer tokens. That is
not the Mercari US consumer interface used here. No official public US consumer
seller contract was located in this pass; do not apply Shops schemas to US calls.

Anteroom's extension uses `https://www.mercari.com/v1/api` for persisted GraphQL
operations and `/v1/initialize` for the current session. Operation names include
`publicProfileSearchQuery` for imports and `sellFetchItemsDetails` for item reads.
The operation registry in the native module owns the query hashes; do not copy
those hashes into documentation as a second source of truth.

Implementation: [native requests and operation registry](../../vendoo-extension/background/mercari-native.js),
[imports](../../vendoo-extension/background/marketplace-import.js),
[server API helper](../../vendoo-studio/server/vendoo_studio/services/mercari_api.py),
[native form](../../vendoo-studio/server/vendoo_studio/services/mercari_form.py).
Coverage: [native tests](../../vendoo-extension/tests/mercari-native.test.js).
