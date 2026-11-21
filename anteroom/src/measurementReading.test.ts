import { describe, expect, it } from "vitest";
import type { MeasurementReadResult } from "./api/types";
import { applyReading, readEach, readingSuggestion } from "./measurementReading";

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

  it("says the yardstick could not be read, without naming the model", () => {
    const outcome = applyReading("top", {}, {
      photos_read: 1,
      measurements: { pitToPit: { status: "cannot_read", reads: ["cannot read", "cannot read"] } },
    });
    expect(outcome.note).toBe("The yardstick could not be read. Measure by hand: Pit to Pit, Length, Sleeve.");
  });

  it("ignores a provider name an older server still sends", () => {
    const outcome = applyReading("top", {}, {
      photos_read: 1,
      reader: "OpenRouter (google/gemini-flash-latest)",
      measurements: { pitToPit: { status: "accepted", value: 20.5, reads: [20.5, 20.5] } },
    } as MeasurementReadResult);
    expect(outcome.note).toBe("Filled from the photos. Check them. Measure by hand: Length, Sleeve.");
    expect(readingSuggestion("top", outcome)).not.toMatch(/openrouter|gemini|google/i);
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

describe("readEach", () => {
  it("bounds in-flight work to the requested concurrency and visits every item", async () => {
    let active = 0;
    let peak = 0;
    const seen: number[] = [];
    await readEach([1, 2, 3, 4, 5, 6], async (item) => {
      active += 1;
      peak = Math.max(peak, active);
      await new Promise((resolve) => setTimeout(resolve, 5));
      seen.push(item);
      active -= 1;
    }, 2);
    expect(peak).toBe(2);
    expect(seen.sort((a, b) => a - b)).toEqual([1, 2, 3, 4, 5, 6]);
  });

  it("stops launching queued items once aborted", async () => {
    const controller = new AbortController();
    const started: number[] = [];
    await readEach([1, 2, 3, 4, 5], async (item) => {
      started.push(item);
      if (started.length === 2) controller.abort();
      await new Promise((resolve) => setTimeout(resolve, 5));
    }, 2, controller.signal);
    expect(started).toEqual([1, 2]);
  });
});
