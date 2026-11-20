import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AnalyticsPage } from "./AnalyticsPage";
import { ledgerMoney, SalesLedger, SalesLedgerSummary } from "./SalesLedger";
import { salesLedgerParams, type SalesLedgerPage, type SalesOverview } from "../api/salesLedger";

const { useQuery } = vi.hoisted(() => ({ useQuery: vi.fn() }));
vi.mock("@tanstack/react-query", () => ({ useQuery, useMutation: () => ({ isPending: false, mutate: vi.fn() }), keepPreviousData: (value: unknown) => value, useQueryClient: () => ({ invalidateQueries: vi.fn() }) }));
const data: SalesLedgerPage = {
  rows: [{ id: "sale", marketplace: "ebay", order_id: "order", line_id: "line", listing_id: "123456789012", quantity: 2,
    order_status: "partially_refunded", source: "marketplace", alert_state: "dismissed", conversation_id: "coat", title: "Wool coat", sold_at: null,
    received_at: "2026-10-26T00:00:00Z", financials: null, financials_stale: true, financial_revision: 1,
    financials_updated_at: null, order_basis: "a".repeat(64), net_revenue: null, profit: null }],
  page: 1, has_more: false, summary: { lines: 3, unmatched: 1, needs_review: 1, missing_records: 2,
    missing_counts: [{ label: "shipping the buyer paid", count: 2 }, { label: "shipping you paid", count: 1 }],
    currencies: [{ currency: "EUR", lines: 1, net_known: 1, profit_known: 1, net_revenue: "10.0001", profit: "7.0001" },
      { currency: "USD", lines: 1, net_known: 0, profit_known: 0, net_revenue: null, profit: null }] },
};
const figures = { count: 3, revenue: 120, revenue_known: 3, profit: null, profit_known: 0, fees_known: 0, median_days: 9, days_known: 3, sell_through: 30 };
const overview: SalesOverview = {
  start: "2026-10-06", end: "2026-11-04", undated_sales: 0, current: figures,
  previous: { ...figures, count: 1, revenue: 40, revenue_known: 1, start: "2026-09-06", end: "2026-10-05" },
  weeks: [{ start: "2026-10-26", count: 2, revenue: 80, revenue_known: 2 }, { start: "2026-11-02", count: 1, revenue: 40, revenue_known: 1 }],
  weeks_truncated: false, marketplaces: [{ id: "poshmark", count: 2, revenue: 90, revenue_known: 2 }, { id: "ebay", count: 1, revenue: 30, revenue_known: 1 }],
};
beforeEach(() => {
  useQuery.mockReset();
  useQuery.mockImplementation(({ queryKey }: { queryKey: string[] }) => ({
    data: queryKey[0] === "analytics" ? overview : data, isPending: false, isFetching: false,
  }));
});

