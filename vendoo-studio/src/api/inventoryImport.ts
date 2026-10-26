export type ImportMarketplace = "ebay" | "etsy";

export interface MarketplaceImportPage {
  page: number;
  total: number | null;
  has_more: boolean;
  rows: {
    id: string;
    title: string;
    price: string | null;
    quantity: number | null;
    reason: string | null;
    conversation_id: string | null;
  }[];
}

export interface MarketplaceImportResult {
  conversation_id: string;
  listing_id: string;
  reused: boolean;
  photo_count: number;
  warnings: string[];
}
