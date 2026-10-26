/** Direct eBay and Etsy connections, through Anteroom's relay. */
export type DirectMarketplace = "ebay" | "etsy";

export interface DirectAccountStatus {
  connected: boolean;
  /** eBay username or Etsy shop name. */
  label: string | null;
  connectedAt: number | null;
  /** A sign-in tab is open and Anteroom is waiting for it. */
  pending: boolean;
  authorizeUrl: string | null;
  error: string | null;
}

export interface DirectConnectionsStatus {
  accounts: Record<DirectMarketplace, DirectAccountStatus>;
  relay: {
    running: boolean;
    connected: boolean;
    lastError: string | null;
    lastEventAt: number | null;
  };
}

export type MarketplaceSaleState = "new" | "dismissed";

export interface SaleDetectionStatus {
  ok: boolean;
  enabled: Record<string, boolean>;
  checks: Record<string, string>;
  /** When a sale is recorded, delist the other marketplaces. Off unless the seller turns it on. */
  autodelist: boolean;
}

/** A sale a marketplace reported, waiting for the seller. */
export interface MarketplaceSale {
  id: string;
  marketplace: string;
  orderId: string;
  orderStatus: "paid" | "partially_refunded" | "cancelled" | "refunded" | "cancellation_pending";
  listingId: string | null;
  quantity: number | null;
  stockStatus: "remaining" | "sold_out" | "unknown";
  remainingQuantity: number | null;
  state: MarketplaceSaleState;
  soldAt: string | null;
  receivedAt: string | null;
  conversationId: string | null;
  title: string | null;
  /** Other marketplace listings still present, from direct records and saved links. */
  listedElsewhere: string[];
  /** Optional link back to an item originally imported from Vendoo. */
  vendooUrl: string | null;
}
