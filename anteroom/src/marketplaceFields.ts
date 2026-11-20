import editorFields from "../../skills/list-this/references/listing-editor-fields.json";

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

export const ETSY_CATEGORY_OPTIONALS: { key: string; label: string }[] = editorFields.etsy_optionals;

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

/** Depop's Size Grouping is for petite, plus, tall and maternity sizing only. */
export function depopSizeGroupingApplies(listing: Record<string, unknown> | undefined): boolean {
  const depop = listing?.depop_specifics;
  const grouping = depop && typeof depop === "object" ? String((depop as Record<string, unknown>).sizeGrouping || "").trim() : "";
  if (grouping && !/^(?:does not apply|n\/?a|none)$/i.test(grouping)) return true;
  return /petite|plus|tall|maternity/i.test(String(listing?.sizeType || ""));
}
