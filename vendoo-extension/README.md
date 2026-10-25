# Anteroom Bridge

The extension runs in the background and connects Chrome to Anteroom. Listing review, approved Sends, progress, and diagnostics live in Anteroom. The extension has no popup, on-page panel, or field highlighting.

## Installation

1. Use **Connect Chrome** in Anteroom to open everyday Chrome.
2. Open `chrome://extensions/` and enable **Developer mode**.
3. Click **Load unpacked** and select the repo's `vendoo-extension/` folder. Zip users select `~/Library/Application Support/Anteroom/vendoo-extension`.
4. Keep Anteroom running. The extension pairs automatically with Anteroom on `127.0.0.1:4318`.

## Usage

1. Create or import a listing in Anteroom and review it there.
2. Approve **Send to Vendoo** in Anteroom to start a draft job.
3. Follow the browser preview and fill results in Anteroom. Automation stops at a saved draft.

Chrome still shows the extension in its extensions menu for browser-managed permissions and removal. Clicking its icon does not open an extension interface.

After updating the extension, reload it in `chrome://extensions/` and refresh open marketplace tabs.

## Companion Skills

`skills/list-this/SKILL.md` is the canonical source for listing rules and the JSON payload shared by Anteroom and the extension. Its references live in top-level `skills/list-this/references/`.

## JSON Format

```json
{
  "title": "Vintage Denim Jacket",
  "description": "Classic Levi's denim jacket from the 90s. Great condition with minimal wear.",
  "price": 45.00,
  "brand": "Levi's",
  "size": "M",
  "condition": "Good",
  "category": "Clothing > Jackets",
  "color": "Blue",
  "sku": "DENIM-001",
  "originalPrice": 0,
  "quantity": 1,
  "tags": ["vintage", "denim", "levis", "90s"],
  "images": ["https://example.com/img1.jpg", "https://example.com/img2.jpg"]
}
```

### Supported Fields

| Field | Type | Description |
|-------|------|-------------|
| title | string | **Required** - Listing title |
| description | string | Item description |
| price | number | Listing price |
| brand | string | Brand name |
| size | string | Size (S/M/L/XL or numeric) |
| condition | string | new, like new, good, fair, poor |
| category | string | Category path |
| color | string | Primary color |
| sku | string | Your SKU/inventory ID |
| originalPrice | number | Poshmark original price; always 0 |
| quantity | number | Available quantity |
| tags | array | Keywords/tags (Etsy) |
| images | array | Image URLs (not yet implemented) |

## Platform Notes

### Vendoo (Primary)
- Fills most fields directly
- Category selection may need manual completion
- Images not yet implemented

### eBay
- Multi-step wizard flow
- Category requires tree navigation
- Rich text description

### Poshmark
- Brand autocomplete - may need manual selection
- Size buttons need clicking
- NWT checkbox for new items

### Mercari
- Modal-based category picker
- Condition dropdown
- Shipping options need manual selection

### Depop
- No separate title field (uses description)
- Color chips need clicking
- UK-focused sizing

### Etsy
- "Who made it" / "When made" dropdowns
- Tags are important for search
- Multiple listing types

## Permissions

Every permission in `manifest.json` has a caller. Remove a permission when its last caller goes away.

| Permission | Why |
|---|---|
| `alarms` | Wakes the worker to reconnect to Anteroom. |
| `storage` | Keeps the Anteroom pairing token, the active job, the preview window id, and diagnostic observations. |
| `scripting` | Injects fill helpers and the diagnostic collector into marketplace tabs. |
| `tabs` | Finds, opens, and focuses the Vendoo tab for a fill job. |
| `debugger` | Streams the live browser preview into Anteroom through the DevTools protocol. |
| `system.display` | Places the automation window inside a real display work area. |

Host permissions cover Vendoo, eBay, Poshmark, Mercari, Depop, and Anteroom on `127.0.0.1:4318` only. The extension never opens or fills etsy.com; Etsy listings are filled in Vendoo's Etsy section.

## Development

### Selectors
Each content script has placeholder selectors that need updating based on actual site inspection:

```javascript
const SELECTORS = {
  title: 'input[name="title"]', // Update with real selector
  // ...
};
```

To find real selectors:
1. Open the platform's listing page
2. Right-click element → Inspect
3. Note the actual `id`, `name`, `data-testid`, or unique class
4. Update the selector in the content script

### Testing
Run the worker unit tests from the repo root:

```bash
node --test 'vendoo-extension/tests/*.test.js'
```

They load `background.js` and its `background/` modules into one Node VM with a stubbed `chrome` API.

For live pages:
1. Reload the extension in `chrome://extensions/` and refresh the marketplace page.
2. Approve a draft Send from Anteroom and inspect its browser preview and fill results.
3. Open browser DevTools and filter by `[VENDOO]` or the marketplace prefix for detailed logs.
4. For manual form inspection, paste `diagnostic-tool.js` into the page's DevTools console and use `vendooDiagnostics.exportStructure()`. Anteroom also receives diagnostic observations from the worker.

### Icons
Add icon files:
- `icons/icon16.png` (16x16)
- `icons/icon48.png` (48x48)
- `icons/icon128.png` (128x128)

## TODO

- [ ] Update selectors with real values from each platform
- [ ] Implement image upload handling
- [ ] Auto-detect when user saves draft
- [ ] Category tree navigation
- [ ] Better error recovery
