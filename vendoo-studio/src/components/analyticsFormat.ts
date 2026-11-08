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

/** Money on Analytics: the currency's symbol and always two decimals, so figures line up. */
export function formatCurrency(value: number | string, currency = "USD"): string {
  const amount = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(amount)) return "—";
  try {
    return amount.toLocaleString("en-US", { style: "currency", currency, minimumFractionDigits: 2, maximumFractionDigits: 2 });
  } catch {
    // An unknown currency code: keep the code, still two decimals.
    return `${currency} ${amount.toFixed(2)}`;
  }
}

/** Short money for chart labels; the bar's description has the full figure. */
export function compactMoney(value: number): string {
  const sign = value < 0 ? "-" : "";
  const amount = Math.abs(value);
  return amount >= 1000
    ? `${sign}$${(amount / 1000).toLocaleString("en-US", { maximumFractionDigits: 1 })}K`
    : `${sign}$${amount.toLocaleString("en-US", { maximumFractionDigits: amount < 10 ? 1 : 0 })}`;
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

/** What both tabs need to say which amounts a figure is missing. */
interface Coverage {
  count: number;
  revenue_known: number;
  profit_known: number;
  fees_known: number;
}

export function saleCount(count: number): string {
  return count === 1 ? "1 sale" : `${count} sales`;
}

/** Which amounts leave profit short, e.g. "cost missing on 1 sale". Empty when none are. */
export function profitGaps(figures: Coverage): string {
  const cost = figures.count - figures.profit_known;
  const fees = figures.profit_known - figures.fees_known;
  return [cost > 0 ? `cost missing on ${saleCount(cost)}` : "", fees > 0 ? `fees missing on ${saleCount(fees)}` : ""]
    .filter(Boolean).join(", ");
}

function priceGaps(figures: Coverage): string {
  const missing = figures.count - figures.revenue_known;
  return missing > 0 ? `price missing on ${saleCount(missing)}` : "";
}

export function revenueHint(figures: Coverage): string | undefined {
  if (figures.count > 0 && figures.revenue_known === 0) return "Record sale prices to see revenue";
  const gaps = priceGaps(figures);
  return gaps ? `Partial: ${gaps}` : undefined;
}

export function profitHint(figures: Coverage & { profit: number | null }): string {
  if (figures.count > 0 && figures.profit == null) return "Record sale prices and costs to see profit";
  const gaps = profitGaps(figures);
  return gaps ? `Partial: ${gaps}` : "After fees, shipping and cost";
}

/** A change only when both periods have every amount; otherwise which amounts are missing, and where. */
export function compareChange(
  current: Coverage, previous: Coverage | null, kind: "revenue" | "profit",
  now: number | null, before: number | null, format: (value: number) => string,
): string | undefined {
  if (!previous) return undefined;
  const gaps = kind === "revenue" ? priceGaps : profitGaps;
  const here = gaps(current);
  if (here) return `No comparison: ${here}`;
  const there = gaps(previous);
  if (there) return `No comparison: ${there} in the earlier period`;
  return formatChange(now, before, format);
}

export const SELL_THROUGH_HINT = "Sold ÷ (sold + still listed)";

/** The figures row on Sales records: this period, and how it moved since the one before. */
export function overviewFigures(
  current: SalesOverviewFigures,
  previous: SalesOverviewFigures | null,
  marketplaceChosen: boolean,
): OverviewFigure[] {
  const none = current.count === 0;
  const count = (value: number) => String(Math.round(value));
  return [
    {
      label: "Revenue",
      value: none ? formatCurrency(0) : current.revenue_known === 0 ? null : formatCurrency(current.revenue),
      hint: revenueHint(current),
      change: compareChange(current, previous, "revenue", current.revenue, previous?.revenue ?? null, formatCurrency),
    },
    {
      label: "Profit",
      value: none ? formatCurrency(0) : current.profit == null ? null : formatCurrency(current.profit),
      negative: current.profit != null && current.profit < 0,
      hint: profitHint(current),
      change: compareChange(current, previous, "profit",
        none ? 0 : current.profit, previous && previous.count === 0 ? 0 : previous?.profit ?? null, formatCurrency),
    },
    {
      label: "Items sold",
      value: String(current.count),
      change: previous ? formatChange(current.count, previous.count, count) : undefined,
    },
    {
      label: "Median days to sell",
      value: current.median_days == null ? null : formatDays(current.median_days),
      hint: daysHint(current),
      change: previous && current.median_days != null && previous.median_days != null
        ? formatChange(current.median_days, previous.median_days, formatDays) : undefined,
    },
    {
      label: "Sell-through",
      value: current.sell_through == null ? null : `${current.sell_through}%`,
      hint: marketplaceChosen ? "Shown for all marketplaces only" : SELL_THROUGH_HINT,
    },
  ];
}

export function daysHint(figures: { count: number; days_known: number }): string | undefined {
  const missing = figures.count - figures.days_known;
  return missing > 0 && figures.days_known > 0 ? `List date missing on ${saleCount(missing)}` : undefined;
}

/** "Sep 10 – Oct 9, 2026"; both years when they differ. */
export function dayRange(start: string, end: string): string {
  const day = (value: string) => new Date(`${value.slice(0, 10)}T00:00:00Z`);
  const first = day(start);
  const last = day(end);
  const format = (date: Date, year: boolean) =>
    date.toLocaleDateString("en-US", { month: "short", day: "numeric", ...(year ? { year: "numeric" } : {}), timeZone: "UTC" });
  return `${format(first, first.getUTCFullYear() !== last.getUTCFullYear())} – ${format(last, true)}`;
}

/** "shipping the buyer paid (8), shipping you paid (8)" */
export function missingList(counts: { label: string; count: number }[]): string {
  return counts.map(({ label, count }) => `${label} (${count})`).join(", ");
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
