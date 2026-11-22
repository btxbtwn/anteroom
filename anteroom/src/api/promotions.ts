export type PromotionMarketplace = "poshmark" | "mercari" | "depop";
export type PromotionAction = "share" | "offer" | "bundle_offer" | "follow";
export type PromotionState = "preview" | "skipped" | "sending" | "done" | "failed" | "unknown" | "checked";

export interface ShippingOption { id: string; label: string; min_offer_cents: number | null }

export interface WaterfallStep {
  discount_percent: number;
  delay_minutes: number;
  shipping_label?: string | null;
}

export type ShareMode = "self" | "party" | "community";
export type HourlyIntensity = "none" | "low" | "medium" | "high";

export interface FollowConfig {
  reciprocal_24h?: boolean;
  new_active_sellers_cap?: number;
  unfollow_cap?: number;
  unsubscribe_live_shows?: boolean;
}

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
  waterfall_step?: number | null;
  like_count: number | null;
  shipping_options: ShippingOption[];
  shipping_discount_id: string | null;
  shipping_discount_label: string | null;
  state: PromotionState;
  note: string | null;
  marketplace_message: string | null;
  confirmation?: "request_accepted" | "job_completed" | "recipients_created" | "legacy_unverified" | null;
  recipient_count?: number | null;
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
  share_modes?: ShareMode[];
  hourly_schedule?: Record<string, HourlyIntensity> | null;
  discount_percent?: number;
  minimum_price?: string | null;
  min_likes?: number;
  shipping_label?: string | null;
  waterfall_steps?: WaterfallStep[] | null;
  bundle_pause_minutes?: number | null;
  bundle_min_items?: number | null;
  bundle_comment?: string | null;
  follow_config?: FollowConfig | null;
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

export interface PromotionAutomation {
  id: string;
  marketplace: PromotionMarketplace;
  action: PromotionAction;
  name: string;
  enabled: boolean;
  available: boolean;
  scope: "all" | "items";
  conversation_ids: string[];
  excluded_ids: string[];
  min_days_listed: number;
  share_order: ShareOrder | null;
  speed: ShareSpeed | null;
  daily_cap: number | null;
  times: string[];
  timezone: string | null;
  share_modes?: ShareMode[] | null;
  hourly_schedule?: Record<string, HourlyIntensity> | null;
  discount_percent: number | null;
  minimum_price: string | null;
  min_likes: number | null;
  shipping_label: string | null;
  waterfall_steps?: WaterfallStep[] | null;
  bundle_pause_minutes?: number | null;
  bundle_min_items?: number | null;
  bundle_comment?: string | null;
  follow_config?: FollowConfig | null;
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
  share_modes?: ShareMode[];
  hourly_intensities?: HourlyIntensity[];
  posh_party_times_pt?: string[];
  max_daily_cap: number;
  max_daily_follows?: number;
  max_daily_unfollows?: number;
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
