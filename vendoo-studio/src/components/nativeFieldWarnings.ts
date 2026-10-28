import type { PreflightGap } from "../api/nativeMarketplaces";

/**
 * A gap that names a different listing field from the row it fills. Poshmark
 * checks General's colors and shows its answer on its own Color row, and its
 * Size is the inventory list.
 */
const ROW_ALIASES: Record<string, Record<string, string>> = {
  poshmark: {
    primaryColor: "poshmark_specifics.colors",
    secondaryColor: "poshmark_specifics.colors",
    size: "poshmark_specifics.inventoryRows",
  },
};

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
