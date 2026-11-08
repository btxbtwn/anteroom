import { expect, it, vi } from "vitest";
import { api } from "./client";
import { installHostedApiSession } from "./transport";

it("shares one hosted screen request across the core listing reads", async () => {
  const stop = installHostedApiSession({ seller: "seller", origin: "https://app.test", accessToken: async () => "token", unauthorized: () => {} });
  const screen = { conversation: { id: "coat" }, listing: { listing: { title: "Coat" } }, photos: [], messages: [], jobs: [], activity: { busy: false } };
  const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify(screen)));
  vi.stubGlobal("fetch", fetch);
  try {
    const results = await Promise.all([api.conversations.get("coat"), api.listings.get("coat"), api.conversations.photos("coat"),
      api.conversations.messages("coat"), api.jobs.list("coat"), api.conversations.activity("coat")]);
    expect(results).toEqual([screen.conversation, screen.listing, [], [], [], screen.activity]);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][0]).toBe("https://app.test/api/conversations/coat/screen");
  } finally { stop(); vi.unstubAllGlobals(); }
});

it("keeps a single polling read narrow and retries failed batches", async () => {
  const stop = installHostedApiSession({ seller: "seller", origin: "https://app.test", accessToken: async () => "token", unauthorized: () => {} });
  const fetch = vi.fn().mockImplementation(async () => new Response("[]"));
  vi.stubGlobal("fetch", fetch);
  try {
    await api.conversations.photos("coat");
    expect(fetch.mock.calls[0][0]).toBe("https://app.test/api/conversations/coat/photos");
    fetch.mockImplementation(async () => new Response("{}", { status: 503 }));
    const failed = await Promise.allSettled([api.conversations.get("coat"), api.listings.get("coat")]);
    expect(failed.every(result => result.status === "rejected")).toBe(true);
    fetch.mockImplementation(async () => new Response(JSON.stringify({ conversation: { id: "coat" }, listing: {} })));
    expect(await Promise.all([api.conversations.get("coat"), api.listings.get("coat")])).toEqual([{ id: "coat" }, {}]);
  } finally { stop(); vi.unstubAllGlobals(); }
});

it("never shares an in-flight screen across accounts", async () => {
  let finish!: (response: Response) => void;
  const oldStop = installHostedApiSession({ seller: "seller", origin: "https://app.test", accessToken: async () => "old", unauthorized: () => {} });
  const fetch = vi.fn().mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; }))
    .mockImplementation(async () => new Response(JSON.stringify({ conversation: { id: "new" }, listing: {} })));
  vi.stubGlobal("fetch", fetch);
  const old = Promise.allSettled([api.conversations.get("coat"), api.listings.get("coat")]);
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  const newStop = installHostedApiSession({ seller: "seller", origin: "https://app.test", accessToken: async () => "new", unauthorized: () => {} });
  try {
    const fresh = await Promise.all([api.conversations.get("coat"), api.listings.get("coat")]);
    expect(fresh).toEqual([{ id: "new" }, {}]);
    finish(new Response(JSON.stringify({ conversation: { id: "old" }, listing: {} })));
    expect((await old).every(result => result.status === "rejected")).toBe(true);
    expect(fetch).toHaveBeenCalledTimes(2);
  } finally { oldStop(); newStop(); vi.unstubAllGlobals(); }
});

it("coalesces a cold editor open without requiring a warm-up call", async () => {
  const stop = installHostedApiSession({ seller: "seller", origin: "https://app.test", accessToken: async () => "token", unauthorized: () => {} });
  const screen = { conversation: { id: "coat" }, listing: { listing: { title: "Coat" } } };
  const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify(screen)));
  vi.stubGlobal("fetch", fetch);
  try {
    expect(await Promise.all([api.conversations.get("coat"), api.listings.get("coat")])).toEqual([screen.conversation, screen.listing]);
    expect(fetch.mock.calls.map(call => call[0])).toEqual([
      "https://app.test/api/conversations/coat/screen",
    ]);
  } finally { stop(); vi.unstubAllGlobals(); }
});

