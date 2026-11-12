import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IDLE_POLL_MS, LIVE_IDLE_POLL_MS, pollMs, setLiveUpdates } from "../api/polling";
import { createStudioQueryClient, LIVE_STALE_MS, STALE_MS } from "../api/queryClient";
import { INVENTORY_REFRESH_MS } from "../api/inventoryRefresh";
import { followSellerChanges, invalidateTopics, queryKeyMatchesTopic, TOPIC_QUERY_KEYS } from "./liveUpdates";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  setLiveUpdates(false);
});

describe("queryKeyMatchesTopic", () => {
  it("maps each server topic to the screens it covers", () => {
    expect(queryKeyMatchesTopic("listings", ["conversations"])).toBe(true);
    expect(queryKeyMatchesTopic("listings", ["listing", "c1"])).toBe(true);
    expect(queryKeyMatchesTopic("photos", ["photos", "c1"])).toBe(true);
    expect(queryKeyMatchesTopic("jobs", ["jobs", "c1"])).toBe(true);
    expect(queryKeyMatchesTopic("jobs", ["queue"])).toBe(true);
    expect(queryKeyMatchesTopic("settings", ["settings-ui"])).toBe(true);
    expect(queryKeyMatchesTopic("settings", ["settings-provider"])).toBe(true);
    expect(queryKeyMatchesTopic("assistant", ["assistant", "threads"])).toBe(true);
    expect(queryKeyMatchesTopic("sales", ["marketplace-sales"])).toBe(true);
    expect(queryKeyMatchesTopic("automations", ["promotion-automations"])).toBe(true);
    expect(queryKeyMatchesTopic("sourcing", ["sourcing"])).toBe(true);
  });

  it("leaves other screens and unknown topics alone", () => {
    expect(queryKeyMatchesTopic("assistant", ["conversations"])).toBe(false);
    expect(queryKeyMatchesTopic("settings", ["settings"])).toBe(false);
    expect(queryKeyMatchesTopic("listings", ["extension-status"])).toBe(false);
    expect(queryKeyMatchesTopic("unknown", ["conversations"])).toBe(false);
    expect(queryKeyMatchesTopic("listings", [{ id: 1 }])).toBe(false);
  });

  it("covers every topic the server writes", () => {
    expect(Object.keys(TOPIC_QUERY_KEYS).sort()).toEqual(
      ["assistant", "automations", "jobs", "listings", "photos", "sales", "settings", "sourcing"]);
  });
});

describe("invalidateTopics", () => {
  it("finishes an in-flight read before refreshing changes without duplicating server work", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(["conversations"], "before");
    let complete!: (value: string) => void;
    const read = vi.fn(() => new Promise<string>(resolve => { complete = resolve; }));
    const observer = new QueryObserver(client, { queryKey: ["conversations"], queryFn: read });
    const stop = observer.subscribe(() => {});
    try {
      expect(read).toHaveBeenCalledTimes(1);
      const refreshes = [invalidateTopics(client, ["listings"]), invalidateTopics(client, ["jobs"])];
      // API reads do not consume React Query's AbortSignal. Cancelling a
      // refetch abandons its result but leaves the server using a connection.
      expect(read).toHaveBeenCalledTimes(1);
      complete("during");
      await vi.waitFor(() => expect(read).toHaveBeenCalledTimes(2));
      complete("after");
      await Promise.all(refreshes);
      expect(client.getQueryData(["conversations"])).toBe("after");
      expect(read).toHaveBeenCalledTimes(2);
    } finally { stop(); client.clear(); }
  });

  it("marks only the matching queries stale", async () => {
    const client = new QueryClient();
    for (const key of [["conversations"], ["assistant", "threads"], ["settings-ui"]]) client.setQueryData(key, 1);
    invalidateTopics(client, ["listings"]);
    expect(client.getQueryState(["conversations"])?.isInvalidated).toBe(true);
    expect(client.getQueryState(["assistant", "threads"])?.isInvalidated).toBe(false);
    expect(client.getQueryState(["settings-ui"])?.isInvalidated).toBe(false);
  });

  it("refreshes after a failed read and leaves closed screens stale", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(["conversations"], "before");
    let reject!: (error: Error) => void;
    const read = vi.fn().mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail; }))
      .mockResolvedValue("after");
    const observer = new QueryObserver(client, { queryKey: ["conversations"], queryFn: read });
    const stop = observer.subscribe(() => {});
    const refresh = invalidateTopics(client, ["listings"]);
    reject(new Error("Temporary read failure"));
    await refresh;
    expect(client.getQueryData(["conversations"])).toBe("after");
    expect(read).toHaveBeenCalledTimes(2);
    stop();
    await invalidateTopics(client, ["listings"]);
    expect(read).toHaveBeenCalledTimes(2);
    expect(client.getQueryState(["conversations"])?.isInvalidated).toBe(true);
    client.clear();
  });

  it("fetches changes arriving during the follow-up read too", async () => {
    const client = new QueryClient();
    client.setQueryData(["conversations"], "before");
    let complete!: (value: string) => void;
    const read = vi.fn(() => new Promise<string>(resolve => { complete = resolve; }));
    const observer = new QueryObserver(client, { queryKey: ["conversations"], queryFn: read });
    const stop = observer.subscribe(() => {});
    try {
      const first = invalidateTopics(client, ["listings"]);
      complete("during");
      await vi.waitFor(() => expect(read).toHaveBeenCalledTimes(2));
      const latest = invalidateTopics(client, ["listings"]);
      expect(read).toHaveBeenCalledTimes(2);
      complete("after first change");
      await vi.waitFor(() => expect(read).toHaveBeenCalledTimes(3));
      complete("latest change");
      await Promise.all([first, latest]);
      expect(client.getQueryData(["conversations"])).toBe("latest change");
    } finally { stop(); client.clear(); }
  });
});

