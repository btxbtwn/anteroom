/** Bottoms size choices each marketplace accepts. Keep in step with services/bottoms_sizes.py. */

export const WOMENS_NUMERIC = ["00", "0", "2", "4", "6", "8", "10", "12", "14", "16", "18", "20", "22", "24"];
const EBAY_WOMENS_SIZES = [...WOMENS_NUMERIC, ...WOMENS_NUMERIC.map((number) => `${number}P`)];

const EBAY_WAISTS = Array.from({ length: 35 }, (_, index) => String(26 + index));
const EBAY_INSEAMS = Array.from({ length: 11 }, (_, index) => String(26 + index));
const EBAY_SIZE_TYPES = ["Regular", "Petites", "Plus", "Tall", "Big & Tall", "Maternity"];

const POSHMARK_WOMEN = [
  "23", "24", "25", "26", "27", "28", "29", "30", "31", "00", "0", "2", "4", "6", "8", "10", "12",
  "32", "33", "34", "14", "14W", "16", "16W", "18", "18W", "20", "20W", "22", "22W", "24", "24W",
  "26", "26W", "28", "28W", "30", "30W", "32W",
  "23P", "24P", "25P", "26P", "27P", "28P", "29P", "30P", "31P", "32P", "33P", "34P",
  "00P", "0P", "2P", "4P", "6P", "8P", "10P", "12P", "14P", "16P", "18P",
  "1", "3", "5", "7", "9", "11", "13", "15", "17",
];
const POSHMARK_MEN = [28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 46, 48, 50, 52, 54, 56, 58, 60]
  .map((waist) => `Waist ${waist}`);
const POSHMARK_SIZE_TYPES = ["Regular", "Petite", "Plus", "Juniors", "Maternity", "Big & Tall"];

const MERCARI_WAISTS = Array.from({ length: 25 }, (_, index) => String(26 + index));
const DEPOP_MEN = [...Array.from({ length: 19 }, (_, index) => String(26 + index)), "Other"];
const ETSY_WAISTS = MERCARI_WAISTS;
const ETSY_COMBINED = ETSY_WAISTS.filter((waist) => Number(waist) >= 28 && Number(waist) <= 44)
  .flatMap((waist) => [28, 30, 32, 34].map((inseam) => `${waist}x${inseam}`));

const VINTED_WOMEN = [
  "XXXS", "XXS / 0", "XS / 2", "XS / 4", "S / 6", "S / 8", "M / 10", "M / 12",
  "L / 14", "L / 16", "XL / 18", "XL / 20", "XXL / 22", "XXL / 24",
];
const VINTED_MEN = [
  ...Array.from({ length: 25 }, (_, index) => String(26 + index)),
  ...Array.from({ length: 25 }, (_, index) => `W${26 + index}`),
];

const BOTTOMS_RE = /\b(jeans|pants|trousers|chinos|slacks|shorts|joggers|sweatpants|cargos|corduroys|leggings|skirts|culottes|capris|khakis|dungarees)\b/i;
const WOMEN_RE = /\b(women|ladies|female|womenswear)\b/i;
const MEN_RE = /\b(men|male|menswear)\b/i;

type ListingLike = Record<string, unknown> | undefined;

function textOf(listing: ListingLike): string {
  if (!listing) return "";
  const ebay = listing.ebay_specifics;
  const ebayText = ebay && typeof ebay === "object" ? Object.values(ebay as Record<string, unknown>) : [];
  const categories = listing.marketplace_categories;
  const categoryText = categories && typeof categories === "object" ? Object.values(categories as Record<string, unknown>) : [];
  return [listing.category_path, listing.title, listing.department, ...ebayText, ...categoryText].map((part) => String(part || "")).join(" ");
}

export function isBottomsListing(listing: ListingLike): boolean {
  return BOTTOMS_RE.test(textOf(listing));
}

function departmentKind(listing: ListingLike): "women" | "men" | "" {
  const text = textOf(listing);
  if (WOMEN_RE.test(text)) return "women";
  if (MEN_RE.test(text)) return "men";
  return "";
}

