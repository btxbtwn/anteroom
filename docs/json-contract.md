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


## Commands relayed to Chrome from the web

On the web, the seller's Anteroom tab carries a few fixed commands from the
hosted API to Anteroom Bridge (`services/hosted_browser_relay.py`,
`vendoo-extension/cloud-marketplace-protocol.js`). Each one is exactly
`{type, job_id, message_id, payload}`, with a 32-hex `message_id` that
`payload.request_id` repeats. The Bridge rejects anything else.

| Command | Bridge capability | `job_id` | Payload besides `request_id` | Reply |
|---|---|---|---|---|
| `marketplace.execute` | `marketplace.actions` | the job | the approved action, as on the Mac | the action's result |
| `marketplace.result` | `marketplace.actions` | the job | `operation_id`, `context` | what the action did |
| `marketplace.delete` | `marketplace.actions` | the job | the confirmed delist | the delist result |
| `job.cancel` | `marketplace.actions` | the operation | `job_id` | none awaited |
| `marketplace.vinted_categories` | `marketplace.reads` | `null` | none | `{ok, options}` or `{ok: false, error_code}` |
| `marketplace.vinted_brands` | `marketplace.reads` | `null` | `query` (≤100), `category` (≤1000) | `{ok, options}` or `{ok: false, error_code}` |

A read's `error_code` is one of `signed_out`, `challenge`, `rate_limited`,
`schema_drift` or `network`; the marketplace's own words stay in Chrome.
