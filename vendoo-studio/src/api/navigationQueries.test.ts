import { QueryObserver } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { createStudioQueryClient } from "./queryClient";
import { api } from "./client";
import { prefetchAssistantThread, prefetchListingReads } from "./navigationQueries";
import { photoThumbUrl } from "../photoThumb";
import type { Photo } from "./types";

afterEach(() => vi.restoreAllMocks());

it("warms bounded versioned thumbnails, deduplicates intent, and retries failed images", async () => {
  const images: { src: string; fetchPriority: string; onload: () => void; onerror: () => void }[] = [];
  vi.stubGlobal("Image", class {
    src = "";
    fetchPriority = "";
    onload = () => {};
    onerror = () => {};
    constructor() { images.push(this); }
  });
  const client = createStudioQueryClient();
  const photos = Array.from({ length: 8 }, (_, i) => ({ id: `warm-${i}`, url: `/api/photos/warm-${i}?v=1` }) as Photo);
  vi.spyOn(api.conversations, "get").mockResolvedValue({ id: "warm" } as never);
  vi.spyOn(api.conversations, "messages").mockResolvedValue([]);
  vi.spyOn(api.conversations, "photos").mockResolvedValue(photos);
  try {
    await prefetchListingReads(client, "warm");
    expect(images.map(image => image.src)).toEqual(photos.slice(0, 4).map(photoThumbUrl));
    expect(images.every(image => image.fetchPriority === "low")).toBe(true);
    await prefetchListingReads(client, "warm");
    client.setQueryData(["photos", "other"], [{ id: "other", url: "/api/photos/other" }]);
    await prefetchListingReads(client, "other");
    expect(images).toHaveLength(4);
    images.forEach(image => image.onload());
    await prefetchListingReads(client, "warm");
    expect(images).toHaveLength(4);

    const edited = { ...photos[0], url: "/api/photos/warm-0?v=2" };
    client.setQueryData(["photos", "warm"], [edited]);
    await prefetchListingReads(client, "warm");
    expect(images[4].src).toBe(photoThumbUrl(edited));
    images[4].onerror();
    await prefetchListingReads(client, "warm");
    expect(images).toHaveLength(6);
  } finally {
    images.forEach(image => image.onerror());
    client.clear();
    vi.unstubAllGlobals();
  }
});

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
