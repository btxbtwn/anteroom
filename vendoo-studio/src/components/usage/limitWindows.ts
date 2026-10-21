// Ported from T3 Code (https://github.com/pingdotgg/t3code), MIT License,
// (c) 2026 T3 Tools Inc.: packages/shared/src/usageLimits.ts (the window
// arithmetic and wording) and apps/web/src/timestampFormat.ts
// (formatUpcomingTimestamp). Studio's API names fields in snake_case.
import type { ProviderUsageLimits, ProviderUsageWindow } from "../../api/types";

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

export function limitsNotice(limits: ProviderUsageLimits): string | null {
  if (limits.unavailable?.reason === "unsupported") {
    return limits.unavailable.message ?? "This account has no subscription limits.";
  }
  if (limits.unavailable?.reason === "probeFailed") {
    return limits.unavailable.message ?? "Could not read limits.";
  }
  return limits.windows.length === 0 ? "No limits reported." : null;
}

export function remainingPercent(window: ProviderUsageWindow): number {
  return Math.round(100 - Math.max(0, Math.min(100, window.used_percent)));
}

function resetMillis(window: ProviderUsageWindow): number | null {
  if (window.resets_at === undefined) return null;
  const at = Date.parse(window.resets_at);
  return Number.isFinite(at) ? at : null;
}

export function elapsedShare(window: ProviderUsageWindow, now: number): number | null {
  const resetsAt = resetMillis(window);
  if (resetsAt === null || window.window_duration_mins === undefined) return null;
  const length = window.window_duration_mins * MINUTE;
  if (length <= 0) return null;
  return Math.max(0, Math.min(1, (length - (resetsAt - now)) / length));
}

// Bars show quota remaining, so even pace tracks time remaining.
export function paceRemainingPercent(window: ProviderUsageWindow, now: number): number | null {
  const elapsed = elapsedShare(window, now);
  return elapsed === null ? null : Math.round((1 - elapsed) * 100);
}

export type LimitPace = "ahead" | "on" | "under";

export function paceOf(window: ProviderUsageWindow, now: number): LimitPace | null {
  const elapsed = elapsedShare(window, now);
  return elapsed === null ? null : paceOfShares(window.used_percent, elapsed);
}

function paceOfShares(usedPercent: number, elapsed: number): LimitPace {
  const gap = usedPercent - elapsed * 100;
  if (gap > 5) return "ahead";
  if (gap < -5) return "under";
  return "on";
}

export function formatDuration(ms: number): string {
  const remaining = Math.max(0, ms);
  const days = Math.floor(remaining / DAY);
  const hours = Math.floor((remaining % DAY) / HOUR);
  const minutes = Math.floor((remaining % HOUR) / MINUTE);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

export function formatResetsIn(window: ProviderUsageWindow, now: number): string | null {
  const resetsAt = resetMillis(window);
  if (resetsAt === null) return null;
  if (resetsAt <= now) return "resets now";
  if (resetsAt - now < MINUTE) return "Resets momentarily";
  return `resets in ${formatDuration(resetsAt - now)}`;
}

const timeFormatter = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
const numericDateFormatter = new Intl.DateTimeFormat(undefined, { month: "numeric", day: "numeric" });
const numericDateWithYearFormatter = new Intl.DateTimeFormat(undefined, {
  month: "numeric",
  day: "numeric",
  year: "numeric",
});

/** "3:40 PM", "tomorrow at 3:40 PM" or "11/19 3:40 PM", as T3 words a reset. */
export function formatUpcomingTimestamp(isoDate: string, nowMs: number = Date.now()): string {
  const date = new Date(isoDate);
  if (!Number.isFinite(date.getTime())) return "";
  const time = timeFormatter.format(date);
  const now = new Date(nowMs);
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const startOfTargetDay = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  const dayDiff = Math.round((startOfTargetDay - startOfToday) / DAY);
  if (dayDiff === 0) return time;
  if (dayDiff === 1) return `tomorrow at ${time}`;
  const dateFormatter =
    date.getFullYear() === now.getFullYear() ? numericDateFormatter : numericDateWithYearFormatter;
  return `${dateFormatter.format(date)} ${time}`;
}
