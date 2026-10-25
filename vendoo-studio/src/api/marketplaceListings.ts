export type DelistMarketplace = "ebay" | "etsy" | "poshmark" | "mercari" | "depop" | "sellwild";
export interface MarketplaceListing {
  url: string | null;
  listing_id: string | null;
  source: "anteroom" | "vendoo" | null;
  sold_here: boolean;
  state: "live" | "deleting" | "deleted" | "failed";
  error?: string | null;
  at?: string | null;
}
export type MarketplaceListings = Partial<Record<DelistMarketplace, MarketplaceListing>>;
