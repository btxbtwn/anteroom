// Reduces a webhook to the IDs Studio needs to look the change up itself.
// Nothing else is kept: no buyer names, addresses, usernames, prices or text.

const EBAY_ID_KEYS = new Set([
  "orderId",
  "legacyOrderId",
  "lineItemId",
  "legacyItemId",
  "itemId",
  "listingId",
  "offerId",
  "sku",
  "transactionId",
  "userId",
]);

const ID_VALUE = /^[A-Za-z0-9._:-]{1,128}$/;

function collectIds(value, keys, found = {}, depth = 0) {
  if (depth > 6 || value === null || typeof value !== "object") return found;
  for (const [key, child] of Object.entries(value)) {
    if (keys.has(key) && (typeof child === "string" || typeof child === "number")) {
      const text = String(child);
      if (ID_VALUE.test(text)) {
        found[key] ??= [];
        if (!found[key].includes(text) && found[key].length < 50) found[key].push(text);
      }
    } else if (typeof child === "object") {
      collectIds(child, keys, found, depth + 1);
    }
  }
  return found;
}

function cleanString(value, max = 128) {
  return typeof value === "string" && ID_VALUE.test(value) && value.length <= max ? value : null;
}

export function extractEbayEvent(payload) {
  const topic = cleanString(payload?.metadata?.topic);
  const notificationId = cleanString(payload?.notification?.notificationId);
  if (!topic || !notificationId) return null;
  return {
    source: "ebay",
    eventType: topic,
    dedupeKey: `ebay:${notificationId}`,
    ids: collectIds(payload.notification.data ?? {}, EBAY_ID_KEYS),
    occurredAt: cleanString(payload.notification.eventDate, 40),
  };
}

const ETSY_ID_KEYS = ["shop_id", "listing_id", "product_id", "receipt_id"];

export function extractEtsyEvent(payload, webhookId) {
  const eventType = typeof payload?.event_type === "string" && /^[a-z_]+\.[a-z_]+$/.test(payload.event_type)
    ? payload.event_type
    : null;
  const id = cleanString(webhookId);
  if (!eventType || !id) return null;
  const ids = {};
  for (const key of ETSY_ID_KEYS) {
    const value = payload[key];
    if ((typeof value === "string" || typeof value === "number") && ID_VALUE.test(String(value))) {
      ids[key] = [String(value)];
    }
  }
  // Order events carry only resource_url; its path holds the receipt ID.
  if (typeof payload.resource_url === "string") {
    const match = payload.resource_url.match(/\/receipts\/(\d+)/);
    if (match) ids.receipt_id ??= [match[1]];
  }
  return { source: "etsy", eventType, dedupeKey: `etsy:${id}`, ids, occurredAt: null };
}
