import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it } from "vitest";
import type { SaleEvent, SaleTotals } from "../api/saleCalendar";
import { SaleCalendar } from "./SaleCalendar";

function calendar(count: number, days: number, options: { missingPrice?: boolean; selectedItem?: boolean } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } });
  const date = new Date().toISOString().slice(0, 10);
  const totals: SaleTotals = { count, revenue: count * 10, profit: count * 5, profit_known: count, revenue_known: options.missingPrice ? count - 1 : count };
  const event: SaleEvent = {
    id: "fixture", title: "Fixture recorded sale", marketplace: "ebay", start_date: date, end_date: date, timezone: "UTC", notes: "", status: "ran", items: options.selectedItem ? [{ id: "item", title: "Fixture item", price: 10, cost: 2, category: "", marketplaces: ["ebay"], listed_at: null, estimated_profit: 5 }] : [], discount_percent: null, fee_percent: null, shipping_cost: null, minimum_profit: null,
    result: { through: date, scope: "marketplace", after_days: days, after: totals, selected: totals, marketplace: totals, comparison: totals, comparison_start: date, comparison_end: date, comparison_unavailable: null, ongoing: false },
  };
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  client.setQueryData(["sale-calendar", timezone], { timezone, events: [event], items: [], patterns: [] });
  try { return renderToStaticMarkup(<QueryClientProvider client={client}><SaleCalendar marketplace="ebay" onOpenListing={() => {}} /></QueryClientProvider>); }
  finally { client.clear(); }
}

it("uses singular sales and day wording for one recorded sale and one day", () => {
  const html = calendar(1, 1);
  expect(html).not.toContain("1 marketplace sales");
  expect(html).toContain("1 marketplace sale");
  expect(html).toContain("Whole marketplace: 1 sale");
  expect(html).toContain("In the 1 day after the event");
});

it("keeps plural sales and days for larger counts", () => {
  const html = calendar(2, 2);
  expect(html).toContain("2 marketplace sales");
  expect(html).toContain("Whole marketplace: 2 sales");
  expect(html).toContain("In the 2 days after the event");
});

it("uses singular recovery copy when one sale has no price", () => {
  expect(calendar(1, 1, { missingPrice: true })).toContain("1 sale has no recorded price");
});

it("uses a singular selected item for a one-item event", () => {
  expect(calendar(1, 1, { selectedItem: true })).toContain("1/1 selected item sold");
});

it("counts one included item and one event on a day in the singular", () => {
  const html = calendar(1, 1, { selectedItem: true });
  expect(html).toContain(" · 1 item<");
  expect(html).not.toContain("1 items");
  expect(html).toContain(", 1 sale event\"");
  expect(html).not.toContain("1 sale events");
});
