import { describe, expect, it } from "vitest";
import {
  formatEnableAllConfirmMessage,
  MAX_WATERFALL_DELAY_MINUTES,
  validateWaterfallDraft,
} from "./EbayOffersSettings";

describe("EbayOffersSettings waterfall validation and confirmation", () => {
  it("allows an intentionally empty waterfall configuration", () => {
    const result = validateWaterfallDraft([]);
    expect(result).toEqual({ valid: true, steps: undefined, rowErrors: [] });
  });

  it("preserves explicit 0 delay and accepts valid increasing waterfall steps", () => {
    const result = validateWaterfallDraft([
      { discount: "10", delayMinutes: "0" },
      { discount: "15", delayMinutes: "1440" },
      { discount: "20", delayMinutes: String(MAX_WATERFALL_DELAY_MINUTES) },
    ]);
    expect(result.valid).toBe(true);
    expect(result.rowErrors).toEqual([null, null, null]);
    expect(result.steps).toEqual([
      { discount_percent: 10, delay_minutes: 0 },
      { discount_percent: 15, delay_minutes: 1440 },
      { discount_percent: 20, delay_minutes: MAX_WATERFALL_DELAY_MINUTES },
    ]);
  });

  it("rejects blank delay instead of coercing it to 0, and rejects out-of-range delays/discounts", () => {
    const blankDelay = validateWaterfallDraft([{ discount: "10", delayMinutes: "" }]);
    expect(blankDelay.valid).toBe(false);
    expect(blankDelay.steps).toBeUndefined();
    expect(blankDelay.rowErrors[0]).toMatch(/Delay must be a whole number/);

    const overMaxDelay = validateWaterfallDraft([
      { discount: "10", delayMinutes: String(MAX_WATERFALL_DELAY_MINUTES + 1) },
    ]);
    expect(overMaxDelay.valid).toBe(false);
    expect(overMaxDelay.steps).toBeUndefined();
    expect(overMaxDelay.rowErrors[0]).toMatch(/43,200/);

    const badDiscount = validateWaterfallDraft([
      { discount: "10", delayMinutes: "15" },
      { discount: "4", delayMinutes: "1440" },
    ]);
    expect(badDiscount.valid).toBe(false);
    expect(badDiscount.steps).toBeUndefined();
    expect(badDiscount.rowErrors[0]).toBeNull();
    expect(badDiscount.rowErrors[1]).toMatch(/Discount must be a whole number/);

    const nonIncreasing = validateWaterfallDraft([
      { discount: "15", delayMinutes: "15" },
      { discount: "15", delayMinutes: "1440" },
    ]);
    expect(nonIncreasing.valid).toBe(false);
    expect(nonIncreasing.rowErrors[1]).toMatch(/higher than the previous step/);

    const backwardDelay = validateWaterfallDraft([
      { discount: "10", delayMinutes: "1440" },
      { discount: "15", delayMinutes: "60" },
    ]);
    expect(backwardDelay.valid).toBe(false);
    expect(backwardDelay.rowErrors[1]).toMatch(/shorter than the previous step/);
  });

  it("displays base discount and every validated waterfall step in the enable-all confirmation message", () => {
    const withWaterfall = formatEnableAllConfirmMessage(10, "25.00", [
      { discount_percent: 10, delay_minutes: 0 },
      { discount_percent: 15, delay_minutes: 1440 },
    ]);
    expect(withWaterfall).toContain("Enable 10% off offers");
    expect(withWaterfall).toContain("Offers below $25.00 are skipped.");
    expect(withWaterfall).toContain("Step 1: 10% off after 0 min");
    expect(withWaterfall).toContain("Step 2: 15% off after 1440 min");

    const withoutWaterfall = formatEnableAllConfirmMessage(12, "", undefined);
    expect(withoutWaterfall).toContain("Enable 12% off offers");
    expect(withoutWaterfall).toContain("No minimum price will apply.");
    expect(withoutWaterfall).not.toContain("Waterfall steps:");
  });
});
