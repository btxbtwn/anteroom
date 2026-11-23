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

export interface AttentionDismissalResult {
  item_key: string;
  dismissed_at: string;
  expires_at: string;
  reappears_in_days: number;
}

export interface HomeMutationResponse {
  ok: boolean;
  goal_id?: string;
  home?: HomeSummaryResponse;
  dismissal?: AttentionDismissalResult;
  restored?: boolean;
  item_key?: string;
}

const ATTENTION_DISMISS_DAYS = 7;

function recountAttention(home: Pick<HomeSummaryResponse, "needs_attention" | "dismissed">): HomeSummaryResponse["counts"] {
  const by_kind: Record<string, number> = {};
  for (const item of home.needs_attention) {
    by_kind[item.kind] = (by_kind[item.kind] ?? 0) + 1;
  }
  return {
    active: home.needs_attention.length,
    dismissed: home.dismissed.length,
    by_kind,
  };
}

/** Move one alert into Dismissed without waiting for a full Today rebuild. */
export function patchHomeAfterDismiss(
  home: HomeSummaryResponse,
  item: AttentionItem,
  dismissal?: Partial<AttentionDismissalResult> | null,
): HomeSummaryResponse {
  const dismissed_at = dismissal?.dismissed_at ?? new Date().toISOString();
  const expires_at =
    dismissal?.expires_at
    ?? new Date(Date.now() + ATTENTION_DISMISS_DAYS * 86_400_000).toISOString();
  const reappears_in_days = dismissal?.reappears_in_days ?? ATTENTION_DISMISS_DAYS;
  const needs_attention = home.needs_attention.filter((entry) => entry.key !== item.key);
  const dismissedItem: AttentionItem = {
    ...item,
    dismissed_at,
    expires_at,
    reappears_in_days,
  };
  const dismissed = [
    dismissedItem,
    ...home.dismissed.filter((entry) => entry.key !== item.key),
  ];
  return {
    ...home,
    needs_attention,
    dismissed,
    counts: recountAttention({ needs_attention, dismissed }),
  };
}

/** Move one alert back to Active after Restore. */
export function patchHomeAfterRestore(
  home: HomeSummaryResponse,
  itemKey: string,
): HomeSummaryResponse {
  const item = home.dismissed.find((entry) => entry.key === itemKey);
  if (!item) return home;
  const {
    dismissed_at: _dismissedAt,
    expires_at: _expiresAt,
    reappears_in_days: _reappears,
    ...activeItem
  } = item;
  const needs_attention = [
    activeItem,
    ...home.needs_attention.filter((entry) => entry.key !== itemKey),
  ];
  const dismissed = home.dismissed.filter((entry) => entry.key !== itemKey);
  return {
    ...home,
    needs_attention,
    dismissed,
    counts: recountAttention({ needs_attention, dismissed }),
  };
}

/**
 * Goal mutations still rebuild home_summary for progress bars, but must not
 * replace Needs attention — that scan can lag an optimistic Dismiss/Restore.
 */
export function applyGoalHomeSnapshot(
  server: HomeSummaryResponse,
  local: HomeSummaryResponse | undefined,
): HomeSummaryResponse {
  if (!local) return server;
  return {
    ...local,
    today: server.today,
    goals: server.goals,
  };
}

export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}
