import { describe, expect, it } from "vitest";
import type { ProviderUsageLimits, ProviderUsageWindow } from "../../api/types";
import { isUsageLimitsCommand } from "../chat/ComposerUsageLimits";
import {
  formatDuration,
  formatResetsIn,
  limitsNotice,
  paceOf,
  paceRemainingPercent,
  remainingPercent,
} from "./limitWindows";

const NOW = Date.parse("2026-10-21T12:00:00Z");

function window(overrides: Partial<ProviderUsageWindow> = {}): ProviderUsageWindow {
  return { id: "five_hour", kind: "session", label: "Session", used_percent: 40, ...overrides };
}

function limits(overrides: Partial<ProviderUsageLimits> = {}): ProviderUsageLimits {
  return { provider: "claude", label: "Claude", checked_at: "2026-10-21T12:00:00Z", windows: [], ...overrides };
}

describe("limit windows", () => {
  it("shows what is left, rounded and clamped", () => {
    expect(remainingPercent(window({ used_percent: 33.4 }))).toBe(67);
    expect(remainingPercent(window({ used_percent: 140 }))).toBe(0);
    expect(remainingPercent(window({ used_percent: -3 }))).toBe(100);
  });

  it("words the reset the way T3 does", () => {
    const resets_at = new Date(NOW + (2 * 60 + 5) * 60_000).toISOString();
    expect(formatResetsIn(window({ resets_at }), NOW)).toBe("resets in 2h 5m");
    expect(formatResetsIn(window({ resets_at: new Date(NOW + 30_000).toISOString() }), NOW)).toBe("Resets momentarily");
    expect(formatResetsIn(window({ resets_at: new Date(NOW - 1).toISOString() }), NOW)).toBe("resets now");
    expect(formatResetsIn(window(), NOW)).toBeNull();
    expect(formatDuration(3 * 86_400_000 + 4 * 3_600_000)).toBe("3d 4h");
  });

  it("compares spending with the time left in the window", () => {
    // Halfway through a five-hour window.
    const half = { resets_at: new Date(NOW + 150 * 60_000).toISOString(), window_duration_mins: 300 };
    expect(paceRemainingPercent(window(half), NOW)).toBe(50);
    expect(paceOf(window({ ...half, used_percent: 80 }), NOW)).toBe("ahead");
    expect(paceOf(window({ ...half, used_percent: 52 }), NOW)).toBe("on");
    expect(paceOf(window({ ...half, used_percent: 10 }), NOW)).toBe("under");
    expect(paceOf(window({ resets_at: half.resets_at }), NOW)).toBeNull();
  });

  it("says plainly when a provider has no numbers", () => {
    expect(limitsNotice(limits({ unavailable: { reason: "unsupported", message: "Xiaomi MiMo does not report usage limits." } }))).toBe(
      "Xiaomi MiMo does not report usage limits.",
    );
    expect(limitsNotice(limits({ unavailable: { reason: "probeFailed" } }))).toBe("Could not read limits.");
    expect(limitsNotice(limits())).toBe("No limits reported.");
    expect(limitsNotice(limits({ windows: [window()] }))).toBeNull();
  });

  it("recognises the composer command", () => {
    expect(isUsageLimitsCommand("  /Usage-Limits ")).toBe(true);
    expect(isUsageLimitsCommand("/usage-limits please")).toBe(false);
  });
});
