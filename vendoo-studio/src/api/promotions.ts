export type PromotionMarketplace = "poshmark" | "mercari" | "depop";
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
  automation_id?: string | null;
  created_at: string | null;
  expires_at: string | null;
  finished_at: string | null;
}

export interface PromotionResult extends Promotion { error_code: string | null; stop: boolean }
export interface PromotionPreview { batch_id: string; marketplace: PromotionMarketplace; action: PromotionAction; rows: Promotion[] }
export type PromotionSupport = Partial<Record<PromotionMarketplace, PromotionAction[]>>;
export interface PromotionHistory { supported: PromotionSupport; awaiting_capture?: PromotionSupport; history: Promotion[] }
export interface PromotionFloor { title: string | null; listing_id: string | null; reason: string | null; floor: string | null }
export interface PromotionPreviewRequest {
  conversation_ids: string[];
  marketplace: PromotionMarketplace;
  action: PromotionAction;
  discount_percent?: number;
  floors?: Record<string, string>;
}

export type ShareOrder = "newest" | "oldest" | "random";
export type ShareSpeed = "slow" | "normal" | "fast";

export interface PromotionAutomationBody {
  marketplace: PromotionMarketplace;
  action: PromotionAction;
  name?: string;
  scope: "all" | "items";
  conversation_ids?: string[];
  excluded_ids?: string[];
  min_days_listed?: number;
  share_order?: ShareOrder;
  speed?: ShareSpeed;
  daily_cap?: number;
  times?: string[];
  timezone?: string;
  discount_percent?: number;
  minimum_price?: string | null;
  min_likes?: number;
  shipping_label?: string | null;
}

export interface PromotionRun {
  id: string;
  started_at: string | null;
  finished_at: string | null;
  sent: number;
  skipped: number;
  failed: number;
  note: string | null;
}

export interface PromotionAutomation extends Required<Omit<PromotionAutomationBody, "shipping_label" | "share_order" | "speed" | "daily_cap" | "timezone" | "discount_percent" | "min_likes">> {
  id: string;
  enabled: boolean;
  available: boolean;
  share_order: ShareOrder | null;
  speed: ShareSpeed | null;
  daily_cap: number | null;
  timezone: string | null;
  discount_percent: number | null;
  min_likes: number | null;
  shipping_label: string | null;
  paused_reason: string | null;
  next_run_at: string | null;
  last_run: PromotionRun | null;
}

export interface PromotionAutomations {
  automations: PromotionAutomation[];
  supported: PromotionSupport;
  awaiting_capture: PromotionSupport;
  speeds: ShareSpeed[];
  orders: ShareOrder[];
  max_daily_cap: number;
  shipping_labels: string[];
}

export interface PromotionCoverageItem {
  conversation_id: string;
  title: string | null;
  listing_id: string;
  listed_days: number | null;
  floor: string | null;
  included: boolean;
  too_new: boolean;
}

export interface PromotionCoverage { items: PromotionCoverageItem[]; included: number }
export interface PromotionActivity { runs: PromotionRun[]; sends: Promotion[] }
