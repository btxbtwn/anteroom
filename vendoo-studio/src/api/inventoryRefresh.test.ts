import { afterEach, expect, it, vi } from "vitest";
import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { INVENTORY_REFRESH_MS, refreshInventory } from "./inventoryRefresh";

afterEach(() => { vi.useRealTimers(); });

function watchedInventory() {
  const client = new QueryClient();
  const read = vi.fn(async () => [{ id: "a" }]);
  const observer = new QueryObserver(client, { queryKey: ["conversations"], queryFn: read, staleTime: Infinity });
  const stop = observer.subscribe(() => {});
  return { client, read, stop };
}

it("re-reads the inventory at once, then at most once per interval with the last change", async () => {
  vi.useFakeTimers();
  const { client, read, stop } = watchedInventory();
  await vi.advanceTimersByTimeAsync(0);
  expect(read).toHaveBeenCalledTimes(1);

  refreshInventory(client);
  await vi.advanceTimersByTimeAsync(0);
  expect(read).toHaveBeenCalledTimes(2);

  // A burst of saves and stream connects within the interval: one trailing read.
  for (let i = 0; i < 10; i += 1) refreshInventory(client);
  await vi.advanceTimersByTimeAsync(INVENTORY_REFRESH_MS - 1);
  expect(read).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(1);
  expect(read).toHaveBeenCalledTimes(3);
  await vi.advanceTimersByTimeAsync(INVENTORY_REFRESH_MS * 2);
  expect(read).toHaveBeenCalledTimes(3);
  stop();
  client.clear();
});

it("keeps each workspace's schedule separate", async () => {
  vi.useFakeTimers();
  const first = watchedInventory();
  const second = watchedInventory();
  await vi.advanceTimersByTimeAsync(0);
  refreshInventory(first.client);
  refreshInventory(second.client);
  await vi.advanceTimersByTimeAsync(0);
  expect(first.read).toHaveBeenCalledTimes(2);
  expect(second.read).toHaveBeenCalledTimes(2);
  first.stop(); second.stop();
});
