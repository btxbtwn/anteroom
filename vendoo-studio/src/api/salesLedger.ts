export const FINANCIAL_FIELDS = ["item_revenue", "shipping_credit", "refunds", "fees", "shipping_cost", "item_cost"] as const;
export type FinancialField = (typeof FINANCIAL_FIELDS)[number];
export type FinancialEntry = { currency: string } & Record<FinancialField, string | null>;
export interface SalesLedgerRow {
  id: string;
  marketplace: string;
  order_id: string;
  line_id: string;
  listing_id: string | null;
  quantity: number | null;
  order_status: string;
  alert_state: string;
  conversation_id: string | null;
  title: string | null;
  sold_at: string | null;
  received_at: string;
  financials: FinancialEntry | null;
  financials_stale: boolean;
  financial_revision: number;
  financials_updated_at: string | null;
  order_basis: string;
  net_revenue: string | null;
  profit: string | null;
}
export interface SalesLedgerFilters { start: string; end: string; marketplace?: string; conversation_id?: string }
export interface FinancialSave extends FinancialEntry { expected_revision: number; expected_order_basis: string }
export interface SalesLedgerPage {
  rows: SalesLedgerRow[];
  page: number;
  has_more: boolean;
  summary: {
    lines: number;
    unmatched: number;
    unrecorded: number;
    needs_review: number;
    currencies: { currency: string; lines: number; net_known: number; profit_known: number; net_revenue: string | null; profit: string | null }[];
  };
}
export function salesLedgerParams(filters: SalesLedgerFilters, page?: number): string {
  const params = new URLSearchParams({ start: filters.start, end: filters.end });
  if (filters.marketplace) params.set("marketplace", filters.marketplace);
  if (filters.conversation_id) params.set("conversation_id", filters.conversation_id);
  if (page != null) params.set("page", String(page));
  return params.toString();
}
