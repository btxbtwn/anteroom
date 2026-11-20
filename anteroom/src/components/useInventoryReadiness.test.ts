import { describe, expect, it } from "vitest";
import { missingDetail, needsText } from "./readiness";
import { readinessBlocks, readinessKey } from "./useInventoryReadiness";

describe("inventory readiness", () => {
  it("asks for the fixed blocks the rows on screen fall in", () => {
    expect(readinessBlocks(0, 12)).toEqual([0]);
    expect(readinessBlocks(20, 30)).toEqual([0, 1]);
    expect(readinessBlocks(60, 74)).toEqual([2]);
    expect(readinessBlocks(0, -1)).toEqual([]);
  });

  it("reads a row again after a saved edit or a new List result", () => {
    const row = { id: "a", updated_at: "2026-11-05T10:00:00Z", direct_records: {} };
    expect(readinessKey(row)).toBe(readinessKey({ ...row }));
    expect(readinessKey({ ...row, updated_at: "2026-11-05T10:01:00Z" })).not.toBe(readinessKey(row));
    expect(readinessKey({ ...row, direct_records: { depop: { state: "not_saved", failed: true, fields: 1 } } })).not.toBe(readinessKey(row));
  });

  it("names the missing fields", () => {
    expect(needsText(1)).toBe("Needs 1 field");
    expect(missingDetail([{ field: "price", label: "Price", message: "" }, { field: "size", label: "Size", message: "" }]))
      .toBe("Needs 2 fields: Price, Size");
    expect(missingDetail([])).toBe("Ready to list");
  });
});
