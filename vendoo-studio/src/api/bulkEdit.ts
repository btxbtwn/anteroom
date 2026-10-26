export const BULK_FIELDS = ["title", "description", "brand", "size", "sku", "price", "cost", "quantity"] as const;
export type BulkField = (typeof BULK_FIELDS)[number];
export interface BulkEdits { values: Partial<Record<BulkField, string | number | null>>; labels_mode: "keep" | "add" | "remove" | "replace"; labels: string[] }
export interface BulkReviewToken { conversation_id: string; expected_revision_id: string; expected_basis: string; expected_result: string }
export interface BulkReviewRow extends BulkReviewToken { title: string; skip_reason: string | null; changes: { field: string; old: unknown; new: unknown }[] }
export interface BulkEditResult { applied: { conversation_id: string; revision_id: string; valid: boolean }[]; skipped: { conversation_id: string; reason: string }[] }

export function bulkEditsFromInputs(fields: Partial<Record<BulkField, string>>, labelsMode: BulkEdits["labels_mode"], labelsText: string): BulkEdits {
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
      values[field] = text;
    }
  }
  const labels = [...new Set(labelsText.split(",").map((value) => value.trim()).filter(Boolean))];
  if (!Object.keys(values).length && labelsMode === "keep") throw new Error("Choose at least one field or label action.");
  if ((labelsMode === "add" || labelsMode === "remove") && !labels.length) throw new Error("Enter labels to add or remove.");
  return { values, labels_mode: labelsMode, labels };
}
export function bulkReviewTokens(rows: BulkReviewRow[]): BulkReviewToken[] {
  return rows.filter((row) => !row.skip_reason && row.changes.length > 0).map(({ conversation_id, expected_revision_id, expected_basis, expected_result }) => ({ conversation_id, expected_revision_id, expected_basis, expected_result }));
}
