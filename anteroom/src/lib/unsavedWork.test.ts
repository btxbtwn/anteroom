import { afterEach, describe, expect, it, vi } from "vitest";
import { commitUnsavedWork, hasUnsavedWork, installUnsavedWorkGuard, registerUnsavedWork, subscribeUnsavedWork } from "./unsavedWork";

const cleanups: (() => void)[] = [];
afterEach(() => cleanups.splice(0).forEach((cleanup) => cleanup()));

function source(initial: boolean) {
  const state = { pending: initial, commits: 0 };
  cleanups.push(registerUnsavedWork({
    pending: () => state.pending,
    commit: () => { state.commits += 1; },
  }));
  return state;
}

function fakeWindow() {
  const target = new EventTarget();
  return target as unknown as Window;
}

describe("unsaved work", () => {
  it("is pending while any source holds work, and tells subscribers when that changes", () => {
    const listener = vi.fn();
    cleanups.push(subscribeUnsavedWork(listener));
    expect(hasUnsavedWork()).toBe(false);
    const typed = source(true);
    expect(hasUnsavedWork()).toBe(true);
    expect(listener).toHaveBeenCalledTimes(1);
    typed.pending = false;
    commitUnsavedWork();
    expect(typed.commits).toBe(1);
    expect(hasUnsavedWork()).toBe(false);
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("asks before leaving only while something is still unsaved", () => {
    const win = fakeWindow();
    cleanups.push(installUnsavedWorkGuard(win));
    const leave = () => {
      const event = new Event("beforeunload", { cancelable: true }) as BeforeUnloadEvent;
      win.dispatchEvent(event);
      return event.defaultPrevented;
    };
    expect(leave()).toBe(false);
    const saving = source(true);
    expect(leave()).toBe(true);
    // Leaving saves typed fields first.
    expect(saving.commits).toBe(1);
    saving.pending = false;
    expect(leave()).toBe(false);
  });

  it("saves typed fields when the page is hidden", () => {
    const win = fakeWindow();
    cleanups.push(installUnsavedWorkGuard(win));
    const typed = source(true);
    win.dispatchEvent(new Event("pagehide"));
    expect(typed.commits).toBe(1);
  });
});
