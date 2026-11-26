/** A scheduled task the seller confirmed, or a draft that has not been confirmed. */

export type AutomationStatus = "draft" | "active" | "paused" | "completed";

export interface AutomationAction {
  type: "regenerate" | "update_fields" | "price" | "delist" | "relist";
  mode?: "amount" | "percent";
  value?: number | null;
  floor?: number | null;
  floor_percent?: number | null;
  repeat?: "once" | "step";
  fields?: Record<string, string>;
}

export interface AutomationSchedule {
  kind: "once" | "daily" | "weekly";
  at?: string;
  time?: string;
  weekdays?: number[];
}

export interface AutomationFilters {
  category?: string;
  department?: string;
  brand?: string;
  min_age_days?: number;
  max_age_days?: number;
  status?: string;
  conversation_ids?: string[];
}

export interface Automation {
  id: string;
  name: string;
  prompt: string | null;
  status: AutomationStatus;
  timezone: string;
  schedule: AutomationSchedule;
  filters: AutomationFilters;
  actions: AutomationAction[];
  marketplaces: string[];
  price_repeat?: "once" | "step" | null;
  summary: string;
  confirmed_at: string | null;
  next_run_at: string | null;
  last_run_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

/** What a sentence turned into. Nothing is saved until the seller confirms it. */
export interface AutomationDraft {
  op?: string;
  id?: string;
  name: string;
  prompt: string;
  timezone: string;
  schedule: AutomationSchedule | null;
  filters: AutomationFilters;
  actions: AutomationAction[];
  marketplaces: string[];
  price_repeat?: "once" | "step" | null;
  issues: string[];
  ready: boolean;
  summary?: string;
}

export interface AutomationPreviewItem {
  conversation_id: string;
  title: string;
  include: boolean;
  skip_reason: string | null;
  age_days: number | null;
  listed_at?: string | null;
  listed_source?: string | null;
  price: number | null;
  next_price: number | null;
  original_price: number | null;
  marketplaces: string[];
}

export interface AutomationPreview {
  ready: boolean;
  issues: string[];
  summary: string;
  count: number;
  items: AutomationPreviewItem[];
}

export interface AutomationRunItem {
  conversation_id: string;
  title?: string;
  ok: boolean;
  error?: string | null;
  state?: string | null;
  down?: string[];
  price?: number | null;
  next_price?: number | null;
}

export interface AutomationRun {
  id: string;
  automation_id: string;
  origin: string;
  status: string;
  dry_run: boolean;
  attempt_count: number;
  scheduled_for: string | null;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  items: AutomationRunItem[];
  job_ids: string[];
}

export interface AutomationHistory {
  automation_id: string;
  runs: AutomationRun[];
}

/** The body create, update, and preview accept. `activate` confirms it may run. */
export interface AutomationBody {
  name?: string;
  prompt?: string;
  timezone?: string;
  schedule?: AutomationSchedule;
  filters?: AutomationFilters;
  actions?: AutomationAction[];
  marketplaces?: string[];
  price_repeat?: "once" | "step";
  activate?: boolean;
}

/** An automation an assistant answer drafted. It stays proposed until the seller confirms it. */
export interface AssistantAutomation {
  status: "proposed" | "confirmed" | "dismissed";
  proposal?: AutomationDraft;
  automation_id?: string;
}

/** One immutable item/action/marketplace proposal waiting for seller approval. */
export interface AutomationApproval {
  id: string;
  automation_id: string;
  conversation_id: string;
  title: string | null;
  revision_id: string | null;
  action: "publish" | "delist" | "relist";
  targets: Record<string, { listing_id: string | null; url: string | null; state: string | null; sold_here: boolean | null }>;
  review_token: string;
  marketplaces: string[];
  status: string;
  state: string;
  result?: { ok?: boolean; state?: string | null; error?: string | null; down?: string[] };
}

/** A marketplace a relist left offline. */
export interface RelistGap {
  id: string;
  state: string;
  title: string | null;
  marketplace: string;
  automation_id: string;
  conversation_id: string;
  error: string | null;
}

export interface DraftScheduleState {
  conversation_id: string;
  title: string | null;
  ready_to_list: boolean;
  scheduled_publish_at: string | null;
  scheduled_marketplaces: string[];
  scheduled_revision_id: string | null;
  scheduled_confirmed_at: string | null;
  scheduled_status: string | null;
  scheduled_error: string | null;
}

export interface ScheduledPublishLog {
  id: string;
  conversation_id: string;
  title: string | null;
  revision_id: string | null;
  marketplaces: string[];
  scheduled_for: string | null;
  status: string;
  results: Record<string, unknown> | null;
  error: string | null;
  created_at: string | null;
  finished_at: string | null;
}

export interface BulkScheduleResult {
  scheduled?: DraftScheduleState[];
  unscheduled?: DraftScheduleState[];
  failed: { conversation_id: string; error: string }[];
}

export type AutoRelistMarketplace = "poshmark" | "ebay" | "mercari" | "depop";

export interface AutoRelistFilters {
  min_likes?: number;
  max_likes?: number;
  min_price?: number;
  max_price?: number;
  conditions?: string[];
  keywords?: string[];
  labels?: string[];
}

export interface AutoRelistRule {
  id?: string;
  marketplace: AutoRelistMarketplace;
  enabled: boolean;
  min_age_days: number;
  ebay_recreate_before_gtc_minutes: number | null;
  daily_cap: number;
  price_adjust_mode: "percent" | "amount" | null;
  price_adjust_value: number | null;
  price_floor: number | null;
  filters: AutoRelistFilters;
  relisted_today: number;
  remaining_today: number;
  last_run_at: string | null;
  /** Why this marketplace's Auto-Relist leaves listings up instead of relisting them. */
  paused_reason: string | null;
}

export interface AutoRelistRuleBody {
  enabled: boolean;
  confirm?: boolean;
  min_age_days: number;
  ebay_recreate_before_gtc_minutes?: number | null;
  daily_cap: number;
  price_adjust_mode?: "percent" | "amount" | null;
  price_adjust_value?: number | null;
  price_floor?: number | null;
  filters?: AutoRelistFilters;
}

export interface AutoRelistLog {
  id: string;
  conversation_id: string;
  title: string | null;
  marketplace: AutoRelistMarketplace;
  old_listing_id: string | null;
  new_listing_id: string | null;
  old_price: number | null;
  new_price: number | null;
  status: string;
  reason: string | null;
  created_at: string | null;
}
