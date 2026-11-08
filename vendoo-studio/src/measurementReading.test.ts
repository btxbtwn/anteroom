import { describe, expect, it } from "vitest";
import { applyReading, readingSuggestion } from "./measurementReading";

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

describe("readingSuggestion", () => {
  it("lists what was read for blank fields and what to measure by hand", () => {
    const outcome = applyReading("pants", { waist: "15" }, {
      photos_read: 2,
      measurements: {
        inseam: { status: "accepted", value: 27.25, reads: [27.25, 27.25] },
        rise: { status: "cannot_read", reads: ["cannot read", 10.25] },
      },
    });
    expect(readingSuggestion("pants", outcome)).toBe("From the photos: Inseam 27.25 in. Measure by hand: Rise, Leg Opening.");
  });

  it("passes the plain note through when nothing new was read", () => {
    const outcome = applyReading("top", {}, { photos_read: 0, measurements: {} });
    expect(readingSuggestion("top", outcome)).toBe("No yardstick photo found.");
  });
});
