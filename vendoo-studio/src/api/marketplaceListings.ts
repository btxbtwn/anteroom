export type DelistMarketplace = "ebay" | "etsy" | "poshmark" | "mercari" | "depop" | "sellwild" | "vinted";
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
/** Where a sale can be recorded: a marketplace, or anywhere else (in person, Instagram). */
export type SaleMarketplace = DelistMarketplace | "other";
/** Mark as sold: the sale and its amounts, as decimal strings. Recording it never delists anything. */
export interface SellerSale {
  marketplace: SaleMarketplace;
  sold_on: string;
  quantity: number;
  currency: "USD";
  item_revenue?: string | null;
  shipping_credit?: string | null;
  fees?: string | null;
  shipping_cost?: string | null;
  request_id: string;
}
export interface SellerSaleResult {
  status: string;
  units_sold: number;
  quantity: number;
  listings: MarketplaceListings;
  /** Live listings elsewhere the seller may delist through the confirmed Delist. Never delisted here. */
  delist_offer: DelistMarketplace[];
}
