import { describe, expect, it } from "vitest";
import type { ListingSheetRow } from "../api/types";
import type { Listing } from "./sidebar/listingShelves";
import {
  buildScanSummary,
  canListFromSheet,
  formatSheetCellValue,
  gridColumnsForMarketplace,
  nextQuickEditListingId,
  orderedQuickEditColumns,
  sheetPriceLabel,
  shortCategoryLabel,
} from "./marketplaceSheetConfig";

describe("marketplaceSheetConfig", () => {
  it("shortens category breadcrumbs to their leaf", () => {
    expect(shortCategoryLabel("Menswear > Tops > T-shirts")).toBe("T-shirts");
    expect(shortCategoryLabel("Women / Jackets & Coats / Blazers")).toBe("Blazers");
    expect(shortCategoryLabel("T-shirts")).toBe("T-shirts");
  });

  it("formats prices and categories cleanly", () => {
    const cols = gridColumnsForMarketplace("depop");
    const priceCol = cols.find((c) => c.key === "price")!;
    const catCol = cols.find((c) => c.key === "direct_category_ids.depop")!;
    expect(formatSheetCellValue(priceCol, { value: "28", raw: "28", required: true, missing: false, rejected: false, error: "", editable: true })).toBe("$28");
    expect(formatSheetCellValue(catCol, { value: "Menswear > Tops > T-shirts", raw: "t", required: true, missing: false, rejected: false, error: "", editable: true })).toBe("T-shirts");
  });

  it("resolves per-marketplace price labels and preserves eBay variation/auction labels", () => {
    const depopRow: ListingSheetRow = {
      revision_id: "rev-1",
      ready: true,
      needs_load: false,
      cells: {
        price: { value: "27", raw: "27", required: true, missing: false, rejected: false, error: "", editable: true },
      },
      missing: [],
    };
    expect(sheetPriceLabel("depop", depopRow, null, 32)).toBe("$27");
    expect(sheetPriceLabel("depop", depopRow, { price: 32, depop_specifics: { price: 24 } }, 32)).toBe("$24");

    const variationRow: ListingSheetRow = {
      revision_id: "rev-2",
      ready: true,
      needs_load: false,
      cells: {
        price: { value: "3 variations", raw: "3 variations", required: true, missing: false, rejected: false, error: "", editable: false },
      },
      missing: [],
    };
    expect(sheetPriceLabel("ebay", variationRow, { price: 32 }, 32)).toBe("3 variations");
  });

  it("applies one consistent canListFromSheet rule across desktop and phone", () => {
    const listing = { id: "a", title: "Jacket", status: "draft", created_at: "", updated_at: "" } as Listing;
    const readyRow: ListingSheetRow = { revision_id: "rev-1", ready: true, needs_load: false, cells: {}, missing: [] };
    const unreadyRow: ListingSheetRow = { revision_id: "rev-1", ready: false, needs_load: false, cells: {}, missing: [] };

    expect(canListFromSheet(listing, { kind: "not_listed", label: "Not listed", url: null }, readyRow, true)).toBe(true);
    expect(canListFromSheet(listing, { kind: "draft", label: "Draft", url: null }, readyRow, true)).toBe(true);
    expect(canListFromSheet(listing, { kind: "delisted", label: "Delisted", url: null }, readyRow, true)).toBe(true);

    expect(canListFromSheet(listing, { kind: "needs", label: "Needs 1", url: null }, readyRow, true)).toBe(false);
    expect(canListFromSheet(listing, { kind: "failed", label: "Failed", url: null }, readyRow, true)).toBe(false);
    expect(canListFromSheet(listing, { kind: "listed", label: "Listed", url: null }, readyRow, true)).toBe(false);
    expect(canListFromSheet(listing, { kind: "not_listed", label: "Not listed", url: null }, unreadyRow, true)).toBe(false);
    expect(canListFromSheet(listing, { kind: "not_listed", label: "Not listed", url: null }, readyRow, false)).toBe(false);
    expect(canListFromSheet({ ...listing, settled_at: "2026-01-01" }, { kind: "not_listed", label: "Not listed", url: null }, readyRow, true)).toBe(false);
  });

  it("floats rejected then missing fields to the top in Quick Edit order", () => {
    const row: ListingSheetRow = {
      revision_id: "rev-1",
      ready: false,
      needs_load: false,
      cells: {
        "depop_specifics.parcelSize": { value: "", raw: "", required: true, missing: true, rejected: false, error: "Required", editable: true },
        "depop_specifics.size": { value: "M", raw: "M", required: true, missing: true, rejected: true, error: "Rejected", editable: true },
      },
      missing: [
        { field: "depop_specifics.parcelSize", label: "Package size", message: "Required", rejected: false },
        { field: "depop_specifics.size", label: "Size", message: "Rejected", rejected: true },
      ],
    };
    const ordered = orderedQuickEditColumns("depop", row);
    expect(ordered[0].key).toBe("depop_specifics.size");
    expect(ordered[1].key).toBe("depop_specifics.parcelSize");
  });

  it("builds scan summaries highlighting rejected or missing fields first", () => {
    const rejectedRow: ListingSheetRow = {
      revision_id: "rev-1",
      ready: false,
      needs_load: false,
      cells: {
        "depop_specifics.brand": { value: "Pendleton", raw: "Pendleton", required: false, missing: false, rejected: false, error: "", editable: true },
        "depop_specifics.condition": { value: "Used - Good", raw: "used_good", required: true, missing: false, rejected: false, error: "", editable: true },
        "depop_specifics.size": { value: "M", raw: "M", required: true, missing: true, rejected: true, error: "Invalid", editable: true },
      },
      missing: [{ field: "depop_specifics.size", label: "Size", message: "Invalid", rejected: true }],
    };
    const summary = buildScanSummary("depop", rejectedRow);
    expect(summary.alert).toBe("Fix Size");
    expect(summary.alertTone).toBe("error");
    expect(summary.meta).toContain("Pendleton");

    const readyRow: ListingSheetRow = {
      revision_id: "rev-2",
      ready: true,
      needs_load: false,
      cells: {
        "depop_specifics.brand": { value: "Nike", raw: "Nike", required: false, missing: false, rejected: false, error: "", editable: true },
        "depop_specifics.size": { value: "L", raw: "L", required: true, missing: false, rejected: false, error: "", editable: true },
      },
      missing: [],
    };
    const readySummary = buildScanSummary("depop", readyRow);
    expect(readySummary.alert).toBeNull();
    expect(readySummary.meta).toBe("Nike · L");
  });

  it("finds the next un-listed listing for Next draft traversal", () => {
    const base = { status: "draft", created_at: "", updated_at: "" } as Listing;
    const rows: Listing[] = [
      { ...base, id: "a", title: "Draft 1", direct_records: {} },
      { ...base, id: "b", title: "Listed 1", direct_records: { depop: { state: "published", failed: false, fields: 0 } } },
      { ...base, id: "c", title: "Draft 2", direct_records: {} },
    ];
    expect(nextQuickEditListingId(rows, "a", "depop")).toBe("c");
    expect(nextQuickEditListingId(rows, "c", "depop")).toBe("a");
    expect(nextQuickEditListingId([rows[0], rows[1]], "a", "depop")).toBeNull();
  });
});
