import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AdSpendEntry } from "../api/adSpend";
import type { InventoryAnalytics } from "../api/types";
import { EtsyMarketingPanel } from "./etsy/EtsyWorkspace";
import { AdSpend } from "./AdSpend";
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

/** The page reads only stale stock and ad totals from the analytics payload. */
type MarketingData = Pick<InventoryAnalytics, "stale" | "ads">;

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

const adEntry: AdSpendEntry = {
  id: "posh-week", marketplace: "poshmark", start_date: "2026-09-01", end_date: "2026-09-07",
  spend: 15, clicks: 60, orders: 1, revenue: 45, notes: "", roas: 3, cost_per_click: 0.25,
};

function setup(payload: MarketingData = data, adEntries: AdSpendEntry[] = [], calendar: SaleCalendarData = {
  events: [], patterns: [], items: [], timezone: "UTC",
}) {
  useMutation.mockReturnValue({ mutate: vi.fn(), isPending: false, isSuccess: false, isError: false });
  useQuery.mockImplementation(({ queryKey }: { queryKey: readonly string[] }) =>
    queryKey[0] === "sale-calendar"
      ? { data: calendar, isError: false }
      : queryKey[0] === "ad-spend" ? { data: adEntries, isError: false }
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
    const html = renderToStaticMarkup(<MarketplacesPage onOpenListing={vi.fn()} />);
    expect(html).toContain(">Marketplaces</h1>");
    expect(html).toContain('aria-label="Marketplaces"');
    expect(html.match(/role="tab"/g)).toHaveLength(5);
    for (const name of ["eBay", "Etsy", "Poshmark", "Depop", "Sellwild"]) {
      expect(html).toContain(`${name}</button>`);
    }
    expect(html).not.toContain(">Marketing</h1>");
    expect(html).toContain("Connect eBay");
  });

  it("opens Etsy’s seller dashboard with its own marketplace tools", () => {
    const html = renderToStaticMarkup(<MarketplacePanel marketplace="etsy" onOpenListing={vi.fn()} />);
    expect(html).toContain("My Etsy shop");
    for (const title of ["Dashboard", "Orders", "Listings", "Marketing"]) expect(html).toContain(`${title}</button>`);
    expect(html).not.toContain("Promoted Closet");
    expect(html).not.toContain("Schedule store email");
  });

  it("keeps the sale calendar and Etsy Ads CSV in Etsy marketing", () => {
    const html = renderToStaticMarkup(<EtsyMarketingPanel onOpenListing={vi.fn()} />);
    expect(html).toContain("Sale calendar");
    expect(html).toContain("Upload Etsy Ads CSV");
    expect(html).toContain('accept=".csv,text/csv"');
    expect(html).not.toContain("Promoted Closet");
  });

  it("keeps the existing Poshmark spend history accessible", () => {
    setup(data, [adEntry]);
    const html = renderToStaticMarkup(<MarketplacePanel marketplace="poshmark" onOpenListing={vi.fn()} />);
    expect(html).toContain("Promoted Closet");
    expect(html).toContain("60 clicks · 1 order · $45 sales · 3× ROAS");
    expect(html).not.toContain("Upload Etsy Ads CSV");
  });
});

describe("marketplace ad spend", () => {
  it("shows the empty state for this marketplace even when another has entries", () => {
    setup(data, [adEntry]);
    const html = renderToStaticMarkup(<AdSpend marketplace="etsy" />);
    expect(html).toContain("No ad spend recorded yet.");
    expect(html).not.toContain('aria-label="Ad spend period"');
    expect(html).not.toContain("60 clicks");
  });

  it("keeps the period picker when this marketplace has no spend in the selected period", () => {
    setup(data, [adEntry]);
    const html = renderToStaticMarkup(<AdSpend marketplace="poshmark" />);
    expect(html).toContain('aria-label="Ad spend period"');
    expect(html).toContain("No ad spend in this period.");
    expect(html).not.toContain("No ad spend recorded yet.");
  });

  it("shows only the selected marketplace’s entries, sales, and profit", () => {
    setup({
      ...data,
      ads: {
        spend: 40, profit: 50, profit_after_ads: 10,
        marketplaces: [
          { id: "poshmark", entries: 1, spend: 15, clicks: 60, orders: 1, revenue: 45, roas: 3,
            cost_per_click: 0.25, sales_revenue: 80, spend_percent: 18.8, profit: 30, profit_after_ads: 15 },
          { id: "etsy", entries: 1, spend: 25, clicks: null, orders: null, revenue: null, roas: null,
            cost_per_click: null, sales_revenue: 0, spend_percent: null, profit: null, profit_after_ads: null },
        ],
      },
    }, [adEntry, { ...adEntry, id: "etsy-week", marketplace: "etsy", spend: 25, notes: "Etsy entry" }]);
    const html = renderToStaticMarkup(<AdSpend marketplace="poshmark" />);
    expect(html).toContain("3× return on ad spend · $0.25 a click · 18.8% of $80 Poshmark sales · $15 Poshmark profit after ads");
    expect(html).toContain("$30 marketplace profit, less $15 of ads");
    expect(html).not.toContain("Etsy entry");
    expect(html).not.toContain("Business profit after ads");
    expect(html).not.toContain("$40");
  });
});

describe("marketplace sale calendar", () => {
  it("shows only this marketplace’s events and suggested windows", () => {
    const event = (marketplace: string): SaleEvent => ({
      id: marketplace, marketplace, title: `${marketplace} sale`, status: "planned",
      start_date: "2026-09-01", end_date: "2026-09-07", timezone: "UTC", notes: "",
      discount_percent: 25, fee_percent: 10, shipping_cost: 0, minimum_profit: 0, items: [], result: null,
    });
    setup(data, [], {
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
