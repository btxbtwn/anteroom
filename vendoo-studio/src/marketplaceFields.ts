/** eBay category optional fields shown after Vendoo's "Show Optional Fields". */
export const EBAY_CATEGORY_OPTIONALS: { key: string; label: string }[] = [
  { key: "accents", label: "Accents" },
  { key: "character", label: "Character" },
  { key: "closure", label: "Closure" },
  { key: "countryOfOrigin", label: "Country of Origin" },
  { key: "fabricType", label: "Fabric Type" },
  { key: "fabricWeight", label: "Fabric Weight" },
  { key: "features", label: "Features" },
  { key: "fit", label: "Fit" },
  { key: "garmentCare", label: "Garment Care" },
  { key: "handmade", label: "Handmade" },
  { key: "mpn", label: "MPN" },
  { key: "material", label: "Material" },
  { key: "neckline", label: "Neckline" },
  { key: "occasion", label: "Occasion" },
  { key: "pattern", label: "Pattern" },
  { key: "personalizationInstructions", label: "Personalization Instructions" },
  { key: "personalize", label: "Personalize" },
  { key: "season", label: "Season" },
  { key: "sleeveLength", label: "Sleeve Length" },
  { key: "sleeveType", label: "Sleeve Type" },
  { key: "strapType", label: "Strap Type" },
  { key: "style", label: "Style" },
  { key: "theme", label: "Theme" },
  { key: "unitQuantity", label: "Unit Quantity" },
  { key: "unitType", label: "Unit Type" },
  { key: "vintage", label: "Vintage" },
  { key: "upc", label: "UPC" },
  { key: "primaryStoreCategory", label: "Primary Store Category" },
  { key: "secondaryStoreCategory", label: "Secondary Store Category" },
];

/** eBay category cascade / required-looking fields above optionals on Vendoo. */
export const EBAY_CATEGORY_CORE: { key: string; label: string }[] = [
  { key: "department", label: "Department" },
  { key: "size", label: "Size" },
  { key: "sizeType", label: "Size Type" },
  { key: "type", label: "Type" },
  { key: "condition", label: "Condition" },
  { key: "conditionDescription", label: "Condition Description" },
];

export const ETSY_CATEGORY_OPTIONALS: { key: string; label: string }[] = [
  { key: "clothingStyle", label: "Clothing style" },
  { key: "sleeveLength", label: "Sleeve length" },
  { key: "neckline", label: "Neckline" },
  { key: "closure", label: "Closure" },
  { key: "graphic", label: "Graphic" },
  { key: "collarStyle", label: "Collar style" },
  { key: "holiday", label: "Holiday" },
  { key: "occasion", label: "Occasion" },
  { key: "pattern", label: "Pattern" },
  { key: "sustainability", label: "Sustainability" },
];

/** Depop fields revealed after Vendoo's "Show Optional Fields". */
export const DEPOP_CATEGORY_OPTIONALS: { key: string; label: string }[] = [
  { key: "source", label: "Source" },
  { key: "age", label: "Age" },
  { key: "style", label: "Style" },
  { key: "occasion", label: "Occasion" },
  { key: "parcelSize", label: "Parcel Size" },
  { key: "sizeGrouping", label: "Size Grouping" },
  { key: "material", label: "Material" },
  { key: "type", label: "Type" },
];

/**
 * The listing key a Depop schema attribute is stored under. Depop's category
 * schema names its attributes (``attribute_jacket-type``); the listing and
 * direct List read ``depop_specifics.type``, ``material`` and so on.
 */
export function depopSchemaKey(key: string): string {
  const attribute = /^attribute_(.+)$/.exec(key)?.[1];
  if (!attribute) return key;
  if (attribute === "body-fit") return "sizeGrouping";
  if (attribute.endsWith("-type")) return "type";
  return attribute;
}

/** Depop's Size Grouping is for petite, plus, tall and maternity sizing only. */
export function depopSizeGroupingApplies(listing: Record<string, unknown> | undefined): boolean {
  const depop = listing?.depop_specifics;
  const grouping = depop && typeof depop === "object" ? String((depop as Record<string, unknown>).sizeGrouping || "").trim() : "";
  if (grouping && !/^(?:does not apply|n\/?a|none)$/i.test(grouping)) return true;
  return /petite|plus|tall|maternity/i.test(String(listing?.sizeType || ""));
}

/** Poshmark's conditions, as its listing form names them. Direct List maps them to nwt / uln / ug / uf. */
export const POSHMARK_CONDITIONS = ["New With Tags (NWT)", "Like New", "Good", "Fair"];

/** Poshmark's catalog colors (catalog_v2, read 2026-10-25), used until the live list loads. */
export const POSHMARK_COLORS = [
  "Black", "Blue", "Brown", "Cream", "Gold", "Gray", "Green", "Orange", "Pink", "Purple", "Red", "Silver", "Yellow", "Tan", "White",
];

/** The Poshmark condition a listing's own condition becomes, as direct List maps it. */
export function poshmarkConditionLabel(condition: unknown): string {
  const text = String(condition ?? "").toLowerCase();
  if (/new with tags|\bnwt\b/.test(text) && !/without/.test(text)) return "New With Tags (NWT)";
  if (/new without tags|\bnwot\b|like new|excellent/.test(text)) return "Like New";
  if (/fair|poor/.test(text)) return "Fair";
  if (/good|imperfection|defect|pre-owned|used/.test(text)) return "Good";
  if (/^new\b|brand new/.test(text)) return "New With Tags (NWT)";
  return "";
}

/** Poshmark's spelling of a listing color, or "" when Poshmark does not list it. */
export function poshmarkColorName(color: unknown): string {
  const text = String(color ?? "").trim().toLowerCase();
  const translated = ({ grey: "gray", beige: "tan" } as Record<string, string>)[text] ?? text;
  return POSHMARK_COLORS.find((name) => name.toLowerCase() === translated) ?? "";
}

/** Mercari's item conditions, as its listing form names them (ids 1-5). */
export const MERCARI_CONDITIONS = ["New", "Like new", "Good", "Fair", "Poor"];

/** The Mercari condition a listing's own condition becomes, as direct List maps it. Worn is never Like new. */
export function mercariConditionLabel(condition: unknown): string {
  const text = String(condition ?? "").trim().toLowerCase();
  if (!text) return "";
  if (/poor|major flaw/.test(text)) return "Poor";
  if (/fair/.test(text)) return "Fair";
  if (/new with tags|\bnwt\b/.test(text) && !/without/.test(text)) return "New";
  if (/new without tags|\bnwot\b|like new/.test(text)) return "Like new";
  if (/good|excellent|imperfection|pre-owned|used/.test(text)) return "Good";
  if (/^new\b/.test(text)) return "New";
  return "";
}
