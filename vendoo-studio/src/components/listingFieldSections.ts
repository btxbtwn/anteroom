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
  /** The label is read by screen readers only. */
  labelHidden?: boolean;
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

/**
 * The General tab's own order: the order a seller fills it in by hand, as
 * eBay's and Shopify's listing forms do. Category comes first because it is
 * the one required field and decides each marketplace's form. Then what a
 * buyer scans first (title, condition, size, brand), the price, the
 * description, the remaining item details and shipping. Cost, SKU and
 * quantity are the seller's own records, so they wait under More details,
 * folded until the seller opens it.
 */
const GENERAL_SECTIONS: { id: string; title: string; keys: string[] }[] = [
  { id: "item", title: "Item", keys: ["category_path", "title"] },
  { id: "details", title: "Condition & details", keys: ["condition", "size", "brand"] },
  { id: "pricing", title: "Pricing", keys: ["price"] },
  { id: "description", title: "Description", keys: ["description"] },
  { id: "appearance", title: "Color & department", keys: ["primaryColor", "secondaryColor", "department"] },
  { id: "shipping", title: "Shipping", keys: ["weight_oz", "package_dimensions_in", "zipCode"] },
  { id: "other", title: "More details", keys: ["cost", "sku", "quantity"] },
];

/** The General section that starts folded. */
export const GENERAL_FOLDED_SECTION = "other";

export function generalFormSections<T extends SectionField>(fields: T[]): FieldSection<T>[] {
  const byKey = new Map(fields.map((field) => [field.key, field]));
  const placed = new Set<string>();
  const sections = GENERAL_SECTIONS.flatMap((section) => {
    const rows = section.keys.flatMap((key) => {
      const field = byKey.get(key);
      if (!field) return [];
      placed.add(key);
      return [field];
    });
    if (!rows.length) return [];
    // A lone field the heading already names keeps its label for screen readers only.
    const named = rows.length === 1 && rows[0]!.label === section.title;
    return [{ id: section.id, title: section.title, fields: named ? [{ ...rows[0]!, labelHidden: true }] : rows }];
  });
  // A field the order does not name joins More details, after its own rows.
  const rest = fields.filter((field) => !placed.has(field.key));
  if (!rest.length) return sections;
  const more = sections.find((section) => section.id === OTHER.id);
  return more ? sections.map((section) => section === more ? { ...more, fields: [...more.fields, ...rest] } : section)
    : [...sections, { ...OTHER, fields: rest }];
}
