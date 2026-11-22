import { describe, expect, it } from "vitest";
import {
  DEFAULT_LISTING_FILTERS,
  activeFilterCount,
  cycleLabelFilter,
  filterListings,
  inactiveCount,
  labelFilterState,
  missingSku,
  missingSkuCount,
  priceDriftCount,
  type FilterableListing,
} from "./inventoryFilters";
import { inventoryRows, marketplaceCell } from "./inventoryTable";
import { guardrailWarnings } from "./ListingGuardrails";
import { examplePrice } from "./settings/ListingRulesSettings";
import { NO_TABLE_FILTERS, tableFilterCount } from "./InventoryTableFilters";
import { presetSummary, type ShippingPreset } from "../api/listingRules";

const live = { status: "active", direct_marketplaces: ["ebay"] };
const summer: FilterableListing = { id: "summer", title: "Summer dress", ...live, sku: "A1", vendoo_labels: ["Summer"] };
const consigned: FilterableListing = { id: "consigned", title: "Summer top", ...live, vendoo_labels: ["Summer", "Consigned"] };
const winter: FilterableListing = { id: "winter", title: "Coat", ...live, sku: " ", vendoo_labels: ["Winter"],
  inactive_marketplaces: ["etsy"], price_drift: [{ marketplace: "mercari", expected: 55, price: 70, source: "override" }] };
const all = [summer, consigned, winter];

describe("include and exclude labels", () => {
  it("cycles a label from off to included to excluded and back", () => {
    let filters = DEFAULT_LISTING_FILTERS;
    expect(labelFilterState(filters, "Summer")).toBe("none");
    filters = cycleLabelFilter(filters, "Summer");
    expect(labelFilterState(filters, "Summer")).toBe("include");
    filters = cycleLabelFilter(filters, "Summer");
    expect(labelFilterState(filters, "Summer")).toBe("exclude");
    expect(filters.labels).toEqual([]);
    filters = cycleLabelFilter(filters, "Summer");
    expect(filters).toEqual(DEFAULT_LISTING_FILTERS);
  });

  it("keeps Summer while dropping Consigned", () => {
    const filters = { ...DEFAULT_LISTING_FILTERS, labels: ["Summer"], excludeLabels: ["consigned"] };
    expect(filterListings(all, "", filters).map((listing) => listing.id)).toEqual(["summer"]);
    expect(activeFilterCount(filters)).toBe(2);
  });

  it("excludes alone, case-insensitively", () => {
    const filters = { ...DEFAULT_LISTING_FILTERS, excludeLabels: ["SUMMER"] };
    expect(filterListings(all, "", filters).map((listing) => listing.id)).toEqual(["winter"]);
  });
});

describe("guardrail filters", () => {
  it("narrows to inactive, price drift and missing SKU listings", () => {
    expect(filterListings(all, "", { ...DEFAULT_LISTING_FILTERS, inactive: true })).toEqual([winter]);
    expect(filterListings(all, "", { ...DEFAULT_LISTING_FILTERS, priceDrift: true })).toEqual([winter]);
    expect(filterListings(all, "", { ...DEFAULT_LISTING_FILTERS, missingSku: true }).map((listing) => listing.id)).toEqual(["consigned", "winter"]);
    expect([inactiveCount(all), priceDriftCount(all), missingSkuCount(all)]).toEqual([1, 1, 2]);
    expect(missingSku({ sku: "B2" })).toBe(false);
    expect(activeFilterCount({ ...DEFAULT_LISTING_FILTERS, inactive: true, priceDrift: true, missingSku: true })).toBe(3);
  });

  it("filters the inventory table with the same rules", () => {
    const rows = inventoryRows(all as never[], "", "active", "recent", { ...NO_TABLE_FILTERS, excludeLabels: ["Winter"] });
    expect(rows.map((listing: FilterableListing) => listing.id).sort()).toEqual(["consigned", "summer"]);
    expect(tableFilterCount({ ...NO_TABLE_FILTERS, labels: ["A"], excludeLabels: ["B"], inactive: true })).toBe(3);
  });
});

describe("inactive marketplace cells", () => {
  it("stay listed for every action but read Inactive, not sold or delisted", () => {
    const listing = { direct_records: { etsy: { state: "published", inactive: true, listing_url: "https://etsy.com/listing/9" } } };
    const cell = marketplaceCell(listing as never, "etsy");
    expect(cell).toMatchObject({ kind: "listed", label: "Inactive", inactive: true, url: "https://etsy.com/listing/9" });
    expect(marketplaceCell({ direct_records: { etsy: { state: "published" } } } as never, "etsy").inactive).toBeUndefined();
  });
});

describe("listing warnings", () => {
  it("lists price drift, a missing SKU only when asked, and inactive marketplaces", () => {
    const drift = [{ marketplace: "mercari", expected: 55, price: 70, source: "override" as const }];
    const warnings = guardrailWarnings({ listing: { sku: "" }, drift, warnMissingSku: true, inactive: ["etsy"] });
    expect(warnings.map((warning) => warning.id)).toEqual(["price", "sku", "inactive"]);
    expect(warnings[0].text).toContain("Mercari");
    expect(guardrailWarnings({ listing: { sku: "" }, drift: [], warnMissingSku: false, inactive: [] })).toEqual([]);
    expect(guardrailWarnings({ listing: { sku: "A1" }, drift: [], warnMissingSku: true, inactive: [] })).toEqual([]);
  });
});

describe("pricing rule examples", () => {
  it("round up to the chosen ending, and Poshmark to whole dollars", () => {
    expect(examplePrice(25, { percent: 10, fixed: 0, rounding: "none" }, "ebay")).toBe("$27.50");
    expect(examplePrice(25, { percent: 10, fixed: 0, rounding: "99" }, "ebay")).toBe("$27.99");
    expect(examplePrice(25, { percent: 0, fixed: 0, rounding: "99" }, "ebay")).toBe("$25.99");
    expect(examplePrice(25, { percent: 10, fixed: 0, rounding: "whole" }, "mercari")).toBe("$28");
    expect(examplePrice(25, { percent: 1, fixed: 0, rounding: "none" }, "poshmark")).toBe("$26");
    expect(examplePrice(5, { percent: 0, fixed: -10, rounding: "none" }, "ebay")).toBe("—");
  });
});

describe("shipping preset summary", () => {
  it("names the box, the weight and how many marketplaces it sets", () => {
    const preset: ShippingPreset = { id: "a", name: "Shoes", usage_condition: "", length: 14, width: 10, height: 6,
      weight_lb: 3, weight_oz: 4, marketplaces: { depop: { parcelSize: "Large" } } };
    expect(presetSummary(preset)).toBe("14×10×6 in · 3 lb 4 oz · 1 marketplace");
    expect(presetSummary({ ...preset, length: null, weight_lb: null, weight_oz: 12, marketplaces: {} })).toBe("12 oz");
  });
});
