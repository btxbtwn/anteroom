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

/** A regenerated listing waiting for the seller to publish it. */
export interface AutomationApproval {
  id: string;
  automation_id: string;
  conversation_id: string;
  title: string | null;
  revision_id: string | null;
  action: "publish" | "delist" | "relist";
  targets: Record<string,{listing_id:string|null;url:string|null;state:string|null;sold_here:boolean|null}>;
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
