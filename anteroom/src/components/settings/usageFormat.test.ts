import { expect, it } from "vitest";
import { counted, dayLabel, formatCost, periodDates, periodLabel } from "./usageFormat";

it("writes costs to the cent and never rounds a real charge down to nothing", () => {
  expect(formatCost(0)).toBe("$0.00");
  expect(formatCost(0.3)).toBe("$0.30");
  expect(formatCost(0.3245)).toBe("$0.32");
  expect(formatCost(1234.5)).toBe("$1,234.50");
  expect(formatCost(0.01)).toBe("$0.01");
  expect(formatCost(0.0042)).toBe("$0.0042");
  expect(formatCost(0.00042519)).toBe("$0.00043");
});

it("names periods and days in UTC whatever the device's time zone", () => {
  expect(periodLabel("30d")).toBe("Last 30 days");
  expect(periodLabel("2026-11")).toBe("November 2026");
  expect(periodLabel("2027-01")).toBe("January 2027");
  // The end is exclusive: October ends on the 31st, not on November 1.
  expect(periodDates("2026-10-01T00:00:00+00:00", "2026-11-01T00:00:00+00:00")).toBe("Oct 1 – 31, 2026");
  expect(periodDates("2026-10-28T00:00:00+00:00", "2026-11-27T00:00:00+00:00")).toBe("Oct 28 – Nov 26, 2026");
  expect(periodDates("2026-12-03T00:00:00+00:00", "2027-01-02T00:00:00+00:00")).toBe("Dec 3, 2026 – Jan 1, 2027");
  expect(dayLabel("2026-10-01")).toBe("Oct 1");
});

it("counts in words", () => {
  expect(counted(0, "AI call")).toBe("0 AI calls");
  expect(counted(1, "photo edit")).toBe("1 photo edit");
  expect(counted(3600, "token")).toBe("3,600 tokens");
  expect(counted(48_250, "token")).toBe("48.3K tokens");
});
