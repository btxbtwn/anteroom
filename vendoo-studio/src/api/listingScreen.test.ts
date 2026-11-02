import { expect, it, vi } from "vitest";
import { api } from "./client";
import { installHostedApiSession } from "./transport";

it("shares one hosted screen request across the core listing reads", async () => {
  const stop = installHostedApiSession({ origin: "https://app.test", accessToken: async () => "token", unauthorized: () => {} });
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
  const stop = installHostedApiSession({ origin: "https://app.test", accessToken: async () => "token", unauthorized: () => {} });
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
  const oldStop = installHostedApiSession({ origin: "https://app.test", accessToken: async () => "old", unauthorized: () => {} });
  const fetch = vi.fn().mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; }))
    .mockImplementation(async () => new Response(JSON.stringify({ conversation: { id: "new" }, listing: {} })));
  vi.stubGlobal("fetch", fetch);
  const old = Promise.allSettled([api.conversations.get("coat"), api.listings.get("coat")]);
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  const newStop = installHostedApiSession({ origin: "https://app.test", accessToken: async () => "new", unauthorized: () => {} });
  try {
    const fresh = await Promise.all([api.conversations.get("coat"), api.listings.get("coat")]);
    expect(fresh).toEqual([{ id: "new" }, {}]);
    finish(new Response(JSON.stringify({ conversation: { id: "old" }, listing: {} })));
    expect((await old).every(result => result.status === "rejected")).toBe(true);
    expect(fetch).toHaveBeenCalledTimes(2);
  } finally { oldStop(); newStop(); vi.unstubAllGlobals(); }
});
