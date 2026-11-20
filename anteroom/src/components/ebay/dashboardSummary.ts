import type { EbayRow } from "../../api/ebay";

function total(values: (string | number | null | undefined)[]): number | null {
  if (!values.length || values.some((value) => value == null || value === "" || !Number.isFinite(Number(value)))) return null;
  return values.reduce<number>((sum, value) => sum + Math.round(Number(value) * 100), 0) / 100;
}

/** These are page totals, never estimates for orders that have not loaded. */
export function summarizeDisplayedEarnings(rows: EbayRow[]) {
  return { earnings: total(rows.map((row) => row.amount)), costs: total(rows.map((row) => row.fields["Item cost"])),
    profit: total(rows.map((row) => row.fields.Profit)) };
}

export function displayMoney(value: number | null | undefined) {
  return value == null ? "Not available" : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(value);
}
