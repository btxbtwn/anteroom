export interface BulkDeleteRow {
  conversation_id: string;
  title: string | null;
  skip_reason: string | null;
  warning: string | null;
  review_token: string | null;
}
export interface BulkDeleteResult { deleted: string[]; skipped: { conversation_id: string; reason: string }[] }

/** The reviewed items the seller left checked, each with its review token. */
export function deletableRows(rows: readonly BulkDeleteRow[], unchecked: ReadonlySet<string>): { conversation_id: string; review_token: string }[] {
  return rows.flatMap((row) => !row.skip_reason && row.review_token && !unchecked.has(row.conversation_id)
    ? [{ conversation_id: row.conversation_id, review_token: row.review_token }] : []);
}