describe("local sales records", () => {
  it("opens Analytics on the local ledger without fetching Vendoo analytics", () => {
    const html = renderToStaticMarkup(<AnalyticsPage onOpenListing={vi.fn()} />);
    expect(html).toContain('aria-label="Export CSV"');
    expect(html).toContain('<h1 class="sr-only">Analytics</h1>');
    expect(html.match(/<h1/g)).toHaveLength(1);
    expect(html).not.toContain("Resync Vendoo data");
    const keys = useQuery.mock.calls.map((call) => call[0].queryKey[0]);
    expect(keys).toContain("sales-ledger");
    expect(useQuery.mock.calls.map((call) => call[0].queryKey).filter((key) => key[0] === "analytics").every((key) => key[1] === "overview")).toBe(true);
  });
  it("leads with the period's figures and charts, and says what it can't work out", () => {
    const html = renderToStaticMarkup(<SalesLedger onOpenListing={vi.fn()} />);
    const text = html.replace(/<[^>]+>/g, " ");
    expect(text).toContain("Revenue");
    expect(text).toContain("$120.00");
    expect(text).toContain("Up $80.00 (200%)");
    expect(text).toContain("Last 30 days · All marketplaces");
    expect(text).toContain("Compared with Sep 6 – Oct 5, 2026.");
    expect(text).toContain("Not enough data");
    expect(text).toContain("Items sold per week");
    expect(text).toContain("Poshmark");
    expect(html.indexOf("Sales overview")).toBeLessThan(html.indexOf("Sales records"));
  });
  it("shows changed orders, quantity and unknown profit with local export links", () => {
    const html = renderToStaticMarkup(<SalesLedger onOpenListing={vi.fn()} />);
    expect(html).toContain("partially refunded");
    expect(html).toContain("quantity 2");
    expect(html.replace(/<[^>]+>/g, "")).toContain("Profit: Not recorded");
    expect(html).toContain("Order changed or recorded amounts need review");
    expect(html).not.toContain("NaN");
  });
  it("offers Delete only on sales the seller recorded", () => {
    const marketplaceOnly = renderToStaticMarkup(<SalesLedger onOpenListing={vi.fn()} />);
    expect(marketplaceOnly).not.toContain(">Delete<");
    const seller = { ...data.rows[0]!, id: "mine", source: "seller" };
    useQuery.mockImplementation(({ queryKey }: { queryKey: string[] }) => ({
      data: queryKey[0] === "analytics" ? overview : { ...data, rows: [...data.rows, seller] }, isPending: false, isFetching: false,
    }));
    expect(renderToStaticMarkup(<SalesLedger onOpenListing={vi.fn()} />).match(/>Delete</g)).toHaveLength(1);
  });
  it("keeps currency coverage separate and never rounds recorded decimals", () => {
    const html = renderToStaticMarkup(<SalesLedgerSummary summary={data.summary} />);
    expect(html).toContain("€7.00");
    expect(html).toContain("known on 1 of 1 lines");
    expect(html).not.toContain("USD");
    expect(html).toContain("3 sale records · 1 need review · 1 need an inventory link");
    expect(html).not.toContain("without recorded amounts");
    expect(ledgerMoney("0", "USD")).toBe("$0.00");
    expect(ledgerMoney("68", "USD")).toBe("$68.00");
    expect(ledgerMoney("7.2", "USD")).toBe("$7.20");
    expect(ledgerMoney(null, "USD")).toBe("Not recorded");
  });
  it("says once which amounts are missing, with a filter, and keeps a quiet marker on each row", () => {
    const html = renderToStaticMarkup(<SalesLedger onOpenListing={vi.fn()} />);
    const text = html.replace(/<[^>]+>/g, "");
    expect(text).toContain("2 sales missing sale data");
    expect(text).toContain("shipping the buyer paid (2), shipping you paid (1)");
    expect(text).toContain("Show these sales");
    const row = { ...data.rows[0]!, financials_stale: false, missing_sale_data: ["shipping the buyer paid", "shipping you paid"] };
    useQuery.mockImplementation(({ queryKey }: { queryKey: string[] }) => ({
      data: queryKey[0] === "analytics" ? overview : { ...data, rows: [row, { ...row, id: "second" }] }, isPending: false, isFetching: false,
    }));
    const rows = renderToStaticMarkup(<SalesLedger onOpenListing={vi.fn()} />);
    expect(rows).not.toContain("text-xs text-warning-foreground");
    expect(rows.match(/Missing shipping the buyer paid, shipping you paid/g)).toHaveLength(2);
  });
  it("encodes filters and keeps pagination out of full-period exports", () => {
    const filters = { start: "2026-10-01", end: "2026-10-26", marketplace: "ebay", conversation_id: "coat&hat" };
    expect(new URLSearchParams(salesLedgerParams(filters)).get("conversation_id")).toBe("coat&hat");
    expect(salesLedgerParams(filters)).not.toContain("page=");
    expect(salesLedgerParams(filters, 2)).toContain("page=2");
  });
});
