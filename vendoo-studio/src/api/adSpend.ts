export type AdMarketplace = "poshmark" | "etsy";

/** What the seller copied from Promoted Closet or Etsy Ads for a stretch of days. */
export interface AdSpendInput {
  marketplace: AdMarketplace;
  start_date: string;
  end_date: string;
  spend: number;
  clicks: number | null;
  /** Orders and revenue the dashboard credits to the ads, not every sale. */
  orders: number | null;
  revenue: number | null;
  notes: string;
}

export interface AdSpendEntry extends AdSpendInput {
  id: string;
  roas: number | null;
  cost_per_click: number | null;
}

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

/** What an Etsy Ads stats CSV upload added. */
export interface EtsyAdsImport {
  /** Days with ad activity; idle days are not recorded. */
  days: number;
  start: string;
  end: string;
  spend: number;
  /** Typed Etsy entries that cover some of the same days, left as they were. */
  overlapping_entries: number;
}
