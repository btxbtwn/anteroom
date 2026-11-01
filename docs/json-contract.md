# Listing JSON contract

Anteroom, the listing skills, and the extension share a listing payload. This abbreviated example shows the structure; `ebay_specifics` keys use camelCase where shown. Category-dependent fields and allowed dropdown values come from the [canonical template](../skills/list-this/references/vendoo_listing_template.md) and [listing skill](../skills/list-this/SKILL.md).

<details>
<summary>Example listing JSON</summary>

```json
{
  "title": "Levi's M Blue Cotton Crew Neck T-Shirt",
  "description": "Blue cotton crew neck T-shirt...",
  "price": 25,
  "brand": "Levi's",
  "size": "M",
  "condition": "Good",
  "category_path": "Clothing, Shoes & Accessories > Men > Men's Clothing > Shirts > T-Shirts",
  "primaryColor": "Blue",
  "sku": "TEE-001",
  "quantity": 1,
  "weight_lb": 0,
  "weight_oz": 8,
  "package_dimensions_in": "13x10x3",
  "images": ["..."],
  "ebay_specifics": {
    "type": "T-Shirt",
    "department": "Men",
    "sizeType": "Regular",
    "size": "M",
    "brand": "Levi's",
    "fit": "Regular",
    "material": "Cotton",
    "pattern": "Solid",
    "style": "Basic",
    "features": "All Seasons",
    "neckline": "Crew Neck",
    "closure": "Pullover",
    "countryOfOrigin": "United States",
    "fabricType": "Jersey",
    "garmentCare": "Machine Wash",
    "handmade": "No",
    "personalize": "No",
    "vintage": "No",
    "occasion": "Casual",
    "season": "Summer",
    "unitQuantity": "1",
    "unitType": "Unit"
  },
  "depop_specifics": {},
  "etsy_specifics": {},
  "poshmark_specifics": {}
}
```

</details>

The complete template covers marketplace-specific fields beyond this example. Listing rules belong in `skills/`; app and extension code consume that contract.

## Web Save draft command

The web app saves Poshmark, Mercari and Depop drafts through the seller's
Chrome (see [cloud-extension.md](cloud-extension.md#save-draft-from-the-web-app-bridge-0240)).
`POST /api/conversations/{id}/native-marketplaces/{marketplace}/chrome-draft`
with `{"revision_id": "..."}` returns the recorded `record` (the same shape as
`GET .../native-marketplaces`) and a `command`:

```json
{ "body": "<base64url of the command JSON>", "signature": "<base64url of r‖s, 64 bytes>" }
```

The signature is ECDSA P-256 with SHA-256 over the ASCII bytes of `body` as
sent, so neither end re-serializes JSON. The decoded body:

```json
{
  "version": 1,
  "kind": "marketplace.save_draft",
  "user_id": "<seller UUID; must equal the connected account>",
  "operation_id": "<job id>",
  "expires_at": 1793548460,
  "token": "<43-character operation token>",
  "payload": {
    "marketplace": "poshmark",
    "action": "save_draft",
    "confirm": false,
    "operation_id": "<job id>",
    "draft_url": null,
    "listing_id": null,
    "listing": { "title": "...", "poshmark_specifics": {} },
    "local_draft_id": null,
    "revision_id": "<revision id>",
    "photos": [{ "id": "<photo id>", "mime_type": "image/jpeg" }],
    "photo_warning": "<Depop only, when its photo limit leaves some off>"
  }
}
```

`payload` is the Mac's `marketplace.execute` payload for `save_draft`, minus
video and the server's Storage keys. `expires_at` (epoch seconds) is when the
Bridge stops accepting the command; the token works for photo reads and the
result until it is used or 15 minutes pass.

Chrome reports with `POST /api/operations/{operation_id}/result`,
`Authorization: Bearer <token>`, body `{"result": {...}}` (64 KB at most). The
result is the native contract every marketplace runner returns:

| Field | Meaning |
|---|---|
| `ok` | `true` when the draft was saved and read back |
| `state` | `draft` on success; anything else records **Unconfirmed** |
| `draft_url` | the marketplace's private draft link (checked per marketplace) |
| `draft_replaced` | Depop: the saved draft replaced the one linked before |
| `error`, `error_code`, `fields`, `marketplace_message`, `retry_after_s` | the refusal, as on the Mac |
| `safe_to_retry` | `true` when nothing was saved, so the item is ready to save again |
| `warning` | a note that does not change the outcome |

The response is the updated record; the token stops working. `GET
/api/operations/{operation_id}/photos/{photo_id}` with the same token returns one
approved photo's bytes.

