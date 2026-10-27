export type DelistMarketplace = "ebay" | "etsy" | "poshmark" | "mercari" | "depop" | "sellwild" | "vinted" | "facebook";
export interface MarketplaceListing {
  url: string | null;
  listing_id: string | null;
  source: "anteroom" | "vendoo" | "seller" | null;
  sold_here: boolean;
  state: "live" | "deleting" | "deleted" | "failed";
  /** False where the seller deletes the listing on the marketplace itself (Facebook). */
  can_delist?: boolean;
  error?: string | null;
  /** The marketplace's own words when a delist failed. */
  marketplace_message?: string | null;
  at?: string | null;
}
/** A Facebook listing as its own page shows it now, read through Chrome. Nothing on Facebook changes. */
export interface FacebookCheck {
  ok: boolean;
  state?: "live" | "pending" | "sold" | "hidden" | "draft" | "not_live";
  /** "other" when the listing is on a different Facebook account than the one signed in to Chrome. */
  owner?: "you" | "other" | null;
  sale_recorded: boolean;
  error?: string;
  error_code?: string;
  marketplace_message?: string | null;
  listings: MarketplaceListings;
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
