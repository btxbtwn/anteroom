// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { PersistedClient } from "@tanstack/react-query-persist-client";

const saved = vi.hoisted(() => ({ writes: [] as unknown[], values: new Map<string, unknown>() }));
vi.mock("idb-keyval", () => ({
  createStore: () => ({}),
  get: async (key: string) => saved.values.get(key),
  set: async (key: string, value: unknown) => {
    if (key === "queries") saved.writes.push(value);
    saved.values.set(key, value);
  },
  del: async (key: string) => { saved.values.delete(key); },
  clear: async () => { saved.values.clear(); },
}));

const { DEVICE_SAVE_EVERY_MS, devicePersister } = await import("./deviceCache");

function snapshot(n: number): PersistedClient {
  return { timestamp: n, buster: "test", clientState: { queries: [], mutations: [] } };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("indexedDB", {});
  saved.writes.length = 0;
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

it("writes a burst of cache changes to the device once, with the latest state", async () => {
  const persister = devicePersister("seller-a");
  for (let n = 1; n <= 20; n += 1) await persister.persistClient(snapshot(n));
  await vi.advanceTimersByTimeAsync(0);
  expect(saved.writes).toEqual([]);
  await vi.advanceTimersByTimeAsync(DEVICE_SAVE_EVERY_MS);
  expect(saved.writes).toEqual([snapshot(20)]);
});

it("saves at once when the page is hidden or closed", async () => {
  const persister = devicePersister("seller-a");
  await persister.persistClient(snapshot(1));
  window.dispatchEvent(new Event("pagehide"));
  await vi.advanceTimersByTimeAsync(0);
  expect(saved.writes).toEqual([snapshot(1)]);
  await vi.advanceTimersByTimeAsync(DEVICE_SAVE_EVERY_MS);
  expect(saved.writes).toHaveLength(1);
});
