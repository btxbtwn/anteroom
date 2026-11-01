import { describe, expect, it } from "vitest";
import { isMacApp, markMacApp } from "./macApp";

function fakeWindow(mac: boolean) {
  const classes = new Set<string>();
  const listeners = new Map<string, () => void>();
  const target = {
    document: { documentElement: { classList: { add: (name: string) => classes.add(name) } } },
    addEventListener: (name: string, listener: () => void) => listeners.set(name, listener),
    ...(mac ? { pywebview: {} } : {}),
  };
  return { target: target as unknown as Window, classes, listeners };
}

describe("Mac app window", () => {
  it("is recognized by the bridge the window injects", () => {
    expect(isMacApp(fakeWindow(true).target)).toBe(true);
    expect(isMacApp(fakeWindow(false).target)).toBe(false);
  });

  it("leaves room for the traffic lights once the window is ready", () => {
    const page = fakeWindow(false);
    markMacApp(page.target);
    expect(page.classes.has("desktop-app")).toBe(false);
    page.listeners.get("pywebviewready")?.();
    expect(page.classes.has("desktop-app")).toBe(true);
  });
});
