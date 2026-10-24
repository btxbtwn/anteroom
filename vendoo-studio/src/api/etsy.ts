export interface EtsyMoney {
  amount: string;
  currency: string;
}

export interface EtsyShop {
  id: string;
  name: string;
  title: string;
  url: string | null;
  activeListings: number | null;
  soldItems: number | null;
  favorites: number | null;
  reviewCount: number | null;
  rating: number | null;
  onVacation: boolean;
}

export interface EtsyPage<T> {
  rows: T[];
  total: number;
  nextOffset: number | null;
}

export interface EtsyOrder {
  id: string;
  status: string;
  paid: boolean;
  shipped: boolean;
  needsShipping: boolean;
  createdAt: string | null;
  total: EtsyMoney | null;
  items: { id: string; title: string; quantity: number | null; sku: string }[];
}

export interface EtsyOrders extends EtsyPage<EtsyOrder> {
  summary: {
    paidOrders: number;
    needsShipping: number;
    paidTotals: EtsyMoney[];
    missingTotals: number;
  };
}

export type EtsyOrderStatus = "all" | "paid" | "unshipped";
export type EtsyListingState = "active" | "draft" | "inactive" | "sold_out" | "expired" | "removed";

export interface EtsyListing {
  id: string;
  title: string;
  state: string;
  url: string | null;
  price: EtsyMoney | null;
  quantity: number | null;
  favorites: number | null;
  createdAt: string | null;
}
