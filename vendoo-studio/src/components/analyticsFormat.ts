import type { SalesOverviewFigures } from "../api/salesLedger";
import type { AnalyticsRange } from "../api/types";

/** The sales periods Analytics and Marketing both total over. */
export const ANALYTICS_RANGES: { id: AnalyticsRange; label: string; heading: string }[] = [
  { id: "wtd", label: "This week", heading: "This week" },
  { id: "mtd", label: "This month", heading: "This month" },
  { id: "ytd", label: "This year", heading: "This year" },
  { id: "7d", label: "Last 7 days", heading: "Last 7 days" },
  { id: "30d", label: "Last 30 days", heading: "Last 30 days" },
  { id: "90d", label: "Last 90 days", heading: "Last 90 days" },
  { id: "12m", label: "Last 12 months", heading: "Last 12 months" },
  { id: "all", label: "All time", heading: "All time" },
];

/** Whole dollars stay whole; a cent keeps two places. */
export function formatMoney(value: number): string {
  if (!Number.isFinite(value)) return "$0";
  const cents = Math.round(Math.abs(value) * 100);
  const hasCents = cents % 100 !== 0;
  return value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: hasCents ? 2 : 0,
    maximumFractionDigits: 2,
  });
}

export function formatDays(days: number): string {
  const rounded = Math.round(days);
  return rounded === 1 ? "1 day" : `${rounded} days`;
}

/** Use an absolute change when a zero or negative baseline makes percentages misleading. */
export function formatChange(
  current: number | null,
  previous: number | null,
  format: (value: number) => string,
): string {
  if (current == null || previous == null) return "No comparison data";
  if (current === previous) return "No change";
  const difference = current - previous;
  const direction = difference > 0 ? "Up" : "Down";
  const percent = previous > 0 && current >= 0
    ? ` (${Math.abs(difference / previous * 100).toFixed(0)}%)`
    : "";
  return `${direction} ${format(Math.abs(difference))}${percent}`;
}

export interface OverviewFigure {
  label: string;
  /** null when the figure can't be worked out from what was recorded. */
  value: string | null;
  hint?: string;
  change?: string;
  negative?: boolean;
}

/** The figures row on Sales records: this period, and how it moved since the one before. */
export function overviewFigures(
  current: SalesOverviewFigures,
  previous: SalesOverviewFigures | null,
  marketplaceChosen: boolean,
): OverviewFigure[] {
  const none = current.count === 0;
  const pricesComplete = (figures: SalesOverviewFigures) => figures.revenue_known === figures.count;
  const costsComplete = (figures: SalesOverviewFigures) =>
    figures.profit_known === figures.count && figures.fees_known === figures.count;
  const compare = (complete: boolean, missing: string, now: number | null, before: number | null, format: (value: number) => string) =>
    !previous ? undefined : complete ? formatChange(now, before, format) : `No comparison: ${missing}`;
  const count = (value: number) => String(Math.round(value));
  return [
    {
      label: "Revenue",
      value: none ? formatMoney(0) : current.revenue_known === 0 ? null : formatMoney(current.revenue),
      hint: none ? undefined : current.revenue_known === 0 ? "Record sale prices to see revenue"
        : current.revenue_known < current.count ? `Recorded on ${current.revenue_known} of ${current.count} sales` : undefined,
      change: compare(Boolean(previous && pricesComplete(current) && pricesComplete(previous)), "missing prices",
        current.revenue, previous?.revenue ?? null, formatMoney),
    },
    {
      label: "Profit",
      value: none ? formatMoney(0) : current.profit == null ? null : formatMoney(current.profit),
      negative: current.profit != null && current.profit < 0,
      hint: none ? "After fees, shipping and cost" : current.profit == null ? "Record sale prices and costs to see profit"
        : costsComplete(current) ? "After fees, shipping and cost" : `Cost recorded on ${current.profit_known} of ${current.count} sales`,
      change: compare(Boolean(previous && costsComplete(current) && costsComplete(previous)), "missing costs or fees",
        none ? 0 : current.profit, previous && previous.count === 0 ? 0 : previous?.profit ?? null, formatMoney),
    },
    {
      label: "Items sold",
      value: String(current.count),
      change: compare(true, "", current.count, previous?.count ?? null, count),
    },
    {
      label: "Average days to sell",
      value: current.average_days == null ? null : formatDays(current.average_days),
      hint: current.average_days != null && current.days_known < current.count ? `Based on ${current.days_known} of ${current.count} sales` : undefined,
      change: previous && current.average_days != null && previous.average_days != null
        ? formatChange(current.average_days, previous.average_days, formatDays) : undefined,
    },
    {
      label: "Sell-through",
      value: current.sell_through == null ? null : `${current.sell_through}%`,
      hint: marketplaceChosen ? "Shown for all marketplaces only" : "Sold ÷ (sold + still listed)",
    },
  ];
}

/** Clean, evenly spaced axis values from zero up past ``peak``; whole steps for counts. */
export function axisTicks(peak: number, whole = false): number[] {
  if (peak <= 0) return [0, 1];
  const rough = whole ? Math.max(1, peak / 5) : peak / 5;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map((factor) => factor * magnitude).find((candidate) => candidate >= rough) ?? 10 * magnitude;
  const ticks = [];
  for (let value = 0; value < peak + step; value += step) ticks.push(value);
  return ticks;
}

/** "Oct 26" for the week starting Monday 2026-10-26. */
export function weekLabel(isoDay: string): string {
  return new Date(`${isoDay}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}
