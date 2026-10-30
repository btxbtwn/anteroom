import { afterEach, describe, expect, it, vi } from "vitest";
import { installHostedApiSession, studioFetch } from "./transport";
const ORIGIN = "https://anteroom.example";
let dispose: (() => void) | undefined;
afterEach(() => { dispose?.(); dispose = undefined; vi.unstubAllGlobals(); });

describe("shared desktop and cloud API transport", () => {
  it("preserves the local request when no hosted session is installed", async () => {
    const request = vi.fn(async (_input: string, _options?: RequestInit) => new Response("{}")); vi.stubGlobal("fetch", request);
    const options = { method: "POST", body: "draft" };
    await studioFetch("/api/conversations", options);
    expect(request).toHaveBeenCalledWith("/api/conversations", options);
  });
  it("gets a current token for each call and preserves multipart/binary/SSE request options", async () => {
    const request = vi.fn(async (_input: string, _options?: RequestInit) => new Response("{}")); vi.stubGlobal("fetch", request);
    const accessToken = vi.fn().mockResolvedValueOnce("first").mockResolvedValueOnce("refreshed");
    dispose = installHostedApiSession({ origin: ORIGIN, accessToken, unauthorized: vi.fn() });
    const form = new FormData(); form.append("files", new Blob(["image"]), "coat.jpg");
    await studioFetch("/api/conversations/draft/photos", { method: "POST", body: form });
    await studioFetch("/api/conversations/draft/chat", { method: "POST", headers: { Accept: "text/event-stream" } });
    expect(request.mock.calls[0][1]).toMatchObject({ body: form, cache: "no-store", credentials: "omit" });
    const first = request.mock.calls[0][1] as RequestInit;
    const second = request.mock.calls[1][1] as RequestInit;
    expect(new Headers(first.headers).get("Authorization")).toBe("Bearer first");
    expect(new Headers(first.headers).has("Content-Type")).toBe(false);
    expect(new Headers(second.headers).get("Authorization")).toBe("Bearer refreshed");
    expect(new Headers(second.headers).get("Accept")).toBe("text/event-stream");
  });
  it("refuses foreign URLs, missing identity and requests whose account changed during token retrieval", async () => {
    const request = vi.fn(); vi.stubGlobal("fetch", request);
    let resolve!: (value: string | null) => void;
    dispose = installHostedApiSession({ origin: ORIGIN, accessToken: () => new Promise(done => { resolve = done; }), unauthorized: vi.fn() });
    await expect(studioFetch("https://elsewhere.example/api/secret")).rejects.toThrow("origin");
    const pending = studioFetch("/api/conversations");
    dispose(); resolve("old-account");
    await expect(pending).rejects.toThrow("Sign in");
    dispose = installHostedApiSession({ origin: ORIGIN, accessToken: async () => null, unauthorized: vi.fn() });
    await expect(studioFetch("/api/conversations")).rejects.toThrow("Sign in");
    expect(request).not.toHaveBeenCalled();
  });
  it("reports expired sessions without retrying a mutation", async () => {
    const unauthorized = vi.fn(); const request = vi.fn(async () => new Response("{}", { status: 401 })); vi.stubGlobal("fetch", request);
    dispose = installHostedApiSession({ origin: ORIGIN, accessToken: async () => "expired", unauthorized });
    await studioFetch("/api/conversations", { method: "POST" });
    expect(request).toHaveBeenCalledTimes(1); expect(unauthorized).toHaveBeenCalledOnce();
  });
});
