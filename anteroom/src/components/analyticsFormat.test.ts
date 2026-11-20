import { describe, expect, it } from "vitest";
import { compactMoney, dayRange, formatChange, formatCurrency, formatDays, formatMoney } from "./analyticsFormat";

describe("formatCurrency", () => {
  it("always shows cents and the currency symbol", () => {
    expect(formatCurrency(502)).toBe("$502.00");
    expect(formatCurrency(279.04)).toBe("$279.04");
    expect(formatCurrency("7.2")).toBe("$7.20");
    expect(formatCurrency(-15)).toBe("-$15.00");
    expect(formatCurrency("10.0001", "EUR")).toBe("€10.00");
  });

  it("keeps an unknown currency's code and never shows a made-up number", () => {
    expect(formatCurrency("5", "XYZW")).toBe("XYZW 5.00");
    expect(formatCurrency(Number.NaN)).toBe("—");
  });
});

describe("compactMoney", () => {
  it("shortens chart labels", () => {
    expect(compactMoney(163.5)).toBe("$164");
    expect(compactMoney(1250)).toBe("$1.3K");
    expect(compactMoney(0.5)).toBe("$0.5");
    expect(compactMoney(-40)).toBe("-$40");
  });
});

describe("dayRange", () => {
  it("names the year once when both days share it", () => {
    expect(dayRange("2026-09-10", "2026-10-09")).toBe("Sep 10 – Oct 9, 2026");
    expect(dayRange("2025-12-20", "2026-01-18")).toBe("Dec 20, 2025 – Jan 18, 2026");
  });
});

describe("formatMoney", () => {
  it("drops cents on a whole dollar", () => {
    expect(formatMoney(1234)).toBe("$1,234");
    expect(formatMoney(0)).toBe("$0");
  });

  it("keeps cents when the amount is not whole", () => {
    expect(formatMoney(1234.5)).toBe("$1,234.50");
  });

  it("keeps the sign on a loss", () => {
    expect(formatMoney(-15)).toBe("-$15");
  });

  it("falls back when the amount is not a number", () => {
    expect(formatMoney(Number.NaN)).toBe("$0");
  });
});

describe("formatDays", () => {
  it("pluralizes", () => {
    expect(formatDays(1)).toBe("1 day");
    expect(formatDays(18)).toBe("18 days");
  });
});

describe("formatChange", () => {
  it("compares positive baselines", () => {
    expect(formatChange(150, 100, formatMoney)).toBe("Up $50 (50%)");
    expect(formatChange(5, 10, String)).toBe("Down 5 (50%)");
  });

  it("avoids misleading percentages for zero baselines or losses", () => {
    expect(formatChange(40, 0, formatMoney)).toBe("Up $40");
    expect(formatChange(-20, -40, formatMoney)).toBe("Up $20");
    expect(formatChange(-20, 40, formatMoney)).toBe("Down $60");
  });

  it("handles missing and unchanged values", () => {
    expect(formatChange(null, 10, formatDays)).toBe("No comparison data");
    expect(formatChange(10, null, formatDays)).toBe("No comparison data");
    expect(formatChange(0, 0, String)).toBe("No change");
  });
});
