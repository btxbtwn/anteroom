// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { StartupScreen } from "./StartupScreen";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement;
let root: Root;
beforeEach(() => {
  vi.useFakeTimers();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.useRealTimers();
});

it("announces startup and offers recovery only after a long wait", () => {
  const reload = vi.fn();
  act(() => root.render(<StartupScreen onReload={reload} />));
  expect(host.querySelector("h1")?.textContent).toBe("Anteroom");
  expect(host.querySelector('[role="status"]')?.textContent).toBe("Getting things ready…");
  expect(host.querySelector("button")).toBeNull();
  act(() => vi.advanceTimersByTime(10_000));
  expect(host.querySelector('[role="status"]')?.textContent).toContain("Taking longer than usual");
  act(() => host.querySelector("button")!.click());
  expect(reload).toHaveBeenCalledOnce();
});

it("replaces activity with an actionable error and keeps the error after the delay", () => {
  const reload = vi.fn();
  act(() => root.render(<StartupScreen error="Could not open your workspace. Reload to try again." onReload={reload} />));
  act(() => vi.advanceTimersByTime(10_000));
  expect(host.querySelector('[role="alert"]')?.textContent).toContain("Could not open your workspace");
  expect(host.querySelector('[role="status"]')).toBeNull();
  expect(host.querySelector(".startup-spinner")).toBeNull();
  act(() => host.querySelector("button")!.click());
  expect(reload).toHaveBeenCalledOnce();
});

it("clears the long-wait timer when the workspace opens", () => {
  act(() => root.render(<StartupScreen />));
  expect(vi.getTimerCount()).toBe(1);
  act(() => root.unmount());
  expect(vi.getTimerCount()).toBe(0);
  root = createRoot(host);
});
