import { describe, expect, it } from "vitest";
import {
  DEFAULT_LISTING_FILTERS,
  filterListings,
  marketplaceCounts,
  matchesSearch,
  notListedCount,
} from "../inventoryFilters";
import { activeAnchorMs, compactRelativeTime, settledAnchorMs, soldAtMs, type Listing } from "./listingShelves";
import { inventoryStatus, listingRowPrice, liveMarketplaces, partialSale, quantityLabel } from "./listingStatus";

const ms = (iso: string) => Date.parse(iso);

describe("activeAnchorMs", () => {
  it("anchors an open listing on its latest touch", () => {
    expect(activeAnchorMs({
      id: "a",
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-03T00:00:00Z",
      unsettled_at: "2026-09-02T00:00:00Z",
    })).toBe(ms("2026-09-03T00:00:00Z"));
  });

  it("lifts a reopened listing to when it was reopened", () => {
    expect(activeAnchorMs({
      id: "a",
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-02T00:00:00Z",
      unsettled_at: "2026-09-05T00:00:00Z",
    })).toBe(ms("2026-09-05T00:00:00Z"));
  });

  it("treats missing or unreadable dates as the epoch", () => {
    expect(activeAnchorMs({ id: "a" })).toBe(0);
    expect(activeAnchorMs({ id: "a", updated_at: "not a date" })).toBe(0);
  });
});

describe("settledAnchorMs", () => {
  it("anchors a finished listing on its latest sale", () => {
    expect(settledAnchorMs({
      id: "s",
      sold_at: "2026-09-10T00:00:00Z",
      settled_at: "2026-09-08T00:00:00Z",
      updated_at: "2026-09-20T00:00:00Z",
    })).toBe(ms("2026-09-10T00:00:00Z"));
  });

  it("uses when it was finished if that came after the sale", () => {
    expect(settledAnchorMs({
      id: "s",
      sold_at: "2026-09-10T00:00:00Z",
      settled_at: "2026-09-12T00:00:00Z",
    })).toBe(ms("2026-09-12T00:00:00Z"));
  });

  it("falls back to the last edit when neither date is known", () => {
    expect(settledAnchorMs({ id: "s", updated_at: "2026-09-20T00:00:00Z" })).toBe(ms("2026-09-20T00:00:00Z"));
    expect(soldAtMs({ sold_at: null })).toBe(0);
  });
});

describe("compactRelativeTime", () => {
  const now = ms("2026-11-06T12:00:00Z");
  const ago = (seconds: number) => new Date(now - seconds * 1000).toISOString();

  it.each([
    [10, "now"],
    [44, "now"],
    [60, "1m"],
    [59 * 60, "59m"],
    [3 * 3600, "3h"],
    [2 * 86_400, "2d"],
    [14 * 86_400, "2w"],
    [60 * 86_400, "2mo"],
    [400 * 86_400, "1y"],
  ])("labels %i seconds ago as %s", (seconds, label) => {
    expect(compactRelativeTime(ago(seconds), now)).toBe(label);
  });

  it("says now for a clock slightly ahead, and nothing without a date", () => {
    expect(compactRelativeTime(new Date(now + 5000).toISOString(), now)).toBe("now");
    expect(compactRelativeTime(null, now)).toBe("");
    expect(compactRelativeTime("garbage", now)).toBe("");
  });
});

describe("liveMarketplaces", () => {
  it("joins Vendoo's marketplaces with Anteroom's own, once each", () => {
    expect(liveMarketplaces({ vendoo_marketplaces: ["ebay", "depop"], direct_marketplaces: ["ebay", "mercari"] }))
      .toEqual(["ebay", "depop", "mercari"]);
  });

  it("drops what Anteroom delisted, folding Vestiaire's API id", () => {
    expect(liveMarketplaces({
      vendoo_marketplaces: ["ebay", "vestiaireApi", "poshmark"],
      removed_marketplaces: ["vestiaire", "ebay"],
    })).toEqual(["poshmark"]);
  });
});

