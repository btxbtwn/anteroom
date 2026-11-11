import type { QueryClient, QueryKey } from "@tanstack/react-query";

/**
 * Re-reads the queries `matches` picks. Cancelling a browser query cannot stop
 * synchronous work already running on the API, so a burst of changes must not
 * start a fresh inventory scan for each one while earlier scans still hold
 * database connections: wait for current reads, then fetch the change.
 * Overlapping refreshes share that next read; merely sharing the current read
 * could miss the newer change.
 */
export async function refreshMatching(queryClient: QueryClient, matches: (queryKey: QueryKey) => boolean): Promise<void> {
  const filters = { predicate: (query: { queryKey: QueryKey }) => matches(query.queryKey) };
  const running = queryClient.getQueryCache().findAll(filters)
    .filter(query => query.state.fetchStatus === "fetching")
    .flatMap(query => query.promise ? [query.promise] : []);
  if (running.length) await Promise.allSettled(running);
  await queryClient.invalidateQueries(filters, { cancelRefetch: false });
}

export const INVENTORY_KEY = "conversations";
/**
 * The inventory list is the heaviest read, and almost every change touches it.
 * Field saves, stream connects, finished sends and live updates re-read it at
 * most this often; the last change in a burst is always fetched.
 */
export const INVENTORY_REFRESH_MS = 5000;

const schedules = new WeakMap<QueryClient, { readAt: number; timer?: ReturnType<typeof setTimeout> }>();

/** Re-reads the inventory list now, or once the interval since the last read has passed. */
export function refreshInventory(queryClient: QueryClient): void {
  let schedule = schedules.get(queryClient);
  if (!schedule) {
    schedule = { readAt: -Infinity };
    schedules.set(queryClient, schedule);
  }
  if (schedule.timer) return;
  const read = () => {
    schedule.timer = undefined;
    schedule.readAt = Date.now();
    void refreshMatching(queryClient, (queryKey) => queryKey[0] === INVENTORY_KEY).catch(() => {});
  };
  const wait = schedule.readAt + INVENTORY_REFRESH_MS - Date.now();
  if (wait <= 0) read();
  else schedule.timer = setTimeout(read, wait);
}
