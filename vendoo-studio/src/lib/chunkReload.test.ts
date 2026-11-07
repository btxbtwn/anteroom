import { describe, expect, it, vi } from "vitest";
import { installChunkReload, isChunkLoadError, shouldReloadForChunk } from "./chunkReload";

function memoryStorage() {
  const values = new Map<string, string>();
  return { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => void values.set(key, value) };
}

describe("shouldReloadForChunk", () => {
  it("reloads once, then leaves a repeat failure to the error screen", () => {
    const storage = memoryStorage();
    expect(shouldReloadForChunk(storage, 1_000)).toBe(true);
    expect(shouldReloadForChunk(storage, 5_000)).toBe(false);
    // A later deploy gets its own reload.
    expect(shouldReloadForChunk(storage, 120_000)).toBe(true);
  });

  it("does not reload when storage is unavailable or throws", () => {
    expect(shouldReloadForChunk(null)).toBe(false);
    const blocked = { getItem: () => { throw new Error("blocked"); }, setItem: () => {} };
    expect(shouldReloadForChunk(blocked)).toBe(false);
  });
});

describe("installChunkReload", () => {
  it("reloads on vite:preloadError and marks the error handled", () => {
    const reload = vi.fn();
    const target = Object.assign(new EventTarget(), { location: { reload } }) as unknown as Window;
    vi.stubGlobal("window", { sessionStorage: memoryStorage() });
    try {
      const stop = installChunkReload(target);
      const event = new Event("vite:preloadError", { cancelable: true });
      target.dispatchEvent(event);
      expect(reload).toHaveBeenCalledTimes(1);
      expect(event.defaultPrevented).toBe(true);
      const again = new Event("vite:preloadError", { cancelable: true });
      target.dispatchEvent(again);
      expect(reload).toHaveBeenCalledTimes(1);
      expect(again.defaultPrevented).toBe(false);
      stop();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

it("recognizes failed code downloads across browsers", () => {
  expect(isChunkLoadError(new TypeError("Failed to fetch dynamically imported module: /assets/Settings.js"))).toBe(true);
  expect(isChunkLoadError(new TypeError("Importing a module script failed."))).toBe(true);
  expect(isChunkLoadError(new Error("x is undefined"))).toBe(false);
});
