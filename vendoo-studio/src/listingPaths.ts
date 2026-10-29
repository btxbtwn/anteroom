import type { ListingData } from "./api/types";

export function getNestedValue(obj: unknown, path: string): unknown {
  return path.split(".").reduce<unknown>(
    (o, k) => (o && typeof o === "object" ? (o as Record<string, unknown>)[k] : undefined),
    obj,
  );
}

export function cloneListing(listing: ListingData | undefined): ListingData {
  try {
    return JSON.parse(JSON.stringify(listing || {}));
  } catch {
    return { ...listing };
  }
}

export function setNestedValue(obj: ListingData, path: string, value: unknown): ListingData {
  const keys = path.split(".");
  const last = keys.pop()!;
  let target: Record<string, unknown> = obj;
  for (const k of keys) {
    if (!target[k] || typeof target[k] !== "object") target[k] = {};
    target = target[k] as Record<string, unknown>;
  }
  target[last] = value;
  return obj;
}

/** Case- and separator-free form used to spot variants of the same JSON key. */
export function squashFieldKey(value: string): string {
  return String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
}

/**
 * Map a Vendoo form label ("Sleeve Length") onto the listing JSON key ("sleeveLength").
 * Mirrors server ``label_to_json_key``.
 */
export function labelToJsonKey(fieldLabel: string): string {
  // Native form paths already name JSON keys; don't turn sizeSetId into sizesetid.
  if (/^[a-z][A-Za-z0-9]*$/.test(fieldLabel)) return fieldLabel;
  const label = String(fieldLabel || "")
    .toLowerCase()
    .replace(/[_/]+/g, " ")
    .replace(/-/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!label) return "";
  const known: Record<string, string> = {
    "size type": "sizeType",
    "sleeve length": "sleeveLength",
    "sleeve type": "sleeveType",
    "country of origin": "countryOfOrigin",
    "fabric type": "fabricType",
    "fabric weight": "fabricWeight",
    "garment care": "garmentCare",
    "unit quantity": "unitQuantity",
    "unit type": "unitType",
    "character family": "characterFamily",
    "performance activity": "performanceActivity",
    "year manufactured": "yearManufactured",
    "collar style": "collarStyle",
    "strap type": "strapType",
    "clothing style": "clothingStyle",
  };
  if (known[label]) return known[label];
  const parts = label.split(" ").filter(Boolean);
  if (!parts.length) return "";
  return parts[0] + parts.slice(1).map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join("");
}

/** Resolve which key inside ``*_specifics`` holds a schema field's value. */
export function resolveSpecificsKey(
  specifics: Record<string, unknown> | undefined,
  schemaKey: string,
): string {
  const want = squashFieldKey(schemaKey);
  if (!want) return schemaKey;
  if (specifics && typeof specifics === "object") {
    for (const alias of Object.keys(specifics)) {
      if (squashFieldKey(alias) === want) return alias;
    }
  }
  return labelToJsonKey(schemaKey) || schemaKey;
}

/** Text for one form control. A group of values is not text, so it never becomes "[object Object]". */
export function formFieldText(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value.trim() === "[object Object]" ? "" : value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(formFieldText).filter((part) => part.trim()).join(", ");
  if (typeof value === "object") return "";
  const text = String(value);
  return text === "[object Object]" ? "" : text;
}

export function editorValuePresent(value: unknown): boolean {
  if (value == null) return false;
  if (typeof value === "string") return value.trim() !== "" && value.trim() !== "[object Object]";
  if (typeof value === "number" || typeof value === "boolean") return true;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.keys(value).length > 0;
  return formFieldText(value).trim() !== "";
}

/** Preserve arrays and comma-containing option labels; split only older text lists. */
export function formFieldValues(value: unknown, options: readonly string[] = []): string[] {
  if (Array.isArray(value)) return [...new Set(value.map(formFieldText).map((v) => v.trim()).filter(Boolean))];
  const text = formFieldText(value).trim();
  if (!text) return [];
  if (options.includes(text)) return [text];
  return [...new Set(text.split(",").map((v) => v.trim()).filter(Boolean))];
}

/**
 * A multi-value saved as one comma-joined string ("Casual, Minimalist, Indie")
 * becomes separate choices when each piece is itself a choice, or when no
 * choice list is loaded yet. An array element that is one real choice stays one.
 */
export function expandJoinedChoices(value: unknown, options: readonly string[] = []): string[] {
  const values = formFieldValues(value, options);
  if (values.length !== 1 || !values[0].includes(",")) return values;
  const parts = [...new Set(values[0].split(",").map((part) => part.trim()).filter(Boolean))];
  if (parts.length < 2) return values;
  if (!options.length || parts.every((part) => options.includes(part))) return parts;
  return values;
}

/** Keep a field the seller just cleared when a refresh reloads the old server value. */
export function nextFieldDraft(
  server: Record<string, string | string[]>,
  dirty: Record<string, string | string[]>,
): { local: Record<string, string | string[]>; dirty: Record<string, string | string[]> } {
  const local = { ...server };
  const kept: Record<string, string | string[]> = {};
  for (const [key, value] of Object.entries(dirty)) {
    if (formFieldText(server[key]) === formFieldText(value)) continue;
    local[key] = value;
    kept[key] = value;
  }
  return { local, dirty: kept };
}

