import { QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IDLE_POLL_MS, LIVE_IDLE_POLL_MS, pollMs, setLiveUpdates } from "../api/polling";
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
  it("marks only the matching queries stale", async () => {
    const client = new QueryClient();
    for (const key of [["conversations"], ["assistant", "threads"], ["settings-ui"]]) client.setQueryData(key, 1);
    invalidateTopics(client, ["listings"]);
    expect(client.getQueryState(["conversations"])?.isInvalidated).toBe(true);
    expect(client.getQueryState(["assistant", "threads"])?.isInvalidated).toBe(false);
    expect(client.getQueryState(["settings-ui"])?.isInvalidated).toBe(false);
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
    vi.advanceTimersByTime(300);
    expect(invalidate).toHaveBeenCalledTimes(1);
    stop();
    fake.change("jobs");
    vi.advanceTimersByTime(300);
    expect(invalidate).toHaveBeenCalledTimes(1);
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
