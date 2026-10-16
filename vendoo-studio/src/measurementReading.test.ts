import { describe, expect, it } from "vitest";
import { applyReading } from "./measurementReading";

describe("applyReading", () => {
  it("fills blank fields with accepted readings and names the rest", () => {
    const outcome = applyReading("pants", { waist: "15" }, {
      photos_read: 2,
      measurements: {
        waist: { status: "accepted", value: 16.25, reads: [16.25, 16.125] },
        rise: { status: "cannot_read", reads: ["cannot read", 10.25] },
        inseam: { status: "accepted", value: 27.25, reads: [27.25, 27.25] },
        legOpening: { status: "disagree", reads: [6.25, 7] },
      },
    });
    expect(outcome.measurements).toEqual({ waist: "15", inseam: "27.25" });
    expect(outcome.suggested).toEqual(["inseam"]);
    expect(outcome.note).toBe("Filled from the photos. Check them. Measure by hand: Rise, Leg Opening.");
  });

  it("says so when no photo had a yardstick", () => {
    const outcome = applyReading("top", {}, { photos_read: 0, measurements: {} });
    expect(outcome.measurements).toEqual({});
    expect(outcome.note).toBe("No yardstick photo found.");
  });
});
