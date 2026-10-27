import { describe, expect, it } from "vitest";
import {
  DEFAULT_LISTING_FILTERS,
  compactCount,
  activeFilterCount,
  filterListings,
  labelOptions,
  listingStatusTab,
  marketplaceOptions,
  matchesSearch,
  sortListings,
  statusCounts,
  isStale,
  labelCounts,
  listedDaysAgo,
  marketplaceCounts,
  notListedCount,
  notListedOnCounts,
  staleCounts,
  unsentCount,
  toggleValue,
  type FilterableListing,
} from "./inventoryFilters";

const tee: FilterableListing = {
  title: "Nike Tee Black M",
  status: "active",
  sku: "NIKE-TEE-M",
  price: 24,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-18T00:00:00Z",
  vendoo_labels: ["A19", "To List"],
  vendoo_marketplaces: ["ebay", "poshmark"],
  vendoo_listed_at: "2026-06-20T00:00:00Z",
};
const boots: FilterableListing = {
  title: "Doc Martens 1460",
  status: "sold",
  sku: "DOC-1460-9",
  price: 120,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-09-19T00:00:00Z",
  vendoo_labels: ["Vintage"],
  vendoo_marketplaces: ["depop"],
  vendoo_listed_at: "2026-01-05T00:00:00Z",
};
const draft: FilterableListing = {
  title: "Untitled",
  status: "draft",
  created_at: "2026-09-10T00:00:00Z",
  updated_at: "2026-09-10T00:00:00Z",
  vendoo_labels: [],
  vendoo_marketplaces: [],
};
const inProgress: FilterableListing = {
  ...draft,
  title: "Generating listing",
  status: "in_progress",
};
const inventory = [tee, boots, draft];

const withFilters = (overrides: Partial<typeof DEFAULT_LISTING_FILTERS>) => ({
  ...DEFAULT_LISTING_FILTERS,
  ...overrides,
});

describe("matchesSearch", () => {
  it("matches title and SKU, case-insensitively", () => {
    expect(matchesSearch(tee, "nike tee")).toBe(true);
    expect(matchesSearch(tee, "nike-tee-m")).toBe(true);
    expect(matchesSearch(tee, "martens")).toBe(false);
  });

  it("matches the labels a listing wears", () => {
    expect(matchesSearch(tee, "a19")).toBe(true);
    expect(matchesSearch(boots, "a19")).toBe(false);
  });

  it("keeps everything for an empty query", () => {
    expect(matchesSearch(draft, "")).toBe(true);
  });
});

describe("filterListings", () => {
  it("keeps the whole inventory by default", () => {
    expect(filterListings(inventory, "", DEFAULT_LISTING_FILTERS)).toEqual(inventory);
  });

  it("filters by status tab", () => {
    expect(filterListings(inventory, "", withFilters({ status: "sold" }))).toEqual([boots]);
  });

  it("keeps an in-progress listing in the Draft tab", () => {
    expect(listingStatusTab("in_progress")).toBe("draft");
    expect(filterListings([...inventory, inProgress], "", withFilters({ status: "draft" }))).toEqual([
      draft,
      inProgress,
    ]);
  });

  it("matches any selected marketplace", () => {
    expect(filterListings(inventory, "", withFilters({ marketplaces: ["depop", "ebay"] }))).toEqual([tee, boots]);
    expect(filterListings(inventory, "", withFilters({ marketplaces: ["etsy"] }))).toEqual([]);
  });

  it("matches any selected label, ignoring case", () => {
    expect(filterListings(inventory, "", withFilters({ labels: ["to list"] }))).toEqual([tee]);
  });

  it("shows only unlisted items when no marketplace is picked", () => {
    expect(filterListings(inventory, "", withFilters({ notListed: true }))).toEqual([draft]);
  });

  it("shows what is missing from any picked marketplace", () => {
    expect(filterListings(inventory, "", withFilters({ notListed: true, marketplaces: ["ebay"] }))).toEqual([
      boots,
      draft,
    ]);
    expect(filterListings(inventory, "", withFilters({ notListed: true, marketplaces: ["ebay", "poshmark"] }))).toEqual([
      boots,
      draft,
    ]);
    expect(filterListings(inventory, "", withFilters({ notListed: true, marketplaces: ["ebay", "depop"] }))).toEqual(
      inventory,
    );
  });

  it("keeps the other filters working under Not listed", () => {
    expect(
      filterListings(inventory, "", withFilters({ notListed: true, marketplaces: ["depop"], labels: ["A19"] })),
    ).toEqual([tee]);
    expect(filterListings(inventory, "", withFilters({ notListed: true, labels: ["A19"] }))).toEqual([]);
  });

  it("combines search with the filters", () => {
    expect(filterListings(inventory, "doc", withFilters({ status: "sold" }))).toEqual([boots]);
    expect(filterListings(inventory, "doc", withFilters({ status: "active" }))).toEqual([]);
  });
});