describe("partialSale", () => {
  it("notes units sold on an active multi-quantity item", () => {
    expect(partialSale({ units_sold: 1, quantity: 3 }, "active")).toBe("1 of 3 sold");
  });

  it("says nothing once every unit sold, before any sold, or off the Active tab", () => {
    expect(partialSale({ units_sold: 3, quantity: 3 }, "active")).toBeNull();
    expect(partialSale({ units_sold: 0, quantity: 3 }, "active")).toBeNull();
    expect(partialSale({ units_sold: 1, quantity: 3 }, "sold")).toBeNull();
  });
});

describe("sidebar search", () => {
  const listings: Listing[] = [
    { id: "1", title: "Levi's 501 Jeans", sku: "LV-501", status: "active", vendoo_labels: ["Bin 4"], vendoo_marketplaces: ["ebay"] },
    { id: "2", title: null, status: "draft" },
    {
      id: "3",
      title: "Patagonia Fleece",
      sku: "PAT-FL",
      status: "active",
      vendoo_marketplaces: ["poshmark"],
      removed_marketplaces: ["poshmark"],
    },
    { id: "4", title: "Coach Bag", status: "draft", direct_marketplaces: ["mercari"] },
  ];

  // The sidebar trims and lowercases what the seller types before matching.
  const search = (needle: string) => filterListings(listings, needle, DEFAULT_LISTING_FILTERS).map((l) => l.id);

  it("finds a listing by part of its title, SKU or label", () => {
    expect(search("501")).toEqual(["1"]);
    expect(search("pat-")).toEqual(["3"]);
    expect(search("bin 4")).toEqual(["1"]);
  });

  it("finds an untitled draft by the name the sidebar shows", () => {
    expect(search("untitled")).toEqual(["2"]);
  });

  it("expects the needle already lowercased", () => {
    expect(matchesSearch(listings[0], "LEVI")).toBe(false);
    expect(matchesSearch(listings[0], "levi")).toBe(true);
  });

  it("returns nothing for a query nothing matches", () => {
    expect(search("zzz")).toEqual([]);
  });

  it("files a delisted-everywhere item under Draft and an Anteroom publish under Active", () => {
    const tab = (status: "draft" | "active") =>
      filterListings(listings, "", { ...DEFAULT_LISTING_FILTERS, status }).map((l) => l.id);
    expect(inventoryStatus(listings[2])).toBe("draft");
    expect(tab("draft")).toEqual(["2", "3"]);
    expect(tab("active")).toEqual(["1", "4"]);
  });

  it("counts marketplace rows from where items are actually live", () => {
    expect(marketplaceCounts(listings)).toEqual({ ebay: 1, mercari: 1 });
    expect(notListedCount(listings)).toBe(2);
  });
});

describe("quantityLabel", () => {
  it("shows how many units an unsold multi-quantity item has", () => {
    expect(quantityLabel({ units_sold: 0, quantity: 3 }, "active")).toBe("Qty 3");
    expect(quantityLabel({ quantity: 2 }, "draft")).toBe("Qty 2");
  });

  it("stays quiet for one unit, once units sell (partialSale says it) and for sold items", () => {
    expect(quantityLabel({ quantity: 1 }, "active")).toBeNull();
    expect(quantityLabel({ quantity: null }, "active")).toBeNull();
    expect(quantityLabel({ units_sold: 1, quantity: 3 }, "active")).toBeNull();
    expect(quantityLabel({ quantity: 3 }, "sold")).toBeNull();
  });
});

describe("listingRowPrice", () => {
  it("shows the asking price on drafts and active items", () => {
    expect(listingRowPrice({ price: 24 }, "active")).toBe("$24");
    expect(listingRowPrice({ price: 18.5 }, "draft")).toBe("$18.50");
    expect(listingRowPrice({ price: 1250 }, "active")).toBe("$1,250");
  });

  it("hides a missing or zero price, and the asking price of a sold item", () => {
    expect(listingRowPrice({ price: null }, "active")).toBeNull();
    expect(listingRowPrice({ price: 0 }, "draft")).toBeNull();
    expect(listingRowPrice({ price: 24 }, "sold")).toBeNull();
  });
});
