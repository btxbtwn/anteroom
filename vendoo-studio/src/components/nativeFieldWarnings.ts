import type { PreflightGap } from "../api/nativeMarketplaces";

/**
 * A marketplace's own name for one of its rows, when it differs from the row's
 * key. Mercari answers a refusal with its form's names (``sizeId``,
 * ``categoryId``, ``name``); Poshmark checks General's colors and shows its
 * answer on its own Color row, and its Size is the inventory list. Every
 * marketplace's ``photos`` answer is about its own photo area.
 */
const ROW_ALIASES: Record<string, Record<string, string>> = Object.fromEntries(
  ["ebay", "etsy", "poshmark", "mercari", "depop", "vinted", "sellwild"].map((marketplace) => [marketplace, { photos: `marketplace_photos.${marketplace}` }]),
) as Record<string, Record<string, string>>;

Object.assign(ROW_ALIASES.poshmark, {
  primaryColor: "poshmark_specifics.colors",
  secondaryColor: "poshmark_specifics.colors",
  size: "poshmark_specifics.inventoryRows",
});
Object.assign(ROW_ALIASES.mercari, {
  name: "mercari_specifics.title",
  categoryId: "direct_category_ids.mercari",
  conditionId: "mercari_specifics.condition",
  size: "mercari_specifics.nativeSizeId",
  sizeId: "mercari_specifics.nativeSizeId",
  brandId: "mercari_specifics.brand",
  shippingClassIds: "mercari_specifics.shippingClassId",
  weight: "mercari_specifics.package.pounds",
});

/**
 * The messages a direct List would say about the fields on this form, under the
 * field each one is about. A gap whose field has no row here (a category the
 * form does not show, or the listing as a whole) stays beside List, where List
 * is.
 */
export function gapsByField(
  marketplace: string,
  gaps: readonly PreflightGap[],
  rows: readonly { key: string }[],
): Map<string, string[]> {
  const found = new Map<string, string[]>();
  for (const gap of gaps) {
    const message = String(gap.message || "").trim();
    if (!message) continue;
    const key = rowForField(marketplace, String(gap.field || ""), rows);
    if (!key) continue;
    const messages = found.get(key) || [];
    // A revision can repeat the same answer; the seller reads it once.
    if (!messages.includes(message)) found.set(key, [...messages, message]);
  }
  return found;
}

/**
 * The row a marketplace's answer is about: the field's own key, the form's
 * namespaced key for it, or a row with the same last name. Empty when this form
 * has no such row.
 */
export function rowForField(marketplace: string, field: string, rows: readonly { key: string }[]): string {
  const keys = rows.map((row) => row.key);
  const named = [ROW_ALIASES[marketplace]?.[field], field, `${marketplace}_specifics.${field}`];
  const exact = named.find((key) => Boolean(key) && keys.includes(key as string));
  if (exact) return exact as string;
  const leaf = field.split(".").pop()?.toLowerCase() || "";
  if (!leaf) return "";
  return keys.find((key) => key.split(".").pop()?.toLowerCase() === leaf) || "";
}
