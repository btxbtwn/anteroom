export interface AdMarketplaceTotals {
  id: string;
  entries: number;
  spend: number;
  clicks: number | null;
  orders: number | null;
  revenue: number | null;
  roas: number | null;
  cost_per_click: number | null;
  /** Every recorded sale on the marketplace in the range, with or without ads. */
  sales_revenue: number;
  spend_percent: number | null;
  /** Profit on the marketplace's sales with a recorded cost, before this spend. */
  profit: number | null;
  profit_after_ads: number | null;
}

export interface AnalyticsAds {
  spend: number;
  /** Profit across every marketplace in the range, before any ad spend. */
  profit: number | null;
  profit_after_ads: number | null;
  marketplaces: AdMarketplaceTotals[];
}