describe("staleness", () => {
  const now = Date.parse("2026-09-20T00:00:00Z");

  it("measures the days since a listing went live", () => {
    expect(listedDaysAgo(tee, now)).toBe(92);
    expect(listedDaysAgo(draft, now)).toBeNull();
  });

  it("counts a listing stale once it has sat unsold past the cutoff", () => {
    expect(isStale(tee, 90, now)).toBe(true);
    expect(isStale(tee, 120, now)).toBe(false);
    // Sold is a finished story, and a draft never went live, so neither is stale.
    expect(isStale(boots, 30, now)).toBe(false);
    expect(isStale(draft, 30, now)).toBe(false);
    // No cutoff keeps everything, including the listings without a date.
    expect(isStale(draft, 0, now)).toBe(true);
  });

  it("filters and sorts by the listing date", () => {
    expect(filterListings(inventory, "", withFilters({ staleDays: 90 }), now)).toEqual([tee]);
    expect(filterListings(inventory, "", withFilters({ staleDays: 365 }), now)).toEqual([]);
    const fresh = { ...tee, title: "Fresh", vendoo_listed_at: "2026-09-18T00:00:00Z" };
    // The draft has no listing date, so it sorts last whichever end is asked for.
    expect(sortListings([draft, tee, fresh], "stalest").map((l) => l.title)).toEqual([
      "Nike Tee Black M",
      "Fresh",
      "Untitled",
    ]);
    expect(sortListings([draft, tee, fresh], "freshest").map((l) => l.title)).toEqual([
      "Fresh",
      "Nike Tee Black M",
      "Untitled",
    ]);
  });

  it("ages a listing from its oldest live marketplace", () => {
    const bulkRelisted = {
      ...tee,
      vendoo_listed_at: "2026-09-18T00:00:00Z",
      vendoo_marketplaces: ["ebay", "sellwild"],
      vendoo_listed_dates: { ebay: "2026-01-05T00:00:00Z", sellwild: "2026-09-18T00:00:00Z" },
    };
    expect(isStale(bulkRelisted, 90, now)).toBe(true);
    // A marketplace the item sold on is finished; only live copies age it.
    const soldOnEbay = { ...bulkRelisted, vendoo_sold_dates: { ebay: "2026-02-01T00:00:00Z" } };
    expect(isStale(soldOnEbay, 90, now)).toBe(false);
  });

  it("counts the staleness cutoff as a filter", () => {
    expect(activeFilterCount(withFilters({ staleDays: 60 }))).toBe(1);
  });
});

describe("sortListings", () => {
  it("leaves the order alone for recent activity", () => {
    expect(sortListings(inventory, "recent")).toEqual(inventory);
  });

  it("sorts by date created and modified in both directions", () => {
    expect(sortListings(inventory, "created_desc")).toEqual([draft, tee, boots]);
    expect(sortListings(inventory, "created_asc")).toEqual([boots, tee, draft]);
    expect(sortListings(inventory, "modified_desc")).toEqual([boots, tee, draft]);
    expect(sortListings(inventory, "modified_asc")).toEqual([draft, tee, boots]);
  });

  it("sorts alphabetically and by price", () => {
    expect(sortListings(inventory, "title_asc")).toEqual([boots, tee, draft]);
    expect(sortListings(inventory, "title_desc")).toEqual([draft, tee, boots]);
    expect(sortListings(inventory, "price_desc")).toEqual([boots, tee, draft]);
    expect(sortListings(inventory, "price_asc")).toEqual([tee, boots, draft]);
  });

  it("sorts by SKU and leaves listings without one at the end", () => {
    expect(sortListings(inventory, "sku_asc")).toEqual([boots, tee, draft]);
    expect(sortListings(inventory, "sku_desc")).toEqual([tee, boots, draft]);
  });
});

