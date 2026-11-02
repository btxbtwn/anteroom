import type { FillLogEntry, FillLogReport } from "../../api/types";
import { isAccountManagedField } from "./fieldKeys";

const STATUS_ORDER = ["invalid", "failed", "not_found", "uncertain", "new", "skipped", "not_applicable", "filled"];
export const FILLABLE_STATUSES = new Set(["invalid", "failed", "not_found", "uncertain", "new", "skipped"]);


function isAlreadySetEntry(entry: { status?: string; reason?: string }): boolean {
  return /^already set$/i.test(String(entry.reason || "").trim());
}

export function leftoverEntries(report: FillLogReport): FillLogEntry[] {
  const rows: FillLogEntry[] = [];
  for (const group of Object.values(report.by_marketplace)) {
    for (const entry of group.entries) {
      if (!FILLABLE_STATUSES.has(entry.status)) continue;
      // Already-correct controls are successes, not leftovers to retry.
      if (isAlreadySetEntry(entry)) continue;
      if (isAccountManagedField(entry.marketplace, entry.field)) continue;
      rows.push(entry);
    }
  }
  return rows.sort((left, right) => {
    const statusDelta = STATUS_ORDER.indexOf(left.status) - STATUS_ORDER.indexOf(right.status);
    if (statusDelta !== 0) return statusDelta;
    return `${left.marketplace} ${left.field}`.localeCompare(`${right.marketplace} ${right.field}`);
  });
}
