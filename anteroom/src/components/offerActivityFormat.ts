import type { OfferAnalyticsMarketplace, OfferLogEntry } from "../api/contract.gen";
import { marketplaceName } from "./marketplaceNames";

const plural = (count: number, one: string, many = `${one}s`) => `${count} ${count === 1 ? one : many}`;

/** Offers the marketplace accepted, whatever it said about who received them. */
export function offersSent(row: OfferAnalyticsMarketplace): number {
  return row.confirmed_sends + row.accepted_requests + row.completed_jobs + row.unverified;
}

/**
 * What the marketplace told Anteroom about who received the offers. Poshmark
 * and Depop never say, so their lines say that instead of showing a number.
 */
export function offerReach(row: OfferAnalyticsMarketplace): string[] {
  const lines: string[] = [];
  if (row.confirmed_recipients != null) {
    lines.push(`Reached ${plural(row.confirmed_recipients, "buyer")}, as ${marketplaceName(row.marketplace)} reported`);
  }
  if (row.accepted_requests) {
    lines.push(`Poshmark accepted ${row.accepted_requests === 1 ? "it" : `all ${row.accepted_requests}`} but doesn't say how many likers got each offer`);
  }
  if (row.completed_jobs) {
    lines.push(`Depop finished ${row.completed_jobs === 1 ? "it" : `all ${row.completed_jobs}`} but doesn't say how many likers got each offer`);
  }
  if (row.unverified) lines.push(`${plural(row.unverified, "offer")} with no marketplace receipt saved`);
  return lines;
}

/** Only the problems that happened; nothing when every offer went through. */
export function offerProblems(row: OfferAnalyticsMarketplace): string | null {
  const parts = [
    row.unknown ? `${row.unknown} not confirmed` : "",
    row.failed ? `${row.failed} failed` : "",
    row.pending ? `${row.pending} sending` : "",
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
}

const money = (cents: number) => `$${(cents / 100).toFixed(2)}`;

export function offerPrice(entry: OfferLogEntry): string | null {
  if (entry.offer_price_cents == null) return null;
  return entry.price_cents != null && entry.price_cents > entry.offer_price_cents
    ? `${money(entry.offer_price_cents)} (from ${money(entry.price_cents)})`
    : money(entry.offer_price_cents);
}

/** One log entry's result, saying only what the marketplace confirmed. */
export function offerOutcome(entry: OfferLogEntry): string {
  const market = marketplaceName(entry.marketplace);
  if (entry.status === "sending") return "Sending…";
  if (entry.status === "unconfirmed") return `Not confirmed. Check ${market} before sending again.`;
  if (entry.status === "checked") return "You checked this one";
  if (entry.status === "failed") return entry.note ? `Not sent: ${entry.note}` : "Not sent";
  if (entry.recipients != null) return `Sent to ${plural(entry.recipients, "buyer")}`;
  if (entry.confirmation === "job_completed") {
    return entry.like_count ? `Sent · Depop listed ${plural(entry.like_count, "eligible liker")} before sending` : "Sent · Depop doesn't report how many";
  }
  if (entry.confirmation === "request_accepted") {
    return entry.like_count ? `Sent · ${plural(entry.like_count, "like")} at the time; Poshmark doesn't report how many got it` : "Sent · Poshmark doesn't report how many";
  }
  return "Sent · no marketplace receipt saved";
}