describe("filter options", () => {
  it("counts each status tab", () => {
    expect(statusCounts(inventory)).toEqual({ all: 3, active: 1, sold: 1, draft: 1 });
  });

  it("counts an in-progress listing as a draft", () => {
    expect(statusCounts([...inventory, inProgress])).toEqual({ all: 4, active: 1, sold: 1, draft: 2 });
  });

  it("counts a Vendoo draft Anteroom published as listed", () => {
    const jacket: FilterableListing = {
      ...draft,
      title: "Columbia XL",
      direct_marketplaces: ["depop", "ebay"],
      unsent_edits: true,
    };
    expect(statusCounts([jacket])).toMatchObject({ all: 1, active: 1, draft: 1 });
    expect(filterListings([jacket], "", withFilters({ status: "active", marketplaces: ["depop"] }))).toEqual([jacket]);
    expect(filterListings([jacket], "", withFilters({ notListed: true }))).toEqual([]);
    expect(marketplaceCounts([jacket])).toMatchObject({ depop: 1, ebay: 1 });
    const formerlyFailed = { ...jacket, status: "failed" };
    expect(filterListings([formerlyFailed], "", withFilters({ status: "active" }))).toEqual([formerlyFailed]);
    expect(filterListings([formerlyFailed], "", withFilters({ status: "failed" }))).toEqual([]);
  });

  it("lists every label once, sorted", () => {
    expect(labelOptions([tee, boots, { vendoo_labels: ["a19"] }])).toEqual(["A19", "To List", "Vintage"]);
  });

  it("keeps Settings' marketplace order and appends the rest", () => {
    expect(marketplaceOptions(inventory, ["poshmark", "ebay", "mercari"])).toEqual(["poshmark", "ebay", "depop"]);
    // Asking what is missing from a marketplace needs the ones nothing sits on yet.
    expect(marketplaceOptions(inventory, ["poshmark", "ebay", "mercari"], true)).toEqual([
      "poshmark",
      "ebay",
      "mercari",
      "depop",
    ]);
  });

  it("counts what is filtering the list", () => {
    expect(activeFilterCount(DEFAULT_LISTING_FILTERS)).toBe(0);
    expect(activeFilterCount(withFilters({ status: "sold", labels: ["A19"], notListed: true }))).toBe(3);
    // "Not listed on eBay" is one narrowing; a second marketplace is a second.
    expect(activeFilterCount(withFilters({ notListed: true, marketplaces: ["ebay"] }))).toBe(1);
    expect(activeFilterCount(withFilters({ notListed: true, marketplaces: ["ebay", "etsy"] }))).toBe(2);
  });

  it("counts what each marketplace and label row would keep", () => {
    expect(marketplaceCounts(inventory)).toEqual({ ebay: 1, poshmark: 1, depop: 1 });
    expect(labelCounts(inventory)).toEqual({ a19: 1, "to list": 1, vintage: 1 });
    // The draft carries no marketplaces, so it is already one of the unlisted.
    expect(notListedCount([...inventory, { title: "Nothing yet" }])).toBe(2);
    expect(notListedOnCounts(inventory, ["ebay", "depop", "vinted"])).toEqual({ ebay: 2, depop: 2, vinted: 3 });
  });

  it("counts what each staleness cutoff would keep", () => {
    // 2026-09-20: the tee went live 92 days back, the sold boots 258.
    const now = Date.parse("2026-09-20T00:00:00Z");
    expect(staleCounts(inventory, now)).toEqual({ 30: 1, 60: 1, 90: 1 });
    expect(Object.keys(staleCounts(inventory, now, 45))).toEqual(["30", "45", "60", "90"]);
    expect(staleCounts([{ ...tee, status: "active", vendoo_listed_at: "2026-09-01T00:00:00Z" }], now)).toEqual({
      30: 0,
      60: 0,
      90: 0,
    });
  });

  it("shortens a count that would push the status tabs into each other", () => {
    expect(compactCount(0)).toBe("0");
    expect(compactCount(629)).toBe("629");
    expect(compactCount(999)).toBe("999");
    expect(compactCount(1121)).toBe("1.1k");
    expect(compactCount(2000)).toBe("2k");
    expect(compactCount(12345)).toBe("12.3k");
    expect(compactCount(120000)).toBe("120k");
  });

  it("toggles a value in and out", () => {
    expect(toggleValue(["ebay"], "etsy")).toEqual(["ebay", "etsy"]);
    expect(toggleValue(["ebay", "etsy"], "ebay")).toEqual(["etsy"]);
  });
});

describe("unsent edits", () => {
  const edited: FilterableListing = { ...tee, unsent_edits: true };
  const sent: FilterableListing = { ...tee, unsent_edits: false };

  it("keeps only the listings Vendoo has not been given yet", () => {
    const rows = filterListings([edited, sent], "", { ...DEFAULT_LISTING_FILTERS, unsent: true });
    expect(rows).toEqual([edited]);
  });

  it("counts them for the filter row", () => {
    expect(unsentCount([edited, sent, edited])).toBe(2);
  });

  it("is off by default, so both still show", () => {
    expect(filterListings([edited, sent], "", DEFAULT_LISTING_FILTERS)).toHaveLength(2);
  });

  it("shows a live listing with unsent edits under Draft as well as Active", () => {
    const draftTab = { ...DEFAULT_LISTING_FILTERS, status: "draft" as const };
    const activeTab = { ...DEFAULT_LISTING_FILTERS, status: "active" as const };
    expect(filterListings([edited, sent], "", draftTab)).toEqual([edited]);
    expect(filterListings([edited, sent], "", activeTab)).toEqual([edited, sent]);
  });

  it("counts it on the Draft tab without counting a draft twice", () => {
    const draft: FilterableListing = { ...tee, status: "draft", unsent_edits: true };
    expect(statusCounts([edited, sent, draft])).toMatchObject({ all: 3, active: 2, draft: 2 });
  });

  it("leaves a sold item under Sold alone", () => {
    const sold: FilterableListing = { ...tee, status: "sold", unsent_edits: true };
    const draftTab = { ...DEFAULT_LISTING_FILTERS, status: "draft" as const };
    expect(filterListings([sold], "", draftTab)).toEqual([]);
    expect(statusCounts([sold]).draft).toBeUndefined();
  });
});
