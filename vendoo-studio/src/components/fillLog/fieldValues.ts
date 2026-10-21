import { normalizeLookupKey } from "./fieldKeys";
import type { DraftField } from "./formModel";

export const EMPTY_CELL = "— empty —";
export const UNREAD_CELL = "— not read —";

const DOES_NOT_APPLY_RE = /^(d|n\/?a|n\.a\.?|does not apply|none|unknown|-+)$/i;

export function listingTextForField(
  listing: Record<string, unknown> | undefined,
  marketplace: string,
  field: DraftField,
): string {
  return listingValueForField(listing, marketplace, field);
}

export function listingFieldEmpty(
  listing: Record<string, unknown> | undefined,
  marketplace: string,
  field: DraftField,
): boolean {
  return !listingTextForField(listing, marketplace, field);
}

export const SKIP_KEYS = new Set([
  "itemid",
  "userid",
  "datecreated",
  "datelastmodified",
  "datelisted",
  "lastdatelisted",
  "laststatusdate",
  "listings",
  "images",
  "videos",
  "id",
  "errors",
  "error",
  "extras",
  "siteid",
  "enabled",
  "listed",
  "listedid",
  "listingid",
  "listingurl",
  "listingattemptmessages",
  "marketplaceaccount",
  "marketplaceid",
  "sales",
  "status",
  "origin",
  "version",
  "validate",
  "hash",
  "draftid",
  "lastsynced",
  "lastmodified",
  "statuses",
]);

const GENERAL_LISTING_KEYS: Record<string, string> = {
  title: "title",
  description: "description",
  price: "price",
  "listing price": "price",
  cost: "cost",
  "cost of goods": "cost",
  quantity: "quantity",
  brand: "brand",
  condition: "condition",
  "primary color": "primaryColor",
  color: "primaryColor",
  "secondary color": "secondaryColor",
  size: "size",
  "us size": "size",
  sku: "sku",
  category: "category_path",
  "zip code": "zipCode",
  "size type": "sizeType",
  pounds: "weight_lb",
  "package weight lb": "weight_lb",
  ounces: "weight_oz",
  "package weight oz": "weight_oz",
  tags: "tags",
  labels: "labels",
  "vendoo labels": "labels",
  notes: "notes",
  "internal notes": "notes",
  "vendoo internal notes": "notes",
};

function lookupToJsonKey(key: string): string {
  const mapped: Record<string, string> = {
    "when made": "when_made",
    "who made": "who_made",
    "what is it": "what_is",
    "size type": "sizeType",
    "country of origin": "countryOfOrigin",
    "year manufactured": "yearManufactured",
  };
  if (mapped[key]) return mapped[key];
  const parts = key.split(" ").filter(Boolean);
  if (!parts.length) return "";
  return parts[0] + parts.slice(1).map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join("");
}

function isBlankListingValue(value: unknown): boolean {
  if (value == null) return true;
  if (typeof value === "string") return value.trim() === "";
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === "object") return Object.keys(value as object).length === 0;
  return false;
}

function valueFromRecord(record: Record<string, unknown> | undefined, key: string): unknown {
  if (!record || !key) return undefined;
  const jsonKey = lookupToJsonKey(key);
  const mapped = GENERAL_LISTING_KEYS[key];
  const colorKeys = new Set(["color", "primary color"]);
  for (const [candidate, value] of Object.entries(record)) {
    const candidateKey = normalizeLookupKey(candidate);
    const matched =
      candidateKey === key ||
      (colorKeys.has(key) && colorKeys.has(candidateKey)) ||
      candidate === jsonKey ||
      (mapped && candidate === mapped);
    if (!matched || isBlankListingValue(value)) continue;
    return value;
  }
  if (jsonKey && jsonKey in record && !isBlankListingValue(record[jsonKey])) return record[jsonKey];
  if (mapped && mapped in record && !isBlankListingValue(record[mapped])) return record[mapped];
  const nestedCategory = record.category_specifics;
  if (nestedCategory && typeof nestedCategory === "object" && !Array.isArray(nestedCategory) && nestedCategory !== record) {
    const found = valueFromRecord(nestedCategory as Record<string, unknown>, key);
    if (found != null) return found;
  }
  const nestedMarket = record.marketplaceSpecifics || record.marketplace_specifics;
  if (nestedMarket && typeof nestedMarket === "object" && !Array.isArray(nestedMarket) && nestedMarket !== record) {
    return valueFromRecord(nestedMarket as Record<string, unknown>, key);
  }
  return undefined;
}