it("passes a query's signal through to fetch", async () => {
  const fetch = vi.fn().mockImplementation(async () => new Response("[]"));
  vi.stubGlobal("fetch", fetch);
  try {
    const controller = new AbortController();
    await api.conversations.list({ signal: controller.signal });
    expect(fetch.mock.calls[0][1].signal).toBe(controller.signal);
    const stop = installHostedApiSession({ seller: "seller", origin: "https://app.test", accessToken: async () => "token", unauthorized: () => {} });
    try {
      await api.conversations.photos("coat", undefined, { signal: controller.signal });
      expect(fetch.mock.calls[1][0]).toBe("https://app.test/api/conversations/coat/photos");
      expect(fetch.mock.calls[1][1].signal.aborted).toBe(false);
      controller.abort();
      await expect(api.conversations.photos("coat", undefined, { signal: controller.signal })).rejects.toThrow();
      expect(fetch).toHaveBeenCalledTimes(2);
    } finally { stop(); }
  } finally { vi.unstubAllGlobals(); }
});

it("keeps a shared screen read running until every caller has aborted", async () => {
  const stop = installHostedApiSession({ seller: "seller", origin: "https://app.test", accessToken: async () => "token", unauthorized: () => {} });
  const signals: AbortSignal[] = [];
  let finish!: (response: Response) => void;
  const fetch = vi.fn().mockImplementation((_url: string, init: RequestInit) => {
    signals.push(init.signal!);
    return new Promise<Response>((resolve, reject) => {
      finish = resolve;
      init.signal!.addEventListener("abort", () => reject(init.signal!.reason));
    });
  });
  vi.stubGlobal("fetch", fetch);
  try {
    const first = new AbortController();
    const second = new AbortController();
    const closing = api.conversations.get("coat", { signal: first.signal });
    const staying = api.listings.get("coat", { signal: second.signal });
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(fetch.mock.calls[0][0]).toBe("https://app.test/api/conversations/coat/screen");
    first.abort();
    await expect(closing).rejects.toMatchObject({ name: "AbortError" });
    expect(signals[0]!.aborted).toBe(false);
    finish(new Response(JSON.stringify({ conversation: { id: "coat" }, listing: { listing: { title: "Coat" } } })));
    expect(await staying).toEqual({ listing: { title: "Coat" } });

    const third = new AbortController();
    const fourth = new AbortController();
    const reads = Promise.allSettled([
      api.conversations.get("coat", { signal: third.signal }),
      api.listings.get("coat", { signal: fourth.signal }),
    ]);
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    third.abort();
    expect(signals[1]!.aborted).toBe(false);
    fourth.abort();
    expect(signals[1]!.aborted).toBe(true);
    expect((await reads).every((result) => result.status === "rejected")).toBe(true);
  } finally { stop(); vi.unstubAllGlobals(); }
});

it("never aborts a shared read that a caller without a signal is waiting on", async () => {
  const stop = installHostedApiSession({ seller: "seller", origin: "https://app.test", accessToken: async () => "token", unauthorized: () => {} });
  let finish!: (response: Response) => void;
  let signal!: AbortSignal;
  const fetch = vi.fn().mockImplementation((_url: string, init: RequestInit) => {
    signal = init.signal!;
    return new Promise<Response>((resolve) => { finish = resolve; });
  });
  vi.stubGlobal("fetch", fetch);
  try {
    const controller = new AbortController();
    const cancelled = api.conversations.get("coat", { signal: controller.signal });
    const prefetch = api.listings.get("coat");
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    controller.abort();
    await expect(cancelled).rejects.toMatchObject({ name: "AbortError" });
    expect(signal.aborted).toBe(false);
    finish(new Response(JSON.stringify({ conversation: { id: "coat" }, listing: {} })));
    expect(await prefetch).toEqual({});
  } finally { stop(); vi.unstubAllGlobals(); }
});
