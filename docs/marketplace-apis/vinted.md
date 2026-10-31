# Vinted

Official Pro reference: https://pro-docs.svc.vinted.com/
Local contract: [Pro Integrations](specs/vinted.yaml).

Pro Integrations is restricted to allowlisted Vinted Pro businesses. Requests use
an access key and an HMAC signature, not consumer browser cookies. Production is
`https://pro.svc.vinted.com`; the sandbox is
`https://pro-public-sandbox.svc.vinted.com`. See the official authentication section
for signing rules. The Pro API supports item management, ontologies, orders, and
webhooks; item submission is asynchronous.

Anteroom currently uses Vinted's signed-in consumer web interface, not Pro:

| Purpose | Observed interface |
| --- | --- |
| Read item | `GET /api/v2/items/{id}` |
| Create draft | `POST /api/v2/item_upload/drafts` |
| Update draft | `PUT /api/v2/item_upload/drafts/{id}` |
| Complete draft | `POST /api/v2/item_upload/drafts/{id}/completion` |
| Read catalogs | `GET /api/v2/item_upload/catalogs` |

Completion is a publishing boundary and requires seller approval. The official Pro
contract is useful for evaluating a future approved integration; it does not
describe these consumer endpoints or replace their existing implementation.

Implementation: [native requests](../../vendoo-extension/background/vinted-native.js),
[browser service](../../vendoo-studio/server/vendoo_studio/services/vinted_browser.py).
Coverage: [native tests](../../vendoo-extension/tests/vinted-native.test.js),
[draft tests](../../vendoo-extension/tests/vinted-draft.test.js).
