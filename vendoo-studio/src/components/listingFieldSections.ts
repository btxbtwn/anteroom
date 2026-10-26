/**
 * Groups listing form fields into titled sections so a long form (eBay's
 * category attributes run past thirty) reads as a few topics instead of one
 * undifferentiated grid. Fields are matched on their label and key, since
 * Vendoo's schema labels ("Sleeve Length", "COLOR", "attribute_body-fit") and
 * the listing JSON keys ("primaryColor", "weight_oz") both have to land in the
 * same place.
 */

export interface SectionField {
  key: string;
  label: string;
  required?: boolean;
}

export interface FieldSection<T extends SectionField> {
  id: string;
  title: string;
  fields: T[];
}

interface SectionRule {
  id: string;
  title: string;
  /** Checked in order; a field's position follows the first pattern it matches. */
  patterns: RegExp[];
}

// Matching order: the first section whose patterns hit claims the field, so
// the narrow topics come before the broad ones ("Fabric Weight" is a material,
// not shipping; "Size Type" is sizing and "Unit Type" inventory, not a
// generic item type).
const RULES: SectionRule[] = [
  { id: "listing", title: "Listing", patterns: [/^title$/, /^description$/] },
  {
    id: "pricing",
    title: "Pricing",
    patterns: [/^price$/, /^originalprice$/, /^cost$/, /^pricingformat$/, /^smartpricing$/, /^minimumprice$/, /offer/],
  },
  {
    id: "condition",
    title: "Condition",
    patterns: [/^condition$/, /^conditiondescription$/, /condition/],
  },
  {
    id: "size",
    title: "Size & fit",
    patterns: [/^sizegroup$/, /^size$/, /^sizetype$/, /size/, /fit$/, /^inseam$/, /^rise$/, /length$/, /^silhouette$/, /^legstyle$/, /measurement/],
  },
  {
    id: "appearance",
    title: "Color & material",
    patterns: [/colou?r/, /^pattern$/, /material/, /fabric/, /wash$/, /^accents$/, /^gemstone$/],
  },
  {
    id: "inventory",
    title: "Inventory & identifiers",
    patterns: [/^quantity$/, /^sku$/, /^(upc|mpn|ean|isbn|gtin)$/, /storecategory/, /^unit(type|quantity)$/],
  },
  {
    id: "item",
    title: "Item",
    patterns: [
      /^category(path)?$/,
      /^subcategory$/,
      /^(brand|designer)$/,
      /^model$/,
      /^productline$/,
      /^(department|gender)$/,
      /^unisex/,
      /^(whomade|whatisit|whenmade)$/,
      /^type$/,
      /type$/,
      /^style$/,
      /style$/,
      /^(vintage|handmade)$/,
      /^countryoforigin$/,
    ],
  },
  {
    id: "shipping",
    title: "Shipping",
    patterns: [/shipping/, /weight/, /dimension/, /package/, /^zipcode$/, /inventorylocation/],
  },
];

/** Display order, which differs from matching order. */
const DISPLAY_ORDER = ["listing", "pricing", "condition", "item", "size", "appearance", "shipping", "inventory", "other"];

const OTHER = { id: "other", title: "More details" };

function normalize(text: string): string {
  return text
    .toLowerCase()
    .replace(/\s*\*$/, "")
    .replace(/^attribute_/, "")
    .replace(/[^a-z0-9]/g, "");
}

function names(field: SectionField): string[] {
  const leaf = field.key.split(".").pop() || field.key;
  return [normalize(field.label), normalize(leaf)].filter(Boolean);
}

function classify(field: SectionField): { id: string; rank: number } {
  const candidates = names(field);
  if (candidates.includes("parcelsize")) return { id: "shipping", rank: 0 };
  for (const rule of RULES) {
    const rank = rule.patterns.findIndex((pattern) => candidates.some((name) => pattern.test(name)));
    if (rank >= 0) return { id: rule.id, rank };
  }
  return { id: OTHER.id, rank: 0 };
}

export function groupFieldsIntoSections<T extends SectionField>(fields: T[]): FieldSection<T>[] {
  const buckets = new Map<string, { field: T; rank: number; index: number }[]>();
  fields.forEach((field, index) => {
    const { id, rank } = classify(field);
    const bucket = buckets.get(id) || [];
    bucket.push({ field, rank, index });
    buckets.set(id, bucket);
  });
  const titles = new Map([...RULES.map((rule) => [rule.id, rule.title] as const), [OTHER.id, OTHER.title]]);
  return DISPLAY_ORDER.flatMap((id) => {
    const bucket = buckets.get(id);
    if (!bucket?.length) return [];
    // Required fields lead, then the section's own order, then schema order.
    const sorted = [...bucket].sort(
      (a, b) =>
        Number(Boolean(b.field.required)) - Number(Boolean(a.field.required))
        || a.rank - b.rank
        || a.index - b.index,
    );
    return [{ id, title: titles.get(id) || id, fields: sorted.map((entry) => entry.field) }];
  });
}
