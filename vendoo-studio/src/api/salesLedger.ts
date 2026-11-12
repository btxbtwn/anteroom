const FINANCIAL_FIELDS = ["item_revenue", "shipping_credit", "refunds", "fees", "shipping_cost", "item_cost"] as const;
export type FinancialField = (typeof FINANCIAL_FIELDS)[number];
/** Editable amounts needed to complete a sale record. */
export const SALE_FORM_FIELDS = [
  ["item_revenue", "Sale price"], ["shipping_credit", "Shipping the buyer paid"], ["fees", "Fees"], ["shipping_cost", "Shipping you paid"],
  ["item_cost", "Cost of goods"],
] as const satisfies readonly (readonly [FinancialField, string])[];
export type SaleFormField = (typeof SALE_FORM_FIELDS)[number][0];
export type FinancialEntry = { currency: string } & Record<FinancialField, string | null>;
export interface SalesLedgerRow {
  financials_source?: string;
  missing_sale_data?: string[];
  id: string;
  marketplace: string;
  order_id: string;
  line_id: string;
  listing_id: string | null;
  quantity: number | null;
  order_status: string;
  /** Who reported it: "seller" for Mark as sold, the only kind a seller can delete. */
  source: string;
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
export interface SalesLedgerFilters { start: string; end: string; marketplace?: string; conversation_id?: string; q?: string; attention?: string; sort?: string }
/** Amounts left out keep their saved value. */
export type FinancialSave = { currency: string } & Partial<Record<FinancialField, string | null>> & { expected_revision: number; expected_order_basis: string; sold_on?: string | null };
export interface SalesLedgerPage {
  rows: SalesLedgerRow[];
  page: number;
  has_more: boolean;
  summary: {
    lines: number;
    unmatched: number;
    needs_review: number;
    /** Sales missing any amount, and how many lack each one. */
    missing_records: number;
    missing_counts: { label: string; count: number }[];
    currencies: { currency: string; lines: number; net_known: number; profit_known: number; net_revenue: string | null; profit: string | null }[];
  };
}
export function salesLedgerParams(filters: SalesLedgerFilters, page?: number): string {
  const params = new URLSearchParams({ start: filters.start, end: filters.end });
  if (filters.marketplace) params.set("marketplace", filters.marketplace);
  if (filters.conversation_id) params.set("conversation_id", filters.conversation_id);
  for (const key of ["q", "attention", "sort"] as const) if (filters[key]) params.set(key, filters[key]);
  if (page != null) params.set("page", String(page));
  return params.toString();
}
export interface SalesOverviewFigures {
  count: number;
  revenue: number;
  revenue_known: number;
  profit: number | null;
  profit_known: number;
  fees_known: number;
  median_days: number | null;
  days_known: number;
  sell_through: number | null;
}
/** Figures and charts for one period (whole UTC days; no start means all time) and marketplace. */
export interface SalesOverview {
  start: string | null;
  end: string;
  undated_sales: number;
  current: SalesOverviewFigures;
  previous: (SalesOverviewFigures & { start: string; end: string }) | null;
  weeks: { start: string; count: number; revenue: number; revenue_known: number }[];
  weeks_truncated: boolean;
  marketplaces: { id: string; count: number; revenue: number; revenue_known: number }[];
}
export function salesOverviewParams(filters: { start: string | null; end: string; marketplace?: string }): string {
  const params = new URLSearchParams({ end: filters.end });
  if (filters.start) params.set("start", filters.start);
  if (filters.marketplace) params.set("marketplace", filters.marketplace);
  return params.toString();
}
