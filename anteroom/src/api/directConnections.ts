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

/** Which eBay account or Etsy shop is connected: changes when another one is connected. */
export function directAccountKey(account: Pick<DirectAccountStatus, "label" | "connectedAt"> | undefined): string {
  return `${account?.label || ""}:${account?.connectedAt || ""}`;
}

export interface DirectConnectionsStatus {
  accounts: Record<DirectMarketplace, DirectAccountStatus>;
  relay: {
    running: boolean;
    connected: boolean;
    lastError: string | null;
    /** A sale event skipped after repeated failures; shown even while connected. */
    skippedEvent: string | null;
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
  /** When a multi-quantity item sells with stock remaining, decrement other marketplaces (and relist Mercari). Off by default. */
  auto_adjust_quantity?: boolean;
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
  /** Why a delist of another marketplace did not confirm, by marketplace. */
  delistErrors?: Record<string, string>;
}

/** A recently sold-out item Anteroom still has listed on another marketplace. */
export interface StillListedItem {
  conversationId: string;
  title: string | null;
  saleId: string;
  soldOn: string;
  soldAt: string | null;
  /** True when a marketplace reported the sale; false for the seller's Mark as sold. */
  detected: boolean;
  /** Anteroom recorded which listings this sale left up elsewhere. */
  pinned: boolean;
  stillListed: {
    marketplace: string;
    listingId: string;
    /** The listing this sale left up; false for one put up after the sale, which Anteroom never delists on its own. */
    followed: boolean;
    state: "live" | "failed" | "deleting";
    error: string | null;
    at: string | null;
    attempts: number;
    url: string | null;
  }[];
}

export interface StillListedResponse {
  items: StillListedItem[];
  /** The seller turned on Delist the other marketplaces: Anteroom keeps retrying. */
  autodelist: boolean;
}

export interface SaleSyncOutcome {
  eventId: string | null;
  marketplace: string;
  action: "delist" | "adjust_quantity" | "mercari_relist";
  status: "succeeded" | "failed" | "pending" | "retrying" | "skipped";
  attempts: number;
  error: string | null;
  listingId: string | null;
  newListingId: string | null;
  previousQuantity: number | null;
  newQuantity: number | null;
  nextRetryAt: string | null;
  updatedAt: string | null;
  retryable: boolean;
}

export interface SaleTrackerEntry {
  saleId: string;
  conversationId: string | null;
  title: string | null;
  sku: string | null;
  soldOn: string;
  orderId: string;
  orderStatus: "paid" | "partially_refunded" | "cancelled" | "refunded" | "cancellation_pending";
  source: string;
  quantity: number | null;
  stockStatus: "remaining" | "sold_out" | "unknown";
  remainingQuantity: number | null;
  soldAt: string | null;
  receivedAt: string | null;
  outcomes: SaleSyncOutcome[];
}

export interface SaleTrackerResponse {
  windowDays: number;
  autodelist: boolean;
  autoAdjustQuantity: boolean;
  summary: {
    totalSales: number;
    succeeded: number;
    failed: number;
    pending: number;
    skipped: number;
  };
  entries: SaleTrackerEntry[];
}
