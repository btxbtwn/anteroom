# Depop

Official partner reference: https://partnerapi.depop.com/api-docs/reference/
Authentication: https://partnerapi.depop.com/api-docs/concepts/authentication/
Local contract: [Selling API](specs/depop.yaml).

The stored contract replaces upstream sample webhook secrets and OAuth tokens with
explicit example placeholders. Endpoint definitions and schemas are unchanged.

The partner API supports API keys for direct partners and OAuth authorization code
flow with PKCE for third-party applications. Depop must configure access first;
public documentation does not grant credentials or partner approval.

Anteroom currently uses Depop's signed-in web requests, not that partner API. The
web import reads `/api/v1/shop/{id}/products/` and
`/presentation/api/v1/products/by-slug/{slug}/`. Do not substitute partner request
schemas into the web transport. Draft creation and activation are separate actions.

Implementation: [native requests](../../vendoo-extension/background/depop-native.js),
[imports](../../vendoo-extension/background/marketplace-import.js),
[native form](../../vendoo-studio/server/vendoo_studio/services/depop_form.py).
Coverage: [native tests](../../vendoo-extension/tests/depop-native.test.js).
