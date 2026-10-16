import type { FillLogEntry, FillLogReport } from "../../api/types";
import { isAccountManagedField } from "./fieldKeys";

const STATUS_LABELS: Record<string, string> = {
  filled: "Filled",
  skipped: "Not filled",
  not_applicable: "Does not apply",
  not_found: "Missing",
  invalid: "Invalid option",
  failed: "Didn't work",
  uncertain: "Uncertain",
  new: "New fields",
};

const STATUS_ORDER = ["invalid", "failed", "not_found", "uncertain", "new", "skipped", "not_applicable", "filled"];
export const FILLABLE_STATUSES = new Set(["invalid", "failed", "not_found", "uncertain", "new", "skipped"]);
const DISCOVERY_STATUSES = new Set(["new"]);
/** Real Apply/Send failures only — not skipped-empty or already-correct controls. */
export const FILL_FAILURE_STATUSES = new Set(["invalid", "failed", "not_found", "uncertain"]);

function isAlreadySetEntry(entry: { status?: string; reason?: string }): boolean {
  return /^already set$/i.test(String(entry.reason || "").trim());
}

export function issueLabel(entry: FillLogEntry): string {
  if (entry.status === "new") return "Discovered on form";
  if (isAlreadySetEntry(entry)) return "Already set";
  if (/^no evidence\b/i.test(String(entry.reason || "").trim())) return "No evidence";
  if (entry.status === "skipped") return "Not filled";
  return STATUS_LABELS[entry.status] || entry.status;
}

export function issueKind(entry: FillLogEntry): "discovery" | "failure" {
  return DISCOVERY_STATUSES.has(entry.status) ? "discovery" : "failure";
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

export function fillFailureEntries(report: FillLogReport): FillLogEntry[] {
  return leftoverEntries(report).filter((entry) => FILL_FAILURE_STATUSES.has(entry.status));
}
