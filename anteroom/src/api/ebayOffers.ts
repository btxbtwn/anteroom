export interface EbayWaterfallStep {
  discount_percent: number;
  delay_minutes: number;
}

export interface EbayOfferListing {
  conversationId: string;
  listingId: string;
  title: string;
  ruleId: string | null;
  enabled: boolean;
  discountPercent: number;
  minimumPrice: string;
  waterfallSteps?: EbayWaterfallStep[] | null;
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
  waterfallStep?: number | null;
  eligible?: boolean;
  state: "preview" | "skipped" | "enabled" | "sending" | "sent" | "failed" | "unknown" | "checked";
  note: string | null;
  recipientCount?: number;
  createdAt?: string;
  expiresAt?: string;
}

export interface EbayOffersStatus {
  connected: boolean;
  allListings: {
    enabled: boolean;
    discountPercent: number;
    minimumPrice: string;
    waterfallSteps?: EbayWaterfallStep[];
  };
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
