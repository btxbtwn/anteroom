export type GoalPeriod = "weekly" | "monthly" | "quarterly" | "yearly";
export type GoalMetric = "profit" | "sales" | "listings" | "sell_through_rate";
export type GoalPace = "achieved" | "ahead" | "on_track" | "behind";

export interface HomeTodaySummary {
  date: string;
  timezone: string;
  units_listed: number;
  drafts_created: number;
  units_sold: number;
  revenue: number;
  profit: number | null;
  profit_known: number;
  shares_sent: number;
  offers_sent: number;
  automation_actions: number;
}

export interface SellerGoalProgress {
  id: string;
  period: GoalPeriod;
  metric: GoalMetric;
  label: string;
  custom_label: string | null;
  target: number;
  current: number;
  remaining: number;
  progress_percent: number;
  expected_percent: number;
  pace: GoalPace;
  window_start: string;
  window_end: string;
  days_elapsed: number;
  days_total: number;
  days_remaining: number;
  unit: "currency" | "percent" | "count";
  note: string | null;
}

export type AttentionKind =
  | "still_listed"
  | "expired_session"
  | "failed_job"
  | "unsent_edits"
  | "pending_approval"
  | "relist_gap"
  | "duplicate_listing";

export interface AttentionTarget {
  view: "listings" | "settings" | "automations";
  conversation_id?: string | null;
  settings_section?: "connections" | "listings";
  target_id?: string | null;
}

export interface AttentionItem {
  key: string;
  kind: AttentionKind;
  priority: "high" | "medium" | "low";
  title: string;
  detail: string;
  action_label: string;
  target: AttentionTarget;
  occurred_at: string | null;
  dismissed_at?: string | null;
  expires_at?: string | null;
  reappears_in_days?: number;
}

export interface HomeSummaryResponse {
  today: HomeTodaySummary;
  goals: SellerGoalProgress[];
  needs_attention: AttentionItem[];
  dismissed: AttentionItem[];
  counts: {
    active: number;
    dismissed: number;
    by_kind: Record<string, number>;
  };
}

export interface HomeMutationResponse {
  ok: boolean;
  goal_id?: string;
  home: HomeSummaryResponse;
}

export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}
