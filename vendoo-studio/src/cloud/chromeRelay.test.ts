import { afterEach, describe, expect, it, vi } from "vitest";

const fetches: { url: string; options?: RequestInit }[] = [];
let responses: Response[] = [];
vi.mock("../api/transport", () => ({
  studioFetch: vi.fn(async (url: string, options?: RequestInit) => { fetches.push({ url, options }); return responses.shift()!; }),
}));

import { relayOnce } from "./chromeRelay";

const command = { type: "marketplace.execute", job_id: "job-1", message_id: "a".repeat(32),
  payload: { marketplace: "poshmark", action: "list", confirm: true, photos: [{ id: "0f1e", mime_type: "image/jpeg" }] } };

afterEach(() => { fetches.length = 0; responses = []; });

describe("Chrome relay", () => {
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
    await relayOnce(bridge, ["marketplace.actions", "marketplace.reads"]);
    expect(fetches[0].url).toBe("/api/browser-relay/next?capabilities=marketplace.actions,marketplace.reads");
    expect(bridge.runMarketplaceCommand).toHaveBeenCalledWith(read, []);
    expect(JSON.parse(String(fetches[1].options?.body))).toEqual({ request_id: "b".repeat(32), payload: { ok: true, options: ["Women > Jeans"] } });
  });
});
