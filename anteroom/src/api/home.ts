import type { AttentionInbox, AttentionItem, AttentionResolution, AttentionSnooze } from "./contract.gen";

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

export type { AttentionItem, AttentionTarget } from "./contract.gen";
export type AttentionKind = AttentionItem["kind"];

export interface HomeSummaryResponse extends AttentionInbox {
  today: HomeTodaySummary;
  goals: SellerGoalProgress[];
}

export interface HomeMutationResponse {
  ok: boolean;
  goal_id?: string;
  home?: HomeSummaryResponse;
}

const ATTENTION_DISMISS_DAYS = 7;

type AttentionLists = Pick<HomeSummaryResponse, "needs_attention" | "dismissed" | "resolved">;

function withLists(home: HomeSummaryResponse, lists: AttentionLists): HomeSummaryResponse {
  const by_kind: Record<string, number> = {};
  for (const item of lists.needs_attention) {
    by_kind[item.kind] = (by_kind[item.kind] ?? 0) + 1;
  }
  return {
    ...home,
    ...lists,
    counts: {
      active: lists.needs_attention.length,
      dismissed: lists.dismissed.length,
      resolved: lists.resolved.length,
      by_kind,
    },
  };
}

function withoutKey(items: AttentionItem[] | undefined, key: string): AttentionItem[] {
  return (items ?? []).filter((entry) => entry.key !== key);
}

/** Move one alert into Snoozed without waiting for a full Today rebuild. */
export function patchHomeAfterDismiss(
  home: HomeSummaryResponse,
  item: AttentionItem,
  dismissal?: Partial<AttentionSnooze> | null,
): HomeSummaryResponse {
  const dismissed_at = dismissal?.dismissed_at ?? new Date().toISOString();
  const expires_at =
    dismissal?.expires_at
    ?? new Date(Date.now() + ATTENTION_DISMISS_DAYS * 86_400_000).toISOString();
  const reappears_in_days = dismissal?.reappears_in_days ?? ATTENTION_DISMISS_DAYS;
  const { resolved_at: _resolvedAt, ...shown } = item;
  return withLists(home, {
    needs_attention: withoutKey(home.needs_attention, item.key),
    dismissed: [{ ...shown, dismissed_at, expires_at, reappears_in_days }, ...withoutKey(home.dismissed, item.key)],
    resolved: withoutKey(home.resolved, item.key),
  });
}

/** Move one alert into Resolved without waiting for a full Today rebuild. */
export function patchHomeAfterResolve(
  home: HomeSummaryResponse,
  item: AttentionItem,
  resolution?: Partial<AttentionResolution> | null,
): HomeSummaryResponse {
  const resolved_at = resolution?.resolved_at ?? new Date().toISOString();
  const { dismissed_at: _dismissedAt, expires_at: _expiresAt, reappears_in_days: _reappears, ...shown } = item;
  return withLists(home, {
    needs_attention: withoutKey(home.needs_attention, item.key),
    dismissed: withoutKey(home.dismissed, item.key),
    resolved: [{ ...shown, resolved_at }, ...withoutKey(home.resolved, item.key)],
  });
}

/** Move one snoozed or resolved alert back to Active after Restore. */
export function patchHomeAfterRestore(
  home: HomeSummaryResponse,
  itemKey: string,
): HomeSummaryResponse {
  const item = [...(home.dismissed ?? []), ...(home.resolved ?? [])].find((entry) => entry.key === itemKey);
  if (!item) return home;
  const {
    dismissed_at: _dismissedAt,
    expires_at: _expiresAt,
    reappears_in_days: _reappears,
    resolved_at: _resolvedAt,
    ...activeItem
  } = item;
  return withLists(home, {
    needs_attention: [activeItem, ...withoutKey(home.needs_attention, itemKey)],
    dismissed: withoutKey(home.dismissed, itemKey),
    resolved: withoutKey(home.resolved, itemKey),
  });
}

/** The button that resolves an item: a duplicate pair is "Not duplicates". */
export function resolveLabel(kind: AttentionKind): string {
  return kind === "duplicate_listing" ? "Not duplicates" : "Resolve";
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
