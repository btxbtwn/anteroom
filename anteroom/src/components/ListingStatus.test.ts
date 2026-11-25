import { describe, expect, it } from "vitest";
import { inventoryStatus, listedFromImport, liveMarketplaces, marketplacesLabel, statusHint, withDirectListings } from "./sidebar/listingStatus";

describe("statusHint", () => {
  it("explains each inventory status", () => {
    expect(statusHint("draft")).toBe("Not listed on any marketplace");
    expect(statusHint("active")).toBe("Listed on a marketplace");
    expect(statusHint("sold")).toBe("Sold");
    expect(statusHint("failed")).toBe("The last send failed");
    expect(statusHint("listing")).toBe("Anteroom is sending this listing");
    expect(statusHint("in_progress")).toBe("Anteroom is working on this listing");
  });

  it("spells out a status it does not know", () => {
    expect(statusHint("some_new_state")).toBe("some new state");
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

  it("never badges Sellwild: Anteroom cannot list or delist it", () => {
    expect(
      listedFromImport({ vendoo_marketplaces: ["ebay", "sellwild", "poshmark"] }).map((row) => row.id),
    ).toEqual(["ebay", "poshmark"]);
    expect(listedFromImport({ vendoo_marketplaces: ["sellwild"] })).toEqual([]);
  });
});

describe("inventoryStatus", () => {
  it("reads a Vendoo draft as active once Anteroom has published it", () => {
    expect(inventoryStatus({ status: "draft", direct_marketplaces: ["depop", "ebay"] })).toBe("active");
    expect(inventoryStatus({ status: "draft" })).toBe("draft");
  });

  it("reads a listing as inactive once Anteroom delisted every marketplace", () => {
    const delisted = {
      status: "active",
      vendoo_marketplaces: ["depop", "ebay", "mercari", "poshmark"],
      removed_marketplaces: ["ebay", "etsy", "poshmark", "mercari", "depop"],
    };
    expect(inventoryStatus(delisted)).toBe("inactive");
    expect(inventoryStatus({ ...delisted, status: "draft" })).toBe("inactive");
    expect(inventoryStatus({ ...delisted, removed_marketplaces: ["ebay"] })).toBe("active");
    expect(inventoryStatus({ ...delisted, direct_marketplaces: ["depop"] })).toBe("active");
    expect(inventoryStatus({ status: "active", vendoo_marketplaces: ["ebay"] })).toBe("active");
    expect(inventoryStatus({ status: "draft" })).toBe("draft");
  });

  it("ignores imported marketplaces Anteroom cannot delist once the rest are delisted", () => {
    const delisted = {
      status: "active",
      vendoo_marketplaces: ["depop", "ebay", "mercari", "poshmark", "sellwild", "facebook"],
      removed_marketplaces: ["ebay", "poshmark", "mercari", "depop"],
    };
    expect(inventoryStatus(delisted)).toBe("inactive");
    expect(inventoryStatus({ ...delisted, removed_marketplaces: ["ebay", "poshmark", "mercari"] })).toBe("active");
    expect(inventoryStatus({ status: "active", vendoo_marketplaces: ["sellwild"] })).toBe("active");
  });

  it("keeps a sale and a failed send without direct listings", () => {
    expect(inventoryStatus({ status: "sold", removed_marketplaces: ["ebay"] })).toBe("sold");
    expect(inventoryStatus({ status: "sold", direct_marketplaces: ["depop"] })).toBe("sold");
    expect(inventoryStatus({ status: "failed" })).toBe("failed");
  });

  it("shows a direct listing as active despite an earlier failed Vendoo send", () => {
    expect(inventoryStatus({ status: "failed", direct_marketplaces: ["ebay"] })).toBe("active");
    expect(inventoryStatus({ status: "listing", direct_marketplaces: ["ebay"] })).toBe("listing");
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
      ["depop", "poshmark", "mercari"],
    );
    expect(rows.filter((row) => row.status === "LISTED").map((row) => row.id)).toEqual([
      "poshmark", "mercari", "depop",
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


describe("withDirectListings sales Anteroom recorded", () => {
  it("shows a marketplace the item sold on as Sold, even while its listing record is live", () => {
    const rows = withDirectListings([], ["poshmark"], undefined, undefined, ["poshmark"]);
    expect(rows.map((row) => [row.id, row.status])).toEqual([["poshmark", "SOLD"]]);
  });

  it("keeps a live record Listed when nothing sold there", () => {
    const rows = withDirectListings([], ["poshmark"], undefined, undefined, []);
    expect(rows.map((row) => [row.id, row.status])).toEqual([["poshmark", "LISTED"]]);
  });

  it("leaves the other marketplaces Listed, since the seller may still need to delist them", () => {
    const rows = withDirectListings([{ id: "mercari", label: "Mercari", status: "LISTED" }], ["poshmark", "ebay"], undefined, undefined, ["poshmark"]);
    expect(Object.fromEntries(rows.map((row) => [row.id, row.status]))).toEqual({ mercari: "LISTED", poshmark: "SOLD", ebay: "LISTED" });
  });

  it("never turns an imported sale back into Listed because a record is live", () => {
    const rows = withDirectListings([{ id: "poshmark", label: "Poshmark", status: "SOLD" }], ["poshmark"]);
    expect(rows.map((row) => [row.id, row.status])).toEqual([["poshmark", "SOLD"]]);
  });
});

describe("marketplacesLabel", () => {
  it("does not call a sold listing listed", () => {
    expect(marketplacesLabel([{ label: "Mercari", status: "LISTED" }, { label: "Poshmark", status: "SOLD" }]))
      .toBe("Listed on Mercari; Sold on Poshmark");
    expect(marketplacesLabel([{ label: "Poshmark", status: "SOLD" }])).toBe("Sold on Poshmark");
  });
});

describe("liveMarketplaces", () => {
  it("does not count a marketplace the item sold on as live", () => {
    expect(liveMarketplaces({ direct_marketplaces: ["poshmark", "mercari"], sold_marketplaces: ["poshmark"] })).toEqual(["mercari"]);
    expect(liveMarketplaces({ vendoo_marketplaces: ["poshmark"], sold_marketplaces: ["poshmark"] })).toEqual([]);
  });
});
