import type { BulkReviewRow } from "../api/bulkEdit";
import { listingChanges } from "./listingChanges";

/** Review the changed listing fields, using the same paths as revision history. */
export function bulkEditChanges(row: BulkReviewRow) {
  return listingChanges(
    Object.fromEntries(row.changes.map((change) => [change.field, change.old])),
    Object.fromEntries(row.changes.map((change) => [change.field, change.new])),
  );
}