function fakeSupabase() {
  let onChange: (payload: { new?: { topic?: unknown } }) => void = () => {};
  let onStatus: (status: string) => void = () => {};
  const channel = {
    on: vi.fn((_type: string, _filter: unknown, callback: typeof onChange) => { onChange = callback; return channel; }),
    subscribe: vi.fn((callback: typeof onStatus) => { onStatus = callback; return channel; }),
  };
  const client = {
    channel: vi.fn(() => channel),
    removeChannel: vi.fn(async () => "ok"),
    realtime: { setAuth: vi.fn(async () => {}) },
  };
  return { client, channel, change: (topic: unknown) => onChange({ new: { topic } }), status: (value: string) => onStatus(value) };
}

describe("followSellerChanges", () => {
  it("subscribes to the seller's own feed and relaxes polling only while subscribed", async () => {
    vi.stubGlobal("document", { hasFocus: () => true });
    const fake = fakeSupabase();
    const stop = followSellerChanges(fake.client as never, "seller-1", "token", new QueryClient());
    await Promise.resolve();
    expect(fake.client.realtime.setAuth).toHaveBeenCalledWith("token");
    expect(fake.channel.on).toHaveBeenCalledWith("postgres_changes",
      { event: "*", schema: "public", table: "seller_changes", filter: "user_id=eq.seller-1" }, expect.any(Function));
    expect(pollMs(IDLE_POLL_MS)).toBe(IDLE_POLL_MS);
    fake.status("SUBSCRIBED");
    expect(pollMs(IDLE_POLL_MS)).toBe(LIVE_IDLE_POLL_MS);
    fake.status("CHANNEL_ERROR");
    expect(pollMs(IDLE_POLL_MS)).toBe(IDLE_POLL_MS);
    fake.status("SUBSCRIBED");
    stop();
    expect(pollMs(IDLE_POLL_MS)).toBe(IDLE_POLL_MS);
    expect(fake.client.removeChannel).toHaveBeenCalledWith(fake.channel);
  });

  it("keeps read data for a minute only while the feed is subscribed", async () => {
    const fake = fakeSupabase();
    const client = createStudioQueryClient();
    const stale = () => client.getDefaultOptions().queries?.staleTime;
    const stop = followSellerChanges(fake.client as never, "seller-1", "token", client);
    await Promise.resolve();
    expect(stale()).toBe(STALE_MS);
    fake.status("SUBSCRIBED");
    expect(stale()).toBe(LIVE_STALE_MS);
    expect(client.getDefaultOptions().queries?.networkMode).toBe("always");
    fake.status("CHANNEL_ERROR");
    expect(stale()).toBe(STALE_MS);
    fake.status("SUBSCRIBED");
    stop();
    expect(stale()).toBe(STALE_MS);
  });

  it("refreshes the topic's screens once per burst", async () => {
    vi.useFakeTimers();
    const fake = fakeSupabase();
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const stop = followSellerChanges(fake.client as never, "seller-1", "token", client);
    await Promise.resolve();
    fake.status("SUBSCRIBED");
    fake.change("jobs");
    fake.change("jobs");
    fake.change(42);
    expect(invalidate).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(300);
    // The topic's screens, then the inventory list on the schedule it shares with the app's own saves.
    expect(invalidate).toHaveBeenCalledTimes(2);
    stop();
    fake.change("jobs");
    await vi.advanceTimersByTimeAsync(300);
    expect(invalidate).toHaveBeenCalledTimes(2);
  });

  it("re-reads the inventory list at most once per window while a job streams changes", async () => {
    vi.useFakeTimers();
    const fake = fakeSupabase();
    const client = new QueryClient();
    const inventory = vi.fn(async () => []);
    const jobs = vi.fn(async () => []);
    const observers = [
      new QueryObserver(client, { queryKey: ["conversations"], queryFn: inventory, staleTime: Infinity }),
      new QueryObserver(client, { queryKey: ["jobs"], queryFn: jobs, staleTime: Infinity }),
    ];
    const unsubscribe = observers.map((observer) => observer.subscribe(() => {}));
    await vi.advanceTimersByTimeAsync(0);
    expect(inventory).toHaveBeenCalledTimes(1);
    const stop = followSellerChanges(fake.client as never, "seller-1", "token", client);
    await Promise.resolve();
    fake.status("SUBSCRIBED");
    // Ten bursts, one every 400 ms: jobs follow each one, the list at most every five seconds.
    for (let burst = 0; burst < 10; burst += 1) {
      fake.change("jobs");
      await vi.advanceTimersByTimeAsync(400);
    }
    expect(jobs).toHaveBeenCalledTimes(11);
    // The first read, then the first burst's.
    expect(inventory).toHaveBeenCalledTimes(2);
    // The last change is still fetched once the window passes, and nothing after it.
    await vi.advanceTimersByTimeAsync(INVENTORY_REFRESH_MS);
    expect(inventory).toHaveBeenCalledTimes(3);
    await vi.advanceTimersByTimeAsync(INVENTORY_REFRESH_MS * 2);
    expect(inventory).toHaveBeenCalledTimes(3);
    stop();
    unsubscribe.forEach((off) => off());
  });

  it("catches up on everything after the channel reconnects", async () => {
    const fake = fakeSupabase();
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const stop = followSellerChanges(fake.client as never, "seller-1", "token", client);
    await Promise.resolve();
    fake.status("SUBSCRIBED");
    expect(invalidate).not.toHaveBeenCalled();
    fake.status("TIMED_OUT");
    fake.status("SUBSCRIBED");
    expect(invalidate).toHaveBeenCalledTimes(1);
    stop();
  });
});
