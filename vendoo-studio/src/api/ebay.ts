export interface EbayRow {
  id: string;
  title: string;
  subtitle: string;
  status: string;
  amount: string | null;
  fields: Record<string, string | number | null>;
  children: EbayRow[];
}

export interface EbayPageData {
  rows: EbayRow[];
  total?: number;
  nextOffset?: number | null;
}

export interface EbayReview {
  id: string;
  summary: string[];
  expiresAt: string;
  images?: string[];
}

export interface EbayResult {
  id: string;
  state: "sent" | "unknown";
  message: string;
  receiptId?: string;
  imageUrl?: string;
}

export interface EbayListing {
  id: string;
  title: string;
  description: string;
  price: string | null;
  quantity: string;
  status: string;
  type: string;
  sku: string;
  categoryId: string;
}

export type EbayPayload = Record<string, unknown>;

/** The policies and locations on the connected eBay account, by id and name. */
export interface EbayListingOptions {
  fulfillment: { id: string; name: string }[];
  payment: { id: string; name: string }[];
  return: { id: string; name: string }[];
  location: { id: string; name: string }[];
}

/** What new eBay listings use when the account has more than one choice. */
export interface EbayListingDefaults {
  fulfillment_policy_id?: string;
  payment_policy_id?: string;
  return_policy_id?: string;
  merchant_location_key?: string;
}
