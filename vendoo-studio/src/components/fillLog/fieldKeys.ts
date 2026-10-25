import { EBAY_CATEGORY_CORE } from "../../marketplaceFields";
import type { DraftField } from "./formModel";

const UNFILLABLE_FIELDS = new Set(["photos", "images", "videos", "image"]);
const SYSTEM_FIELDS = new Set([
  "carrier id",
  "geo lat",
  "geo lng",
  "listing state",
  "price currency",
]);
/** Seller-account rows that never belong in listing JSON or Ask chat. */
const ACCOUNT_SETTING_FIELDS = new Set([
  "allow best offer",
  "auto-accept",
  "auto accept",
  "minimum offer",
  "minimum price",
  "primary store category",
  "secondary store category",
  "store category name",
  "store category2name",
  "store category 2 name",
  "personalization instructions",
  "exclude sku from listing",
  "no brand/not sure",
  "worldwide shipping",
  "custom property",
  "other info",
  "size grouping",
  "accept returns",
  "return within",
  "return refund method",
  "return paid by",
  "return payed by",
  "returns",
  "starting price",
  "payment method",
  "paypal email",
  "smart pricing",
  "smart offers",
  "floor price",
  "set a floor price",
  "status item",
  "zip code",
  "mercari local information",
  "payer id",
]);
/** Only Depop/Mercari set shipping per item; elsewhere it comes from account settings. */
const ITEM_SHIPPING_MARKETPLACES = new Set(["depop", "mercari"]);
// Mirrors is_account_managed_field in vendoo_studio/services/registry.py.
const POLICY_FIELD_RE = /\b(polic(?:y|ies)|returns?|refunds?|handling(?: time)?|processing (?:time|profile)|payment method|ready to ship)\b/;
const SHIPPING_FIELD_RE = /\b(shipping|shipment|ship to|shipped|delivery|parcel|postage|carrier|package (?:type|size)|who pays)\b/;

function isAccountSettingField(field: DraftField | string): boolean {
  const raw = typeof field === "string" ? field : field.label || field.key;
  const key = normalizeLookupKey(raw);
  if (ACCOUNT_SETTING_FIELDS.has(key)) return true;
  // Scraped labels often concatenate the whole control path, e.g.
  // "Pricing Format Details Fixed Price Allow Best Offer".
  return (
    key.includes("best offer") ||
    key.includes("accept offer") ||
    key.includes("decline offer") ||
    key.includes("pricing format details") ||
    key.includes("auto-accept") ||
    key.includes("auto accept") ||
    key.includes("store category") ||
    key.includes("paypal") ||
    key.includes("smart pricing") ||
    key.includes("smart offers") ||
    key.includes("floor price")
  );
}

/** True for shipping/policy rows the seller already set in marketplace settings. */
export function isAccountManagedField(
  marketplace: string | undefined,
  field: DraftField | string,
): boolean {
  const raw = typeof field === "string" ? field : field.label || field.key;
  const key = normalizeLookupKey(raw);
  if (!key) return false;
  if (isAccountSettingField(field)) return true;
  if (POLICY_FIELD_RE.test(key)) return true;
  if (ITEM_SHIPPING_MARKETPLACES.has(String(marketplace || "").trim().toLowerCase())) {
    return false;
  }
  return SHIPPING_FIELD_RE.test(key);
}

export function isUnfillableField(field: DraftField, marketplace?: string): boolean {
  return (
    UNFILLABLE_FIELDS.has(field.label.toLowerCase()) ||
    UNFILLABLE_FIELDS.has(field.key.toLowerCase()) ||
    isSystemField(field) ||
    isAccountManagedField(marketplace, field)
  );
}

