import { QueryObserver } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { createStudioQueryClient } from "./queryClient";
import { api } from "./client";
import { prefetchAssistantThread, prefetchListingReads } from "./navigationQueries";

afterEach(() => vi.restoreAllMocks());

it("warms only safe listing reads and reuses them on mount", async () => {
  const client = createStudioQueryClient();
  const get = vi.spyOn(api.conversations, "get").mockResolvedValue({ id: "item" } as never);
  vi.spyOn(api.conversations, "photos").mockResolvedValue([]);
  vi.spyOn(api.conversations, "messages").mockResolvedValue([]);
  const listing = vi.spyOn(api.listings, "get");
  try {
    await prefetchListingReads(client, "item");
    await prefetchListingReads(client, "item");
    expect(get).toHaveBeenCalledExactlyOnceWith("item");
    expect(client.getQueryData(["conversation", "item"])).toEqual({ id: "item" });
    expect(client.getQueryData(["photos", "item"])).toEqual([]);
    expect(client.getQueryData(["messages", "item"])).toEqual([]);
    expect(listing).not.toHaveBeenCalled();
  } finally { client.clear(); }
});

it("mounting a prefetched thread shows its messages synchronously without another request", async () => {
  const client = createStudioQueryClient();
  const messages = [{ id: "answer", role: "assistant" as const, text: "Saved answer", created_at: null }];
  const read = vi.spyOn(api.assistant, "messages").mockResolvedValue(messages);
  try {
    await prefetchAssistantThread(client, "thread");
    const observer = new QueryObserver(client, {
      queryKey: ["assistant", "messages", "thread"], queryFn: () => api.assistant.messages("thread"),
    });
    const unsubscribe = observer.subscribe(() => {});
    expect(observer.getCurrentResult().data).toEqual(messages);
    expect(observer.getCurrentResult().isFetching).toBe(false);
    expect(read).toHaveBeenCalledExactlyOnceWith("thread");
    unsubscribe();
  } finally { client.clear(); }
});

it("a failed warmup can be retried when the seller opens the destination", async () => {
  const client = createStudioQueryClient();
  const read = vi.spyOn(api.assistant, "messages").mockRejectedValueOnce(new Error("Temporary failure")).mockResolvedValue([]);
  try {
    await prefetchAssistantThread(client, "thread");
    await prefetchAssistantThread(client, "thread");
    expect(read).toHaveBeenCalledTimes(2);
    expect(client.getQueryData(["assistant", "messages", "thread"])).toEqual([]);
  } finally { client.clear(); }
});
