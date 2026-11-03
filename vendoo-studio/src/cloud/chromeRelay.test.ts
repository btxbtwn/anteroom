import { afterEach, describe, expect, it, vi } from "vitest";

const fetches: { url: string; options?: RequestInit }[] = [];
let responses: Response[] = [];
vi.mock("../api/transport", () => ({
  studioFetch: vi.fn(async (url: string, options?: RequestInit) => { fetches.push({ url, options }); return responses.shift()!; }),
}));

import { relayOnce, startChromeRelay } from "./chromeRelay";
import { cloudExtension } from "../api/cloudExtension";
import { studioFetch } from "../api/transport";

const command = { type: "marketplace.execute", job_id: "job-1", message_id: "a".repeat(32),
  payload: { marketplace: "poshmark", action: "list", confirm: true, photos: [{ id: "0f1e", mime_type: "image/jpeg" }] } };

afterEach(() => { fetches.length = 0; responses = []; });

describe("Chrome relay", () => {
  it("keeps heartbeats running during a long read and stops them when the tab disconnects", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("window", { setInterval, clearInterval, setTimeout });
    const fetch = vi.mocked(studioFetch);
    const previous = fetch.getMockImplementation();
    const calls: string[] = [];
    const read = { type: "marketplace.sales_history", job_id: null, message_id: "d".repeat(32), payload: { marketplace: "depop", settled: [] } };
    let finish!: (reply: Record<string, unknown>) => void;
    const bridge = { runMarketplaceCommand: vi.fn(() => new Promise<Record<string, unknown>>(resolve => { finish = resolve; })) };
    fetch.mockImplementation(async url => {
      calls.push(String(url));
      return Response.json(String(url).includes("/next?") ? read : { ok: true });
    });
    let stop: (() => void) | undefined;
    try {
      stop = startChromeRelay(bridge, ["marketplace.actions", "marketplace.sales_history"]);
      await vi.advanceTimersByTimeAsync(35000);
      expect(bridge.runMarketplaceCommand).toHaveBeenCalledTimes(1);
      const beats = calls.filter(url => url.endsWith("/heartbeat")).length;
      expect(beats).toBeGreaterThanOrEqual(4);
      stop();
      await vi.advanceTimersByTimeAsync(20000);
      expect(calls.filter(url => url.endsWith("/heartbeat"))).toHaveLength(beats);
      finish({ ok: true, orders: [], more: false });
      await vi.advanceTimersByTimeAsync(0);
    } finally {
      stop?.();
      fetch.mockImplementation(previous!);
      vi.unstubAllGlobals();
      vi.useRealTimers();
    }
  });

  it("keeps the connected Bridge's sales reader through status, command delivery and reply", async () => {
    const origin = "https://myanteroom.com";
    const listeners = new Set<(event: MessageEvent) => void>();
    const target = {
      location: { origin }, setTimeout, clearTimeout,
      addEventListener: (_name: string, listener: (event: MessageEvent) => void) => listeners.add(listener),
      removeEventListener: (_name: string, listener: (event: MessageEvent) => void) => listeners.delete(listener),
      postMessage: (data: Record<string, unknown>) => queueMicrotask(() => {
        const response = { source: "anteroom-extension", version: 1, requestId: data.requestId, ok: true,
          state: "connected", extensionVersion: "0.2.50",
          supportedActions: ["marketplace.actions", "marketplace.sales_history", "unknown.capability"],
          reply: { ok: true, orders: [], more: false } };
        for (const listener of listeners) listener({ source: target, origin, data: response } as unknown as MessageEvent);
      }),
    } as unknown as Window;
    const bridge = cloudExtension({ id: "11111111-1111-4111-8111-111111111111", email: "alice@example.com" }, target);
    const connection = await bridge.status();
    const read = { type: "marketplace.sales_history", job_id: null, message_id: "c".repeat(32), payload: { marketplace: "depop", settled: [] } };
    responses = [Response.json(read), Response.json({ ok: true })];

    await relayOnce(bridge, connection.supportedActions ?? []);

    expect(fetches[0].url).toBe("/api/browser-relay/next?capabilities=marketplace.actions,marketplace.sales_history");
    expect(fetches).toHaveLength(2);
    expect(JSON.parse(String(fetches[1].options?.body))).toEqual({ request_id: read.message_id,
      payload: { ok: true, orders: [], more: false } });
    expect(listeners.size).toBe(0);
  });

  it("asks for the next command, sends it to Chrome with its approved photos, and posts the reply", async () => {
    responses = [Response.json(command), new Response(new Uint8Array([1, 2, 3])), Response.json({ ok: true })];
    const bridge = { runMarketplaceCommand: vi.fn(async () => ({ ok: true, state: "published" })) };
    expect(await relayOnce(bridge, ["marketplace.actions", "poshmark.form.read"])).toBe(true);
    expect(fetches[0].url).toBe("/api/browser-relay/next?capabilities=marketplace.actions");
    expect(fetches[1].url).toBe("/api/browser-relay/jobs/job-1/photos/0f1e");
    expect(bridge.runMarketplaceCommand).toHaveBeenCalledWith(command, [{ id: "0f1e", mime_type: "image/jpeg", base64: "AQID" }]);
    expect(fetches[2].url).toBe("/api/browser-relay/replies");
    expect(JSON.parse(String(fetches[2].options?.body))).toEqual({ request_id: "a".repeat(32), payload: { ok: true, state: "published" } });
  });

  it("does nothing when no command is waiting", async () => {
    responses = [new Response(null, { status: 204 })];
    const bridge = { runMarketplaceCommand: vi.fn() };
    expect(await relayOnce(bridge, [])).toBe(false);
    expect(fetches[0].url).toBe("/api/browser-relay/next?capabilities=");
    expect(bridge.runMarketplaceCommand).not.toHaveBeenCalled();
  });

  it("reports a photo it could not read as safe to retry, without reaching Chrome", async () => {
    responses = [Response.json(command), new Response(null, { status: 404 }), Response.json({ ok: true })];
    const bridge = { runMarketplaceCommand: vi.fn() };
    await relayOnce(bridge, ["marketplace.actions"]);
    expect(bridge.runMarketplaceCommand).not.toHaveBeenCalled();
    expect(JSON.parse(String(fetches[2].options?.body)).payload).toMatchObject({ ok: false, safe_to_retry: true });
  });

  it("declares reads, and relays a read without photos", async () => {
    const read = { type: "marketplace.vinted_categories", job_id: null, message_id: "b".repeat(32), payload: { request_id: "b".repeat(32) } };
    responses = [Response.json(read), Response.json({ ok: true })];
    const bridge = { runMarketplaceCommand: vi.fn(async () => ({ ok: true, options: ["Women > Jeans"] })) };
    await relayOnce(bridge, ["marketplace.actions", "vinted.choices"]);
    expect(fetches[0].url).toBe("/api/browser-relay/next?capabilities=marketplace.actions,vinted.choices");
    expect(bridge.runMarketplaceCommand).toHaveBeenCalledWith(read, []);
    expect(JSON.parse(String(fetches[1].options?.body))).toEqual({ request_id: "b".repeat(32), payload: { ok: true, options: ["Women > Jeans"] } });
  });
});