export function isSystemField(field: DraftField | string): boolean {
  const raw = typeof field === "string" ? field : field.label || field.key;
  const key = normalizeLookupKey(raw);
  return SYSTEM_FIELDS.has(key) || /^\d{6,}\s/.test(key);
}
export function normalizeLookupKey(value: string): string {
  const aliases: Record<string, string> = {
    "listing price": "price",
    "buy it now price": "price",
    "cost of goods": "cost",
    "us size": "size",
    "vendoo labels": "labels",
    "internal notes": "notes",
    "vendoo internal notes": "notes",
    "primary color": "color",
    "when was it made": "when made",
    "who made it": "who made",
    "starting price": "starting price",
    "inventory location": "zip code",
    "item location": "zip code",
    "shipping location": "zip code",
    "return payed by": "return paid by",
  };
  const key = String(value || "")
    .replace(/^(ebay|etsy|poshmark|mercari|depop|grailed|vinted|facebook|sellwild)\s+/i, "")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_*?-]+/g, " ")
    .replace(/[^\w\s]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
  return aliases[key] || key;
}

const FIELD_NAME_ALIASES: Record<string, string> = {
  "category v2": "category",
  categoryv2: "category",
  "category path": "category",
  categorypath: "category",
  "us size": "size",
  "size option": "size",
  "size option value": "size",
  "size type": "size type",
  "size scale": "size type",
  "size scale value": "size type",
  "listing price": "price",
  "buy it now price": "price",
  "cost of goods": "cost",
  "vendoo labels": "labels",
  "vendoo internal notes": "notes",
  "internal notes": "notes",
  "weight lbs": "pounds",
  "weight (lbs)": "pounds",
  "weight lb": "pounds",
  "weight pounds": "pounds",
  "package weight (lb)": "pounds",
  "package weight lb": "pounds",
  "weight ounces": "ounces",
  "weight (oz)": "ounces",
  "weight oz": "ounces",
  "package weight (oz)": "ounces",
  "package weight oz": "ounces",
  color: "primary color",
  zipcode: "zip code",
  zip: "zip code",
  "inventory location": "zip code",
  "item location": "zip code",
  "shipping location": "zip code",
  "who made it": "who made",
  whomade: "who made",
  "what is": "what is it",
  whatisit: "what is it",
  "when was it made": "when made",
  whenmade: "when made",
  "style tags": "style tags",
  "style tag": "style tags",
  "shipping label": "shipping label",
  "accept return": "accept returns",
  "payment met": "payment method",
  "condition desc": "condition description",
  "size group": "size grouping",
  "body fit": "size grouping",
  "country region of manufacture": "country of origin",
  "fabric pattern": "pattern",
};

export function normalizeFieldName(value: string): string {
  const key = String(value || "")
    .replace(/^(ebay|etsy|poshmark|mercari|depop|grailed|vinted|facebook|sellwild|vendoo)\s+/i, "")
    .replace(/[*?]+/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/[_.-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
  return FIELD_NAME_ALIASES[key] || key;
}

export function isInventoryLocationField(field: DraftField | string): boolean {
  const label = typeof field === "string" ? field : field.label;
  const key = typeof field === "string" ? field : field.key;
  return normalizeLookupKey(label) === "zip code" || normalizeFieldName(label) === "zip code"
    || normalizeLookupKey(key) === "zip code" || normalizeFieldName(key) === "zip code"
    || normalizeFieldName(key.split(".").pop() || key) === "zip code";
}

export function fieldMatchKey(field: DraftField): string {
  if (isInventoryLocationField(field)) return "zip code";
  return normalizeFieldName(field.label) || normalizeFieldName(field.key.split(".").pop() || field.key);
}

/** Cascade fields that must stay visible so optionals can mount on Vendoo. */
const PROTECTED_EBAY_CORE_NAMES = new Set(
  EBAY_CATEGORY_CORE.filter((field) =>
    ["department", "size", "sizeType", "type"].includes(field.key),
  ).map((field) => normalizeFieldName(field.label)),
);

export function isProtectedEbayField(marketplace: string, field: DraftField | string): boolean {
  if (marketplace.toLowerCase() !== "ebay") return false;
  const name =
    typeof field === "string" ? normalizeFieldName(field) : fieldMatchKey(field) || normalizeFieldName(field.label);
  return PROTECTED_EBAY_CORE_NAMES.has(name);
}
