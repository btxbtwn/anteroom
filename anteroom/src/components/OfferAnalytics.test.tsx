import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { OfferEvidence, offerDates } from "./OfferAnalytics";
import { OfferLogRow } from "./OfferLog";
import { offerOutcome, offerPrice } from "./offerActivityFormat";

describe("offer activity", () => {
  it("uses calendar weeks starting Monday and calendar months", () => {
    const sunday = new Date(2026, 10, 22, 12);
    expect(offerDates("week", sunday)).toEqual({ start: "2026-11-16", end: "2026-11-22" });
    expect(offerDates("month", sunday).start).toBe("2026-11-01");
  });
  it("says what each marketplace confirmed instead of “Not available”", () => {
    const base = { confirmed_recipients: null, confirmed_sends: 0, accepted_requests: 0, completed_jobs: 0, unverified: 0, unknown: 0, failed: 0, pending: 0 };
    const html = renderToStaticMarkup(<OfferEvidence data={{ start: "2026-11-16", end: "2026-11-16", timezone: "UTC", confirmed_conversions: null,
      marketplaces: [
        { ...base, marketplace: "poshmark", accepted_requests: 4, unknown: 1 },
        { ...base, marketplace: "depop", completed_jobs: 68, unverified: 2 },
        { ...base, marketplace: "mercari", confirmed_recipients: 5, confirmed_sends: 5 },
      ] }} />);
    expect(html).not.toContain("Not available");
    expect(html).toContain("4 offers sent");
    expect(html).toContain("Poshmark accepted all 4 but doesn&#x27;t say how many likers got each offer");
    expect(html).toContain("1 not confirmed");
    expect(html).toContain("70 offers sent");
    expect(html).toContain("Depop finished all 68");
    expect(html).toContain("2 offers with no marketplace receipt saved");
    expect(html).toContain("Reached 5 buyers, as Mercari reported");
    expect(html).not.toContain("0 failed");
  });
});

describe("offer log", () => {
  const entry = { id: "a", marketplace: "depop", title: "Friends tee", listing_id: "x", sent_at: "2026-11-18T04:30:38Z", status: "sent",
    price_cents: 450, offer_price_cents: 405, recipients: null, confirmation: "job_completed", like_count: 2, note: null };
  it("never turns likes into a recipient count", () => {
    expect(offerOutcome(entry)).toBe("Sent · Depop listed 2 eligible likers before sending");
    expect(offerOutcome({ ...entry, marketplace: "poshmark", confirmation: "request_accepted", like_count: 4 }))
      .toBe("Sent · 4 likes at the time; Poshmark doesn't report how many got it");
    expect(offerOutcome({ ...entry, marketplace: "ebay", confirmation: "recipients_created", recipients: 1 })).toBe("Sent to 1 buyer");
    expect(offerOutcome({ ...entry, confirmation: null, like_count: null })).toBe("Sent · no marketplace receipt saved");
  });
  it("tells the seller to check before resending an unconfirmed offer", () => {
    expect(offerOutcome({ ...entry, status: "unconfirmed" })).toBe("Not confirmed. Check Depop before sending again.");
    expect(offerOutcome({ ...entry, status: "failed", note: "Chrome did not answer in time." })).toBe("Not sent: Chrome did not answer in time.");
  });
  it("shows the offer against the price it came from", () => {
    expect(offerPrice(entry)).toBe("$4.05 (from $4.50)");
    expect(offerPrice({ ...entry, price_cents: null })).toBe("$4.05");
  });
  it("renders a row with marketplace, price and outcome", () => {
    const html = renderToStaticMarkup(<OfferLogRow entry={entry} />);
    expect(html).toContain("Friends tee");
    expect(html).toContain("Depop · $4.05 (from $4.50)");
  });
});
