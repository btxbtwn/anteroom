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

/** A sale eBay or Etsy reported directly, waiting for the seller. */
export interface MarketplaceSale {
  id: string;
  marketplace: DirectMarketplace;
  orderId: string;
  listingId: string | null;
  quantity: number | null;
  state: MarketplaceSaleState;
  soldAt: string | null;
  receivedAt: string | null;
  conversationId: string | null;
  title: string | null;
  /** Marketplaces Vendoo says the item is still listed on. */
  listedElsewhere: string[];
  /** The item in Vendoo, where the seller delists it. */
  vendooUrl: string | null;
}
