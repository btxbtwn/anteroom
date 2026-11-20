import { afterEach, expect, it, vi } from "vitest";

const studioFetch = vi.fn();
vi.mock("./transport", () => ({ studioFetch }));
afterEach(() => { vi.useRealTimers(); vi.resetModules(); studioFetch.mockReset(); });

it("sends the error's name, message, stack and release", async () => {
  studioFetch.mockResolvedValue({ ok: true });
  const { reportError } = await import("./errorReports");
  reportError("render", new TypeError("x is undefined"));
  const body = JSON.parse(studioFetch.mock.calls[0][1].body);
  expect(studioFetch.mock.calls[0][0]).toBe("/api/error-reports");
  expect(body).toMatchObject({ kind: "render", name: "TypeError", message: "x is undefined" });
  expect(body.stack).toContain("x is undefined");
  expect(typeof body.release).toBe("string");
});

it("groups repeats without silently stopping reports after twenty errors", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(100_000);
  studioFetch.mockResolvedValue({ ok: true });
  const { reportError } = await import("./errorReports");
  reportError("crash", new Error("same"));
  reportError("crash", new Error("same"));
  expect(studioFetch).toHaveBeenCalledTimes(1);
  vi.advanceTimersByTime(60_001);
  reportError("crash", new Error("same"));
  expect(studioFetch).toHaveBeenCalledTimes(2);
  for (let i = 0; i < 40; i++) reportError("crash", new Error(`distinct ${i}`));
  expect(studioFetch).toHaveBeenCalledTimes(42);
});

it("reports non-Error rejections and swallows a failed report", async () => {
  studioFetch.mockRejectedValue(new Error("offline"));
  const { reportError } = await import("./errorReports");
  expect(() => reportError("unhandled-rejection", { code: 7 })).not.toThrow();
  expect(JSON.parse(studioFetch.mock.calls[0][1].body).message).toBe('{"code":7}');
  await Promise.resolve();
});
