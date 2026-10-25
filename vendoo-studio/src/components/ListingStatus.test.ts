import { describe, expect, it } from "vitest";
import { inventoryStatus, listedFromImport, marketplaceStatusesFromDraft, statusHint, withDirectListings } from "./sidebar/listingStatus";

describe("statusHint", () => {
  it("says where each Vendoo label comes from", () => {
    expect(statusHint("draft")).toBe("Draft in Vendoo");
    expect(statusHint("active")).toBe("Listed on a marketplace, per Vendoo");
    expect(statusHint("sold")).toBe("Sold, per Vendoo");
  });

  it("explains the states Studio itself causes", () => {
    expect(statusHint("failed")).toBe("The last send to Vendoo failed");
    expect(statusHint("listing")).toBe("Anteroom is sending this listing to Vendoo");
    expect(statusHint("in_progress")).toBe("Anteroom is working on this listing");
  });

  it("still names Vendoo for a status it does not know", () => {
    expect(statusHint("some_new_state")).toBe("some new state · follows Vendoo");
  });
});

describe("listedFromImport", () => {
  it("badges the marketplaces the import recorded", () => {
    expect(listedFromImport({ vendoo_marketplaces: ["poshmark", "ebay"] })).toEqual([
      { id: "ebay", label: "eBay", status: "LISTED" },
      { id: "poshmark", label: "Poshmark", status: "LISTED" },
    ]);
  });

  it("marks the marketplace an item sold on", () => {
    const rows = listedFromImport({
      vendoo_marketplaces: ["ebay", "mercari"],
      vendoo_sold_dates: { mercari: "2026-09-01T00:00:00Z" },
    });
    expect(rows.map((row) => row.status)).toEqual(["LISTED", "SOLD"]);
  });

  it("keeps only the marketplaces Settings shows, and folds Vestiaire's two ids", () => {
    expect(
      listedFromImport({ vendoo_marketplaces: ["vestiaireApi", "depop"] }, new Set(["vestiaire"])),
    ).toEqual([{ id: "vestiaire", label: "Vestiaire Collective", status: "LISTED" }]);
  });

  it("has nothing to say for a listing that was never imported", () => {
    expect(listedFromImport({})).toEqual([]);
  });
});

describe("marketplaceStatusesFromDraft", () => {
  it("shows a direct listing as listed when the nav says not listed", () => {
    const rows = marketplaceStatusesFromDraft({
      statuses: {
        ebay: "NOT LISTED",
        etsy: "NOT LISTED",
        poshmark: "NOT LISTED",
        mercari: "NOT LISTED",
        depop: "NOT LISTED",
        sellwild: "NOT LISTED",
      },
      item: {
        listings: {
          ebay: { status: { notListed: true } },
          mercari: { status: { listed: true } },
        },
        externalListings: {
          poshmark: { status: { listed: true } },
          sellwild: { status: { listed: true } },
        },
      },
    });
    expect(rows.filter((row) => row.id !== "general").map((row) => [row.id, row.status])).toEqual([
      ["ebay", "NOT LISTED"],
      ["etsy", "NOT LISTED"],
      ["poshmark", "LISTED"],
      ["mercari", "LISTED"],
      ["depop", "NOT LISTED"],
      ["sellwild", "LISTED"],
    ]);
  });
});

describe("inventoryStatus", () => {
  it("reads a Vendoo draft as active once Anteroom has published it", () => {
    expect(inventoryStatus({ status: "draft", direct_marketplaces: ["depop", "ebay"] })).toBe("active");
    expect(inventoryStatus({ status: "draft" })).toBe("draft");
  });

  it("reads a Vendoo active as draft once Anteroom delisted every marketplace", () => {
    const delisted = {
      status: "active",
      vendoo_marketplaces: ["depop", "ebay", "mercari", "poshmark", "sellwild"],
      removed_marketplaces: ["ebay", "etsy", "poshmark", "mercari", "depop", "sellwild"],
    };
    expect(inventoryStatus(delisted)).toBe("draft");
    expect(inventoryStatus({ ...delisted, removed_marketplaces: ["ebay"] })).toBe("active");
    expect(inventoryStatus({ ...delisted, vendoo_marketplaces: [...delisted.vendoo_marketplaces, "facebook"] })).toBe("active");
    expect(inventoryStatus({ ...delisted, direct_marketplaces: ["depop"] })).toBe("active");
    expect(inventoryStatus({ status: "active", vendoo_marketplaces: ["ebay"] })).toBe("active");
  });

  it("keeps a sale and a failed send", () => {
    expect(inventoryStatus({ status: "sold", removed_marketplaces: ["ebay"] })).toBe("sold");
    expect(inventoryStatus({ status: "sold", direct_marketplaces: ["depop"] })).toBe("sold");
    expect(inventoryStatus({ status: "failed", direct_marketplaces: ["ebay"] })).toBe("failed");
  });
});

describe("withDirectListings", () => {
  it("shows Anteroom's publish when Vendoo says the marketplace is not listed", () => {
    const rows = withDirectListings(
      [
        { id: "ebay", label: "eBay", status: "DELISTED" },
        { id: "depop", label: "Depop", status: "NOT LISTED" },
        { id: "etsy", label: "Etsy", status: "NOT LISTED" },
      ],
      ["depop", "poshmark", "mercari", "sellwild"],
    );
    expect(rows.filter((row) => row.status === "LISTED").map((row) => row.id)).toEqual([
      "poshmark", "mercari", "depop", "sellwild",
    ]);
    expect(rows.find((row) => row.id === "etsy")?.status).toBe("NOT LISTED");
    expect(rows.find((row) => row.id === "ebay")?.status).toBe("DELISTED");
  });
});

describe("withDirectListings removals", () => {
  it("marks what Anteroom delisted as delisted, but keeps a sale", () => {
    const rows = withDirectListings(
      [
        { id: "ebay", label: "eBay", status: "LISTED" },
        { id: "depop", label: "Depop", status: "SOLD" },
        { id: "mercari", label: "Mercari", status: "LISTED" },
      ],
      undefined,
      undefined,
      ["ebay", "depop"],
    );
    expect(rows.map((row) => [row.id, row.status])).toEqual([
      ["ebay", "DELISTED"], ["depop", "SOLD"], ["mercari", "LISTED"],
    ]);
  });
});
