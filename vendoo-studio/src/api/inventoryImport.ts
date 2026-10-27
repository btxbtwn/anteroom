export type DirectImportMarketplace = "ebay" | "etsy";
export type ChromeImportMarketplace = "poshmark" | "mercari" | "depop" | "sellwild" | "vinted";
export type ImportMarketplace = DirectImportMarketplace | ChromeImportMarketplace;

export interface MarketplaceImportMatch {
  conversation_id: string;
  title: string;
  score: number;
  price: number | null;
  price_differs: boolean;
}

export interface MarketplaceImportPage {
  page?: number;
  total: number | null;
  has_more: boolean;
  /** Chrome marketplaces page with the marketplace's own cursor. */
  next_cursor?: string | null;
  rows: {
    id: string;
    title: string;
    url?: string;
    price: string | null;
    quantity: number | null;
    reason: string | null;
    conversation_id: string | null;
    /** A local item that looks like this listing, for the seller to review. */
    match?: MarketplaceImportMatch | null;
  }[];
}

export interface MarketplaceImportResult {
  conversation_id: string;
  listing_id: string;
  reused: boolean;
  linked?: boolean;
  photo_count: number;
  warnings: string[];
}
