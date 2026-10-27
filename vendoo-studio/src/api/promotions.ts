export type PromotionMarketplace = "poshmark";
export type PromotionAction = "share" | "offer";
export type PromotionState = "preview" | "skipped" | "sending" | "done" | "failed" | "unknown" | "checked";

export interface ShippingOption { id: string; label: string; min_offer_cents: number | null }

export interface Promotion {
  id: string;
  batch_id: string;
  conversation_id: string | null;
  marketplace: PromotionMarketplace;
  action: PromotionAction;
  listing_id: string;
  title: string | null;
  price: string | null;
  offer_price: string | null;
  floor: string | null;
  discount_percent: number | null;
  like_count: number | null;
  shipping_options: ShippingOption[];
  shipping_discount_id: string | null;
  shipping_discount_label: string | null;
  state: PromotionState;
  note: string | null;
  marketplace_message: string | null;
  created_at: string | null;
  expires_at: string | null;
  finished_at: string | null;
}

export interface PromotionResult extends Promotion { error_code: string | null; stop: boolean }
export interface PromotionPreview { batch_id: string; marketplace: PromotionMarketplace; action: PromotionAction; rows: Promotion[] }
export interface PromotionHistory { supported: Partial<Record<PromotionMarketplace, PromotionAction[]>>; history: Promotion[] }
export interface PromotionFloor { title: string | null; listing_id: string | null; reason: string | null; floor: string | null }
export interface PromotionPreviewRequest {
  conversation_ids: string[];
  marketplace: PromotionMarketplace;
  action: PromotionAction;
  discount_percent?: number;
  floors?: Record<string, string>;
}
