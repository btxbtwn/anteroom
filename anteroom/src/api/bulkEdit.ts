export const BULK_FIELDS = ["title", "description", "brand", "size", "sku", "price", "cost", "quantity", "condition", "primaryColor", "secondaryColor"] as const;
export type BulkField = (typeof BULK_FIELDS)[number];
/** Fields chosen from a fixed list rather than typed. */
export const CHOICE_FIELDS: ReadonlySet<BulkField> = new Set(["condition", "primaryColor", "secondaryColor"]);
export type ListMode = "keep" | "add" | "remove" | "replace";
export interface BulkCategories { general?: string; depop?: string; ebay?: { id: string; path: string } }
export interface BulkEdits {
  values: Partial<Record<BulkField, string | number | null>>;
  labels_mode: ListMode;
  labels: string[];
  tags_mode?: ListMode;
  tags?: string[];
  category?: BulkCategories;
  /** A saved shipping preset's id: its package size, weight and marketplace shipping. */
  shipping_preset?: string;
  /** Put every marketplace price back on the seller's pricing rules. */
  pricing_rules?: boolean;
}
export interface BulkExtras { tagsMode: ListMode; tagsText: string; category: BulkCategories; shippingPreset?: string; pricingRules?: boolean }

function splitList(text: string): string[] {
  return [...new Set(text.split(",").map((value) => value.trim()).filter(Boolean))];
}
export interface BulkReviewToken { conversation_id: string; expected_revision_id: string; expected_basis: string; expected_result: string }
export interface BulkReviewRow extends BulkReviewToken { title: string; skip_reason: string | null; changes: { field: string; old: unknown; new: unknown }[] }
export interface BulkEditResult { applied: { conversation_id: string; revision_id: string; valid: boolean }[]; skipped: { conversation_id: string; reason: string }[] }

export function bulkEditsFromInputs(fields: Partial<Record<BulkField, string>>, labelsMode: ListMode, labelsText: string, extras?: BulkExtras): BulkEdits {
  const values: BulkEdits["values"] = {};
  for (const field of BULK_FIELDS) {
    if (fields[field] === undefined) continue;
    const text = fields[field]!.trim();
    if (["price", "cost", "quantity"].includes(field)) {
      if (!text && field === "cost") { values[field] = null; continue; }
      if (!/^\d+(?:\.\d{1,2})?$/.test(text)) throw new Error(`Enter a valid ${field}.`);
      const number = Number(text);
      if (number > 1000000 || (field !== "cost" && (!Number.isInteger(number) || number < 1))) throw new Error(`${field} must be a positive whole number up to 1000000.`);
      values[field] = number;
    } else {
      if (!text && (field === "title" || field === "size")) throw new Error(`${field} cannot be empty.`);
      if (!text && (field === "condition" || field === "primaryColor")) throw new Error(`Choose a ${field === "condition" ? "condition" : "primary color"}.`);
      values[field] = text;
    }
  }
  const labels = splitList(labelsText);
  const tagsMode = extras?.tagsMode ?? "keep";
  const tags = splitList(extras?.tagsText ?? "");
  const category = Object.fromEntries(Object.entries(extras?.category ?? {}).filter(([, value]) => value)) as BulkCategories;
  const hasCategory = Object.keys(category).length > 0;
  const shippingPreset = extras?.shippingPreset || "";
  const pricingRules = Boolean(extras?.pricingRules);
  if (!Object.keys(values).length && labelsMode === "keep" && tagsMode === "keep" && !hasCategory && !shippingPreset && !pricingRules) {
    throw new Error("Choose at least one field, label, tag, category, shipping or pricing action.");
  }
  if ((labelsMode === "add" || labelsMode === "remove") && !labels.length) throw new Error("Enter labels to add or remove.");
  if ((tagsMode === "add" || tagsMode === "remove") && !tags.length) throw new Error("Enter tags to add or remove.");
  return { values, labels_mode: labelsMode, labels,
    ...(tagsMode !== "keep" ? { tags_mode: tagsMode, tags } : {}), ...(hasCategory ? { category } : {}),
    ...(shippingPreset ? { shipping_preset: shippingPreset } : {}), ...(pricingRules ? { pricing_rules: true } : {}) };
}
export function bulkReviewTokens(rows: BulkReviewRow[]): BulkReviewToken[] {
  return rows.filter((row) => !row.skip_reason && row.changes.length > 0).map(({ conversation_id, expected_revision_id, expected_basis, expected_result }) => ({ conversation_id, expected_revision_id, expected_basis, expected_result }));
}
