import { describe, expect, it } from "vitest";
import { inventoryRows, listedCounts, marketplaceCell } from "./inventoryTable";

describe("marketplaceCell", () => {
  it("reads a direct publish as listed, with its link", () => {
    expect(marketplaceCell({ direct_records: { ebay: { state: "published", listing_url: "https://ebay.com/itm/1" } } }, "ebay"))
      .toEqual({ kind: "listed", label: "Listed", url: "https://ebay.com/itm/1" });
    expect(marketplaceCell({ direct_records: { mercari: { state: "pending_review" } } }, "mercari"))
      .toMatchObject({ kind: "listed", label: "In review", url: null });
  });

  it("reads an imported listing as listed, with the imported link", () => {
    expect(marketplaceCell({ vendoo_marketplaces: ["poshmark"], vendoo_listing_urls: { poshmark: "https://posh.mk/1" } }, "poshmark"))
      .toEqual({ kind: "listed", label: "Listed", url: "https://posh.mk/1" });
  });

  it("reads a sale on that marketplace as sold", () => {
    expect(marketplaceCell({ vendoo_marketplaces: ["depop"], vendoo_sold_dates: { depop: "2026-10-01" } }, "depop"))
      .toMatchObject({ kind: "sold", label: "Sold" });
  });

  it("counts the fields a marketplace sent back", () => {
    expect(marketplaceCell({ direct_records: { poshmark: { state: "not_saved", failed: true, fields: 2 } } }, "poshmark"))
      .toEqual({ kind: "needs", label: "Needs 2 fields", url: null });
    expect(marketplaceCell({ direct_records: { poshmark: { state: "not_saved", failed: true, fields: 1 } } }, "poshmark"))
      .toMatchObject({ label: "Needs 1 field" });
    expect(marketplaceCell({ direct_records: { etsy: { state: "not_saved", failed: true } } }, "etsy"))
      .toMatchObject({ kind: "failed", label: "Failed" });
  });

  it("shows a saved marketplace draft and a running action", () => {
    expect(marketplaceCell({ direct_records: { depop: { state: "draft" } } }, "depop")).toMatchObject({ kind: "draft", label: "Draft" });
    expect(marketplaceCell({ direct_records: { depop: { state: "publishing" } } }, "depop")).toMatchObject({ kind: "working", label: "Working" });
    expect(marketplaceCell({ direct_records: { depop: { state: "unknown" } } }, "depop")).toMatchObject({ kind: "working", label: "Checking" });
  });

  it("keeps a live listing listed when a later Update failed", () => {
    expect(marketplaceCell({ direct_records: { ebay: { state: "published", failed: true, fields: 1 } } }, "ebay"))
      .toMatchObject({ kind: "listed" });
  });

  it("reads a direct delist as delisted, and nothing as not listed", () => {
    expect(marketplaceCell({ vendoo_marketplaces: ["ebay"], removed_marketplaces: ["ebay"] }, "ebay"))
      .toMatchObject({ kind: "delisted", label: "Delisted", url: null });
    expect(marketplaceCell({}, "etsy")).toEqual({ kind: "not_listed", label: "Not listed", url: null });
    expect(marketplaceCell({ direct_records: { etsy: { state: "not_saved" } } }, "etsy")).toMatchObject({ kind: "not_listed" });
  });
});

describe("marketplaceCell readiness", () => {
  const missing = [
    { field: "price", label: "Listing Price", message: "Listing Price: Required field is empty." },
    { field: "depop_specifics.parcelSize", label: "Package size", message: "Depop needs a parcel size." },
  ];

  it("counts required fields left empty before anything is listed, and names them", () => {
    expect(marketplaceCell({}, "depop", missing)).toEqual({
      kind: "needs", label: "Needs 2 fields", url: null, detail: "Needs 2 fields: Listing Price, Package size",
    });
    expect(marketplaceCell({ direct_records: { depop: { state: "draft" } } }, "depop", missing)).toMatchObject({ kind: "needs" });
    expect(marketplaceCell({}, "depop", [])).toMatchObject({ kind: "not_listed" });
  });

  it("uses the server's count, which already holds the rejected fields, over the record's own", () => {
    const failed = { direct_records: { depop: { state: "not_saved", failed: true, fields: 1 } } };
    expect(marketplaceCell(failed, "depop", missing)).toMatchObject({ label: "Needs 2 fields" });
    expect(marketplaceCell(failed, "depop")).toMatchObject({ label: "Needs 1 field" });
  });

  it("never says a live, sold, running or delisted listing needs fields", () => {
    expect(marketplaceCell({ direct_records: { depop: { state: "published" } } }, "depop", missing)).toMatchObject({ kind: "listed" });
    expect(marketplaceCell({ vendoo_sold_dates: { depop: "2026-10-01" } }, "depop", missing)).toMatchObject({ kind: "sold" });
    expect(marketplaceCell({ direct_records: { depop: { state: "saving" } } }, "depop", missing)).toMatchObject({ kind: "working" });
    expect(marketplaceCell({ vendoo_marketplaces: ["depop"], removed_marketplaces: ["depop"] }, "depop", missing))
      .toMatchObject({ kind: "delisted" });
  });
});

describe("listedCounts", () => {
  it("counts live listings per marketplace for the column headers", () => {
    const rows = [
      { vendoo_marketplaces: ["poshmark", "ebay"] },
      { direct_records: { poshmark: { state: "published" } } },
      { direct_records: { poshmark: { state: "draft" } } },
    ];
    expect(listedCounts(rows, ["ebay", "poshmark", "etsy"])).toEqual({ ebay: 1, poshmark: 2, etsy: 0 });
  });
});

describe("inventoryRows", () => {
  const rows = [
    { id: "a", title: "Old coat", status: "draft", updated_at: "2026-10-01T00:00:00Z", price: 40 },
    { id: "b", title: "New tee", status: "active", updated_at: "2026-11-01T00:00:00Z", price: 12 },
    { id: "c", title: "Sold boots", status: "sold", settled_at: "2026-10-15T00:00:00Z", updated_at: "2026-10-15T00:00:00Z" },
  ];
  it("orders by recent activity and keeps finished listings out of All", () => {
    expect(inventoryRows(rows, "", "all", "recent").map((row) => row.id)).toEqual(["b", "a"]);
  });
  it("keeps the status tab, search and sort", () => {
    expect(inventoryRows(rows, "", "sold", "recent").map((row) => row.id)).toEqual(["c"]);
    expect(inventoryRows(rows, "boots", "all", "recent").map((row) => row.id)).toEqual(["c"]);
    expect(inventoryRows(rows, "", "all", "price_desc").map((row) => row.id)).toEqual(["a", "b"]);
  });
});
