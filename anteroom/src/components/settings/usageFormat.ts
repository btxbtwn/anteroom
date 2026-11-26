// How Settings → Usage writes money, periods and counts. The server counts days and months in UTC.

/** A cost to the cent. Anything smaller keeps two significant digits, so a real charge never reads as $0.00. */
export function formatCost(usd: number): string {
  if (!(usd > 0)) return "$0.00";
  return usd.toLocaleString("en-US", usd < 0.01
    ? { style: "currency", currency: "USD", maximumSignificantDigits: 2 }
    : { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** "Last 30 days", or the month's name for a `YYYY-MM` period. */
export function periodLabel(id: string): string {
  if (id === "30d") return "Last 30 days";
  return new Date(`${id}-01T00:00:00Z`).toLocaleDateString("en-US", { timeZone: "UTC", month: "long", year: "numeric" });
}

const utcDay = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", month: "short", day: "numeric" });
const utcDate = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", month: "short", day: "numeric", year: "numeric" });

/** The period's first and last day, from its start and its exclusive end: "Oct 1 – 31, 2026". */
export function periodDates(start: string, end: string): string {
  const first = new Date(start);
  const last = new Date(new Date(end).getTime() - 1);
  if (first.getUTCFullYear() !== last.getUTCFullYear()) return `${utcDate.format(first)} – ${utcDate.format(last)}`;
  const until = first.getUTCMonth() === last.getUTCMonth() ? last.getUTCDate() : utcDay.format(last);
  return `${utcDay.format(first)} – ${until}, ${last.getUTCFullYear()}`;
}

/** "Oct 14" for a `YYYY-MM-DD` day. */
export function dayLabel(date: string): string {
  return utcDay.format(new Date(`${date}T00:00:00Z`));
}

const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

/** "1 photo edit", "12 AI calls", "3.6K tokens". */
export function counted(count: number, noun: string): string {
  return `${count < 10_000 ? count.toLocaleString("en-US") : compact.format(count)} ${noun}${count === 1 ? "" : "s"}`;
}
