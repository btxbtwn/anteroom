export type DelistMarketplace = "ebay" | "etsy" | "poshmark" | "mercari" | "depop" | "sellwild";
export interface MarketplaceListing {
  url: string | null;
  listing_id: string | null;
  source: "anteroom" | "vendoo" | "seller" | null;
  sold_here: boolean;
  state: "live" | "deleting" | "deleted" | "failed";
  error?: string | null;
  /** The marketplace's own words when a delist failed. */
  marketplace_message?: string | null;
  at?: string | null;
}
/** The seller's correction to what Anteroom knows; it never changes the marketplace. */
export type MarketplaceMark = "listed" | "sold" | "not_listed";
export type MarketplaceListings = Partial<Record<DelistMarketplace, MarketplaceListing>>;