export function bottomsOptions(listing: ListingLike, marketplace: string): Record<string, string[]> {
  if (!isBottomsListing(listing)) return {};
  const kind = departmentKind(listing);
  const women = kind !== "men";
  const men = kind !== "women";
  if (marketplace === "ebay") {
    return {
      size: [...(women ? EBAY_WOMENS_SIZES : []), ...(men ? EBAY_WAISTS : [])],
      sizeType: EBAY_SIZE_TYPES,
      inseam: EBAY_INSEAMS,
    };
  }
  if (marketplace === "poshmark") {
    return {
      size: [...(women ? POSHMARK_WOMEN : []), ...(men ? POSHMARK_MEN : [])],
      sizeType: POSHMARK_SIZE_TYPES,
    };
  }
  if (marketplace === "mercari") {
    return {
      size: [...(women ? WOMENS_NUMERIC : []), ...(men ? MERCARI_WAISTS : [])],
      sizeType: ["Regular", "Petite", "Plus", "Tall"],
    };
  }
  if (marketplace === "depop") {
    return {
      size: [...(women ? [...WOMENS_NUMERIC, "Other"] : []), ...(men ? DEPOP_MEN : [])],
      sizeGrouping: ["Petite", "Plus size", "Tall", "Maternity"],
    };
  }
  if (marketplace === "etsy") {
    return {
      size: [...(women ? WOMENS_NUMERIC : []), ...(men ? ETSY_WAISTS : []), ...(men ? ETSY_COMBINED : [])],
      inseam: EBAY_INSEAMS,
      sizeType: ["Regular", "Petite", "Plus", "Tall"],
    };
  }
  if (marketplace === "vinted") {
    return {
      size: [...(women ? VINTED_WOMEN : []), ...(men ? VINTED_MEN : [])],
      sizeType: ["Regular", "Petite", "Plus", "Tall"],
    };
  }
  if (marketplace === "sellwild") {
    return {
      size: [...(women ? WOMENS_NUMERIC : []), ...(men ? ETSY_WAISTS : []), ...(men ? ETSY_COMBINED : [])],
      sizeType: ["Regular", "Petite", "Plus", "Tall", "Big & Tall"],
    };
  }
  return {};
}

const FIELD_LABELS: Record<string, string> = {
  size: "Size",
  sizeType: "Size Type",
  inseam: "Inseam",
  sizeGrouping: "Size Grouping",
};

type SizeRow = {
  key: string;
  label: string;
  options?: string[];
  optionsSource?: string;
};

function sizeLeaf(key: string): string {
  const leaf = (key.split(".").pop() || "").toLowerCase().replace(/[^a-z]/g, "");
  if (leaf === "sizetype") return "sizeType";
  // A marketplace's own form names these by its catalog: its size id, and Depop's body fit.
  if (leaf === "sizegrouping" || leaf === "bodyfit") return "sizeGrouping";
  if (leaf === "nativesizeid") return "size";
  return leaf;
}

/**
 * Attach bottoms size choices, and add a size row the marketplace form does not have yet.
 * A live size list (including one that came back empty) is left as the marketplace sent it.
 * The general Size row is a different field, so it does not count as this marketplace's size.
 */
export function applyBottomsFields<T extends SizeRow>(
  fields: T[],
  listing: ListingLike,
  tab: string,
  optionsIn: { respectMarketplaceSize?: boolean } = {},
): T[] {
  const options = bottomsOptions(listing, tab);
  const names = Object.keys(options);
  if (!names.length) return fields;
  const prefix = `${tab}_specifics.`;
  const next = fields.map((field) => {
    if (!field.key.startsWith(prefix)) return field;
    const name = sizeLeaf(field.key);
    const choices = options[name];
    const liveSize = name === "size" && (optionsIn.respectMarketplaceSize || field.optionsSource === "marketplace");
    if (!choices?.length || liveSize || field.options?.length) return field;
    return { ...field, options: choices, optionsSource: "reference" as const };
  });
  const seen = new Set(next.filter((field) => field.key.startsWith(prefix)).map((field) => sizeLeaf(field.key)));
  const added = names.filter((name) => !seen.has(name) && !(name === "size" && optionsIn.respectMarketplaceSize)).map((name) => ({
    key: `${prefix}${name}`,
    label: FIELD_LABELS[name] || name,
    options: options[name],
    optionsSource: "reference" as const,
  }) as T);
  if (!added.length) return next;
  const priceAt = next.findIndex((field) => field.label.endsWith(" price"));
  if (priceAt < 0) return [...next, ...added];
  return [...next.slice(0, priceAt), ...added, ...next.slice(priceAt)];
}
