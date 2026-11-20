import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { OfferEvidence, offerDates } from "./OfferAnalytics";

describe("offer activity", () => {
  it("uses calendar weeks starting Monday and calendar months", () => {
    const sunday = new Date(2026, 10, 22, 12);
    expect(offerDates("week", sunday)).toEqual({ start: "2026-11-16", end: "2026-11-22" });
    expect(offerDates("month", sunday).start).toBe("2026-11-01");
  });
  it("keeps missing recipient data unknown", () => {
    const html = renderToStaticMarkup(<OfferEvidence data={{ start: "2026-11-16", end: "2026-11-16", timezone: "UTC", confirmed_conversions: null,
      marketplaces: [{ marketplace: "poshmark", confirmed_recipients: null, confirmed_sends: 0, accepted_requests: 4, completed_jobs: 0, unverified: 0, unknown: 1, failed: 0, pending: 0 }] }} />);
    expect(html).toContain("Not available");
    expect(html).toContain("4 accepted requests");
    expect(html).not.toContain("0 confirmed recipient offers");
  });
});
