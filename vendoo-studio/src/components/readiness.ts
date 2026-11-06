/** How a marketplace's missing fields read, on the editor's tabs, List everywhere and the inventory. */
import type { MissingField } from "../api/nativeMarketplaces";

/** "Needs 2 fields". */
export function needsText(count: number): string {
  return `Needs ${count} ${count === 1 ? "field" : "fields"}`;
}

/** "Needs 2 fields: Price, Size", for a tooltip or a screen reader. */
export function missingDetail(missing: readonly MissingField[]): string {
  return missing.length ? `${needsText(missing.length)}: ${missing.map((item) => item.label).join(", ")}` : "Ready to list";
}
