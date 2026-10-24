export interface EbayOfferListing {
  conversationId: string;
  listingId: string;
  title: string;
  ruleId: string | null;
  enabled: boolean;
  discountPercent: number;
  minimumPrice: string;
}

export interface EbayOffer {
  id?: string;
  ruleId?: string;
  conversationId?: string;
  listingId?: string;
  title?: string | null;
  currentPrice?: string;
  offerPrice?: string;
  minimumPrice?: string;
  discountPercent?: number;
  eligible?: boolean;
  state: "preview" | "skipped" | "enabled" | "sending" | "sent" | "failed" | "unknown" | "checked";
  note: string | null;
  recipientCount?: number;
  createdAt?: string;
  expiresAt?: string;
}

export interface EbayOffersStatus {
  connected: boolean;
  listings: EbayOfferListing[];
  history: EbayOffer[];
  checkIntervalMinutes: number;
  lastCheck: string | null;
  lastError: string | null;
}

export interface EbayOfferSelection {
  conversation_id: string;
  minimum_price: string;
}
