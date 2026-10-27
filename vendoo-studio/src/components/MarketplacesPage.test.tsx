import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { InventoryAnalytics } from "../api/types";
import { SaleCalendar } from "./SaleCalendar";
import { DiscountSuggestions } from "./DiscountSuggestions";
import { MarketplacesPage, MarketplacePanel } from "./MarketplacesPage";
import type { SaleCalendarData, SaleEvent } from "../api/saleCalendar";

const { useQuery, useMutation } = vi.hoisted(() => ({ useQuery: vi.fn(), useMutation: vi.fn() }));
vi.mock("@tanstack/react-query", () => ({
  useQuery,
  keepPreviousData: vi.fn(),
  useMutation,
  useQueryClient: () => ({ setQueryData: vi.fn(), invalidateQueries: vi.fn() }),
}));

/** The page reads only stale stock and sales totals from the analytics payload. */
type MarketingData = Pick<InventoryAnalytics, "stale" | "ads"> & Partial<Pick<InventoryAnalytics, "marketplaces">>;

const data: MarketingData = {
  stale: [
    {
      conversation_id: "old-stock", title: "Older jacket", days_listed: 110, price: 60,
      cost: 8, lowest_price: 10, discount_percent: 40, sale_price: 36,
    },
    {
      conversation_id: "thin-margin", title: "Pricey boots", days_listed: 75, price: 30,
      cost: 20, lowest_price: 25, discount_percent: null, sale_price: null,
    },
  ],
  ads: { spend: 0, profit: 50, profit_after_ads: 50, marketplaces: [] },
};

function setup(payload: MarketingData = data, calendar: SaleCalendarData = {
  events: [], patterns: [], items: [], timezone: "UTC",
}) {
  useMutation.mockReturnValue({ mutate: vi.fn(), isPending: false, isSuccess: false, isError: false });
  useQuery.mockImplementation(({ queryKey }: { queryKey: readonly string[] }) =>
    queryKey[0] === "sale-calendar"
      ? { data: calendar, isError: false }
      : queryKey[0] === "direct-connections" ? { data: { accounts: {
          ebay: { connected: false }, etsy: { connected: true, label: "My Etsy shop" },
        } }, isError: false }
      : queryKey[0] === "etsy-workspace" ? { isPending: true, error: null }
      : { data: payload, isLoading: false, isError: false, isFetching: false },
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-03T12:00:00Z"));
  setup();
});
afterEach(() => vi.useRealTimers());

describe("MarketplacesPage", () => {
  it("groups marketplace tools under one workspace", () => {
    const html = renderToStaticMarkup(<MarketplacesPage marketplace="ebay" onMarketplaceChange={vi.fn()} onOpenListing={vi.fn()} />);
    expect(html).toContain(">Marketplaces</h1>");
    expect(html).toContain('aria-label="Marketplaces"');
    expect(html.match(/role="tab"/g)).toHaveLength(7);
    for (const name of ["eBay", "Etsy", "Poshmark", "Mercari", "Depop", "Sellwild", "Vinted"]) {
      expect(html).toContain(`${name}</button>`);
    }
    expect(html).not.toContain(">Marketing</h1>");
    expect(html).toContain("Connect eBay");
  });

  it("opens Etsy’s seller dashboard with its own marketplace tools", () => {
    const html = renderToStaticMarkup(<MarketplacePanel marketplace="etsy" onOpenListing={vi.fn()} />);
    expect(html).toContain("My Etsy shop");
    for (const title of ["Dashboard", "Orders", "Listings"]) expect(html).toContain(`${title}</button>`);
    expect(html).not.toContain("Marketing</button>");
    expect(html).not.toContain("Sale calendar");
    expect(html).not.toContain("Schedule store email");
  });

  it("shows Poshmark, Depop, and Sellwild as a sales dashboard with no marketing tab", () => {
    setup({ ...data, marketplaces: [
      { id: "depop", label: "depop", count: 3, revenue: 120, profit: 70, profit_known: 2, fees_known: 2, revenue_known: 3,
        days_known: 3, average_price: 40, median_days: 12, margin: 58 },
    ] });
    for (const id of ["poshmark", "depop", "sellwild"] as const) {
      const html = renderToStaticMarkup(<MarketplacePanel marketplace={id} onOpenListing={vi.fn()} />);
      expect(html).toContain('class="ebay-workspace"');
      expect(html).toContain("Sales channel");
      expect(html).not.toContain("Marketing</button>");
      expect(html).not.toContain("Promoted Closet");
      expect(html).toContain("Sales overview");
    }
    const depop = renderToStaticMarkup(<MarketplacePanel marketplace="depop" onOpenListing={vi.fn()} />);
    expect(depop).toContain("$120");
    expect(depop).toContain("$40 average sale");
    expect(depop).toContain("2 of 3 sales have a recorded cost");
    expect(depop).toContain("Typical time to sell: 12 days");
  });
});

describe("marketplace sale calendar", () => {
  it("shows only this marketplace’s events and suggested windows", () => {
    const event = (marketplace: string): SaleEvent => ({
      id: marketplace, marketplace, title: `${marketplace} sale`, status: "planned",
      start_date: "2026-09-01", end_date: "2026-09-07", timezone: "UTC", notes: "",
      discount_percent: 25, fee_percent: 10, shipping_cost: 0, minimum_profit: 0, items: [], result: null,
    });
    setup(data, {
      timezone: "UTC", events: [event("etsy"), event("ebay")], items: [],
      patterns: ["etsy", "ebay"].map((id) => ({
        marketplace: id as "etsy" | "ebay", weeks: 0, sales: 0, history_start: "2026-09-01", history_end: "2026-09-03",
        weekdays: [], suggested_start: "2026-09-05", suggested_end: "2026-09-06", reason: `${id} suggested window`,
        fee_percent: null, shipping_cost: null,
      })),
    });
    const html = renderToStaticMarkup(<SaleCalendar marketplace="etsy" onOpenListing={vi.fn()} />);
    expect(html).toContain("etsy sale");
    expect(html).toContain("etsy suggested window");
    expect(html).not.toContain("ebay sale");
    expect(html).not.toContain("ebay suggested window");
    expect(html).not.toContain("All marketplaces");
  });
});

describe("DiscountSuggestions", () => {
  it("preserves the inventory-wide discount review for Analytics", () => {
    const html = renderToStaticMarkup(<DiscountSuggestions listings={data.stale} onOpenListing={vi.fn()} />);
    expect(html).toContain("Discount deeper");
    expect(html).toContain("40% off → $36");
    expect(html).toContain("lowest $10");
    expect(html).toContain("Keep full price");
  });
});
