import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AnalyticsPage } from "./AnalyticsPage";
import { ledgerMoney, SalesLedger, SalesLedgerSummary } from "./SalesLedger";
import { salesLedgerParams, type SalesLedgerPage } from "../api/salesLedger";

const { useQuery } = vi.hoisted(() => ({ useQuery: vi.fn() }));
vi.mock("@tanstack/react-query", () => ({ useQuery, useQueryClient: () => ({ invalidateQueries: vi.fn() }) }));
const data: SalesLedgerPage = {
  rows: [{ id: "sale", marketplace: "ebay", order_id: "order", line_id: "line", listing_id: "123456789012", quantity: 2,
    order_status: "partially_refunded", alert_state: "dismissed", conversation_id: "coat", title: "Wool coat", sold_at: null,
    received_at: "2026-10-26T00:00:00Z", financials: null, financials_stale: true, financial_revision: 1,
    financials_updated_at: null, order_basis: "a".repeat(64), net_revenue: null, profit: null }],
  page: 1, has_more: false, summary: { lines: 3, unmatched: 1, unrecorded: 1, needs_review: 1,
    currencies: [{ currency: "EUR", lines: 1, net_known: 1, profit_known: 1, net_revenue: "10.0001", profit: "7.0001" },
      { currency: "USD", lines: 1, net_known: 0, profit_known: 0, net_revenue: null, profit: null }] },
};
beforeEach(() => { useQuery.mockReset(); useQuery.mockReturnValue({ data, isPending: false, isFetching: false }); });

describe("local sales records", () => {
  it("opens Analytics on the local ledger without fetching Vendoo analytics", () => {
    const html = renderToStaticMarkup(<AnalyticsPage onOpenListing={vi.fn()} />);
    expect(html).toContain("Export inventory CSV");
    expect(html).toContain("Export sales CSV");
    expect(html).not.toContain("Resync Vendoo data");
    expect(useQuery).toHaveBeenCalledTimes(1);
    expect(useQuery.mock.calls[0][0].queryKey[0]).toBe("sales-ledger");
  });
  it("shows changed orders, quantity and unknown profit with local export links", () => {
    const html = renderToStaticMarkup(<SalesLedger onOpenListing={vi.fn()} />);
    expect(html).toContain("partially refunded");
    expect(html).toContain("quantity 2");
    expect(html).toContain("Profit: Not recorded");
    expect(html).toContain("Order changed or recorded amounts need review");
    expect(html).toContain("/api/sales-ledger/export/sales.csv?start=");
    expect(html).toContain("/api/sales-ledger/export/inventory.csv");
    expect(html).not.toContain("NaN");
  });
  it("keeps currency coverage separate and never rounds recorded decimals", () => {
    const html = renderToStaticMarkup(<SalesLedgerSummary summary={data.summary} />);
    expect(html).toContain("EUR 7.0001");
    expect(html).toContain("known on 0 of 1 lines");
    expect(html).toContain("1 without recorded amounts");
    expect(ledgerMoney("0", "USD")).toBe("USD 0");
    expect(ledgerMoney(null, "USD")).toBe("Not recorded");
  });
  it("encodes filters and keeps pagination out of full-period exports", () => {
    const filters = { start: "2026-10-01", end: "2026-10-26", marketplace: "ebay", conversation_id: "coat&hat" };
    expect(new URLSearchParams(salesLedgerParams(filters)).get("conversation_id")).toBe("coat&hat");
    expect(salesLedgerParams(filters)).not.toContain("page=");
    expect(salesLedgerParams(filters, 2)).toContain("page=2");
  });
});