export function listingValueForField(
  listing: Record<string, unknown> | undefined,
  marketplace: string,
  field: DraftField,
  includeNotApplicable = false,
): string {
  if (!listing) return "";
  const key = normalizeLookupKey(field.label || field.key);
  let raw: unknown;
  if (["length", "width", "height"].includes(key)) {
    const dimensions = String(listing.package_dimensions_in || "").split(/\s*[x×]\s*/);
    raw = dimensions.length === 3
      ? dimensions[{ length: 0, width: 1, height: 2 }[key as "length" | "width" | "height"]]
      : undefined;
  } else if (marketplace === "general") {
    raw = valueFromRecord(listing, key);
  } else {
    const specifics = listing[`${marketplace}_specifics`];
    const record = specifics && typeof specifics === "object" && !Array.isArray(specifics)
      ? specifics as Record<string, unknown>
      : {};
    raw = valueFromRecord(record, key);
    if (raw == null && key === "when made") {
      const ebay = listing.ebay_specifics;
      raw = valueFromRecord(
        ebay && typeof ebay === "object" && !Array.isArray(ebay) ? ebay as Record<string, unknown> : {},
        "year manufactured",
      );
    }
    if (raw == null) raw = valueFromRecord(listing, key);
  }
  if (raw == null) return "";
  if (Array.isArray(raw)) return raw.map(String).filter(Boolean).join(", ").trim();
  const text = String(raw).trim();
  if (!includeNotApplicable && isDoesNotApplyValue(text)) return "";
  return text;
}

export function isDoesNotApplyValue(value: unknown): boolean {
  if (value == null) return false;
  if (Array.isArray(value)) {
    if (value.length === 0) return false;
    return value.every((item) => isDoesNotApplyValue(item));
  }
  return DOES_NOT_APPLY_RE.test(String(value).trim());
}

export function notApplicableField(field: DraftField, value = "Does not apply"): DraftField {
  return {
    ...field,
    value,
    missing: false,
    notApplicable: true,
    leftover: undefined,
  };
}

export function fieldLabel(key: string): string {
  return key
    .replace(/^[0-9a-f]{8,}_/i, "")
    .replace(/^\d+_/, "")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/[_.-]+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase())
    .trim();
}

export function isEmptyValue(value: unknown): boolean {
  if (value == null) return true;
  if (typeof value === "boolean") return false;
  if (typeof value === "number") return false;
  if (typeof value === "string") return value.trim() === "";
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    if (typeof record.displayName === "string") return record.displayName.trim() === "";
    if (Array.isArray(record.displayPath)) return record.displayPath.length === 0;
    if ("value" in record) return isEmptyValue(record.value);
    if (record.option) return isEmptyValue(record.option);
    return Object.values(record).every(isEmptyValue);
  }
  return true;
}

/** MUI empty selects often render the field label itself as the visible text. */
function isPlaceholderFieldValue(value: unknown, label?: string): boolean {
  if (isEmptyValue(value)) return true;
  if (typeof value !== "string") return false;
  const shown = value
    .trim()
    .replace(/[*?]+$/g, "")
    .replace(/\s+/g, " ")
    .trim();
  if (!shown) return true;
  const lower = shown.toLowerCase();
  if (/^(select|choose|pick|----)\b/.test(lower)) return true;
  if (label) {
    const labelNorm = label
      .trim()
      .replace(/[*?]+$/g, "")
      .replace(/\s+/g, " ")
      .trim()
      .toLowerCase();
    if (labelNorm && lower === labelNorm) return true;
  }
  return /^(department|type|size|size type|condition|brand|primary color|secondary color|shipping label|category)$/i.test(
    lower,
  );
}

export function fieldDisplayValue(value: unknown, label?: string): string {
  if (isPlaceholderFieldValue(value, label)) return "";
  return displayValue(value);
}

export function fieldIsMissing(value: unknown, label?: string): boolean {
  return isPlaceholderFieldValue(value, label);
}

export function displayValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return String(value);
  if (typeof value === "string") return value.trim();
  if (Array.isArray(value)) {
    if (value.length === 0) return "";
    if (value.every((item) => typeof item === "string" || typeof item === "number")) return value.join(", ");
    if (value.some((item) => item && typeof item === "object" && "url" in (item as object))) {
      return `${value.length} photo${value.length === 1 ? "" : "s"}`;
    }
    return `${value.length} items`;
  }
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    if (typeof record.displayName === "string" && record.displayName.trim()) return record.displayName.trim();
    if (Array.isArray(record.displayPath) && record.displayPath.length) return record.displayPath.map(String).join(" > ");
    if (record.option && !isEmptyValue(record.option)) return displayValue(record.option);
    if (record.scale && record.option) {
      const size = displayValue(record.option);
      const scale = displayValue(record.scale);
      return [size, scale].filter(Boolean).join(" · ");
    }
    if ("value" in record) return displayValue(record.value);
    const pounds = record.pounds ?? record.lb;
    const ounces = record.ounces ?? record.oz;
    if (pounds != null || ounces != null) {
      return [pounds != null ? `${pounds} lb` : "", ounces != null ? `${ounces} oz` : ""].filter(Boolean).join(" ");
    }
    const length = record.length;
    const width = record.width;
    const height = record.height;
    if (length != null || width != null || height != null) {
      return [length, width, height].filter((part) => part != null && String(part) !== "").join(" × ");
    }
    const nested = Object.entries(record)
      .filter(([key]) => !SKIP_KEYS.has(key.toLowerCase()))
      .map(([key, nestedValue]) => {
        const shown = displayValue(nestedValue);
        return shown ? `${fieldLabel(key)} ${shown}` : "";
      })
      .filter(Boolean);
    return nested.join(" · ");
  }
  return String(value);
}