/** True when this marketplace form stores its own field value, even if it is blank. */
export function ownedSpecificsValue(
  listing: ListingData | undefined,
  path: string,
): { owned: boolean; value: unknown } {
  const parts = path.split(".");
  // Nested native fields (offer prices, shipping, scheduling) are seller-owned
  // too. Their saved value must win over the form cached when the tab opened.
  if (parts.length > 2 && parts[0].endsWith("_specifics")) {
    const value = getNestedValue(listing, path);
    return { owned: value !== undefined, value };
  }
  if (parts.length !== 2 || !parts[0].endsWith("_specifics")) return { owned: false, value: undefined };
  const bucket = listing?.[parts[0]];
  if (!bucket || typeof bucket !== "object" || Array.isArray(bucket)) return { owned: false, value: undefined };
  const record = bucket as Record<string, unknown>;
  const want = squashFieldKey(parts[1]);
  const key = Object.keys(record).find((name) => squashFieldKey(name) === want);
  if (!key) return { owned: false, value: undefined };
  return { owned: true, value: record[key] };
}

const FOLLOWED_GENERAL_KEYS: Record<string, string> = {
  title: "title",
  description: "description",
  brand: "brand",
  condition: "condition",
  primarycolor: "primaryColor",
  secondarycolor: "secondaryColor",
  quantity: "quantity",
  size: "size",
  department: "department",
  price: "price",
  poshmarkprice: "price",
};

/** The General value a marketplace field shows when that form has not edited it. */
export function followedGeneralValue(listing: ListingData | undefined, path: string): unknown {
  const parts = path.split(".");
  if (!listing || parts.length !== 2 || !parts[0].endsWith("_specifics")) return undefined;
  const general = FOLLOWED_GENERAL_KEYS[squashFieldKey(parts[1])];
  return general ? listing[general] : undefined;
}

// Title, description, brand and department are copies of General. Size and
// condition keep the marketplace form's own spelling when it supplied one.
const PLAIN_GENERAL_FILLS = new Set(["title", "description", "brand", "department"]);

/** What a marketplace row shows: its own edit, otherwise the fill it still follows. */
export function marketplaceDisplayValue(
  listing: ListingData | undefined,
  path: string,
  options: { tab: string; locked?: boolean; native?: boolean; defaultValue?: unknown },
): unknown {
  const owned = ownedSpecificsValue(listing, path);
  if (owned.owned) return owned.value;
  // A locked row is the server's value (variation quantity, SKU), not a live copy of General.
  if (options.locked && options.defaultValue !== undefined) return options.defaultValue;
  const leaf = squashFieldKey(path.split(".").pop() || path);
  if (options.tab !== "general" && PLAIN_GENERAL_FILLS.has(leaf)) {
    const general = followedGeneralValue(listing, path);
    if (general !== undefined) return general;
  }
  if ((options.native || options.locked) && options.defaultValue !== undefined) return options.defaultValue;
  const direct = getListingEditorValue(listing, path);
  if (formFieldText(direct) !== "" || typeof direct === "boolean" || (Array.isArray(direct) && direct.length > 0)) return direct;
  if (options.tab !== "general") {
    const general = followedGeneralValue(listing, path);
    if (general != null && formFieldText(general) !== "") return general;
  }
  return options.defaultValue;
}

/** Read a Forms-tab field, matching schema labels to camelCase listing keys. */
export function getListingEditorValue(listing: ListingData | undefined, path: string): unknown {
  const direct = getNestedValue(listing, path);
  if (editorValuePresent(direct)) return direct;
  const parts = path.split(".");
  if (parts.length === 2 && parts[0].endsWith("_specifics")) {
    const specifics = listing?.[parts[0]];
    if (specifics && typeof specifics === "object" && !Array.isArray(specifics)) {
      const key = resolveSpecificsKey(specifics as Record<string, unknown>, parts[1]);
      return (specifics as Record<string, unknown>)[key];
    }
  }
  return direct;
}

/** Write a Forms-tab field onto the matching listing JSON key. */
export function setListingEditorValue(
  listing: ListingData,
  path: string,
  value: unknown,
): ListingData {
  const parts = path.split(".");
  if (parts.length === 2 && parts[0].endsWith("_specifics")) {
    const bucket = parts[0];
    const current = (listing[bucket] && typeof listing[bucket] === "object" && !Array.isArray(listing[bucket]))
      ? { ...(listing[bucket] as Record<string, unknown>) }
      : {};
    const key = /^[a-z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*$/.test(parts[1]) ? parts[1] : resolveSpecificsKey(current, parts[1]);
    // Drop schema-label duplicates so we don't keep both Accents and accents.
    const want = squashFieldKey(parts[1]);
    for (const alias of Object.keys(current)) {
      if (alias !== key && squashFieldKey(alias) === want) delete current[alias];
    }
    current[key] = value;
    listing[bucket] = current;
    return listing;
  }
  return setNestedValue(listing, path, value);
}
