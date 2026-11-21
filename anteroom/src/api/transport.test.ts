import { afterEach, describe, expect, it, vi } from "vitest";
import { installHostedApiSession, studioBlob, studioFetch, studioUpload } from "./transport";
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
    dispose = installHostedApiSession({ seller: "seller", origin: ORIGIN, accessToken, unauthorized: vi.fn() });
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
    dispose = installHostedApiSession({ seller: "seller", origin: ORIGIN, accessToken: () => new Promise(done => { resolve = done; }), unauthorized: vi.fn() });
    await expect(studioFetch("https://elsewhere.example/api/secret")).rejects.toThrow("origin");
    const pending = studioFetch("/api/conversations");
    dispose(); resolve("old-account");
    await expect(pending).rejects.toThrow("Sign in");
    dispose = installHostedApiSession({ seller: "seller", origin: ORIGIN, accessToken: async () => null, unauthorized: vi.fn() });
    await expect(studioFetch("/api/conversations")).rejects.toThrow("Sign in");
    expect(request).not.toHaveBeenCalled();
  });
  it("reports expired sessions without retrying a mutation", async () => {
    const unauthorized = vi.fn(); const request = vi.fn(async () => new Response("{}", { status: 401 })); vi.stubGlobal("fetch", request);
    dispose = installHostedApiSession({ seller: "seller", origin: ORIGIN, accessToken: async () => "expired", unauthorized });
    await studioFetch("/api/conversations", { method: "POST" });
    expect(request).toHaveBeenCalledTimes(1); expect(unauthorized).toHaveBeenCalledOnce();
  });
  it("authenticates exports and discards a download if its account changes while reading", async () => {
    let finish!: (blob: Blob) => void;
    const blob = new Promise<Blob>(resolve => { finish = resolve; });
    const reading = vi.fn(() => blob);
    const request = vi.fn(async () => ({ ok: true, status: 200, blob: reading }));
    vi.stubGlobal("fetch", request);
    dispose = installHostedApiSession({ seller: "seller", origin: ORIGIN, accessToken: async () => "alice", unauthorized: vi.fn() });
    const pending = studioBlob("/api/sales-ledger/export/inventory.csv");
    await vi.waitFor(() => expect(reading).toHaveBeenCalledOnce());
    const [url, options] = request.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${ORIGIN}/api/sales-ledger/export/inventory.csv`);
    expect(new Headers(options.headers).get("Authorization")).toBe("Bearer alice");
    dispose();
    finish(new Blob(["alice inventory"]));
    await expect(pending).rejects.toThrow("account changed");
  });

  it("notifies onUploadComplete before the response finishes and enforces hosted auth/abort on studioUpload", async () => {
    const instances: FakeXhr[] = [];
    class FakeXhr {
      method = "";
      url = "";
      withCredentials = true;
      headers: Record<string, string> = {};
      status = 0;
      statusText = "";
      responseText = "";
      readyState = 0;
      aborted = false;
      private uploadLoadend: (() => void) | null = null;
      upload = {
        addEventListener: (event: string, cb: () => void) => {
          if (event === "loadend") this.uploadLoadend = cb;
        },
      };
      onreadystatechange: (() => void) | null = null;
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      ontimeout: (() => void) | null = null;
      onabort: (() => void) | null = null;
      constructor() { instances.push(this); }
      open(method: string, url: string) { this.method = method; this.url = url; }
      setRequestHeader(k: string, v: string) { this.headers[k] = v; }
      getAllResponseHeaders() { return "Content-Type: application/json\r\nRetry-After: 5\r\n"; }
      send() {}
      finishUpload() { this.uploadLoadend?.(); }
      respond(status: number, body: string) {
        this.status = status;
        this.responseText = body;
        this.readyState = 4;
        this.onreadystatechange?.();
        this.onload?.();
      }
      abort() { this.aborted = true; this.onabort?.(); }
    }
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const unauthorized = vi.fn();
    dispose = installHostedApiSession({ seller: "seller", origin: ORIGIN, accessToken: async () => "tok", unauthorized });

    const onUploadComplete = vi.fn();
    const form = new FormData();
    const pending = studioUpload("/api/measurements/read", form, { onUploadComplete });
    await vi.waitFor(() => expect(instances).toHaveLength(1));
    expect(instances[0]!.url).toBe(`${ORIGIN}/api/measurements/read`);
    expect(instances[0]!.withCredentials).toBe(false);
    expect(instances[0]!.headers.Authorization).toBe("Bearer tok");
    expect(onUploadComplete).not.toHaveBeenCalled();

    instances[0]!.finishUpload();
    expect(onUploadComplete).toHaveBeenCalledOnce();

    instances[0]!.respond(401, JSON.stringify({ detail: "expired" }));
    const res = await pending;
    expect(res.status).toBe(401);
    expect(res.headers.get("Retry-After")).toBe("5");
    expect(unauthorized).toHaveBeenCalledOnce();
    expect(onUploadComplete).toHaveBeenCalledOnce();

    const controller = new AbortController();
    const onAbortedUpload = vi.fn();
    const aborted = studioUpload("/api/size-tags/read", form, { signal: controller.signal, onUploadComplete: onAbortedUpload });
    await vi.waitFor(() => expect(instances).toHaveLength(2));
    controller.abort();
    await expect(aborted).rejects.toMatchObject({ name: "AbortError" });
    expect(instances[1]!.aborted).toBe(true);
    expect(onAbortedUpload).toHaveBeenCalledOnce();
  });

  it("aborts studioUpload during accessToken lookup and releases onUploadComplete", async () => {
    const xhrCtor = vi.fn();
    vi.stubGlobal("XMLHttpRequest", xhrCtor);
    const controller = new AbortController();
    const onUploadComplete = vi.fn();
    let resolveToken!: (token: string | null) => void;
    dispose = installHostedApiSession({
      seller: "seller",
      origin: ORIGIN,
      accessToken: () => new Promise<string | null>((resolve) => { resolveToken = resolve; }),
      unauthorized: vi.fn(),
    });
    const pending = studioUpload("/api/measurements/read", new FormData(), {
      signal: controller.signal,
      onUploadComplete,
    });
    await vi.waitFor(() => expect(resolveToken).toBeDefined());
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(onUploadComplete).toHaveBeenCalledOnce();
    expect(xhrCtor).not.toHaveBeenCalled();
    resolveToken("late-token");
  });

  it("aborts a stalled upload phase after uploadTimeoutMs without cutting off post-upload inference", async () => {
    vi.useFakeTimers();
    try {
      const instances: FakeTimedXhr[] = [];
      class FakeTimedXhr {
        status = 0;
        statusText = "";
        responseText = "";
        readyState = 0;
        aborted = false;
        private uploadLoadend: (() => void) | null = null;
        upload = {
          addEventListener: (event: string, cb: () => void) => {
            if (event === "loadend") this.uploadLoadend = cb;
          },
        };
        onreadystatechange: (() => void) | null = null;
        onload: (() => void) | null = null;
        onerror: (() => void) | null = null;
        ontimeout: (() => void) | null = null;
        onabort: (() => void) | null = null;
        constructor() { instances.push(this); }
        open() {}
        setRequestHeader() {}
        getAllResponseHeaders() { return "Content-Type: application/json\r\n"; }
        send() {}
        finishUpload() { this.uploadLoadend?.(); }
        respond(status: number, body: string) {
          this.status = status;
          this.responseText = body;
          this.readyState = 4;
          this.onreadystatechange?.();
          this.onload?.();
        }
        abort() { this.aborted = true; this.onabort?.(); }
      }
      vi.stubGlobal("XMLHttpRequest", FakeTimedXhr);
      dispose = installHostedApiSession({ seller: "seller", origin: ORIGIN, accessToken: async () => "tok", unauthorized: vi.fn() });

      // Stalled upload before loadend times out and releases onUploadComplete.
      const onStalledComplete = vi.fn();
      const stalled = studioUpload("/api/measurements/read", new FormData(), {
        uploadTimeoutMs: 500,
        onUploadComplete: onStalledComplete,
      });
      const stalledRejection = expect(stalled).rejects.toThrow("Failed to fetch");
      await vi.waitFor(() => expect(instances).toHaveLength(1));
      await vi.advanceTimersByTimeAsync(500);
      await stalledRejection;
      expect(instances[0]!.aborted).toBe(true);
      expect(onStalledComplete).toHaveBeenCalledOnce();

      // Upload that finishes before uploadTimeoutMs is NOT timed out during slow model inference.
      const onSlowComplete = vi.fn();
      const slow = studioUpload("/api/measurements/read", new FormData(), {
        uploadTimeoutMs: 500,
        onUploadComplete: onSlowComplete,
      });
      await vi.waitFor(() => expect(instances).toHaveLength(2));
      instances[1]!.finishUpload();
      expect(onSlowComplete).toHaveBeenCalledOnce();
      await vi.advanceTimersByTimeAsync(5_000);
      expect(instances[1]!.aborted).toBe(false);
      instances[1]!.respond(200, JSON.stringify({ ok: true }));
      await expect(slow).resolves.toMatchObject({ status: 200 });
    } finally {
      vi.useRealTimers();
    }
  });
});

it.each([true, false])("binds queued requests to their submission session (signed in: %s)", async signedIn => {
  const { registerBackgroundSignal, scheduleRequest } = await import("./requestScheduler");
  const session = { seller: "first", origin: ORIGIN, accessToken: vi.fn(async () => "test"), unauthorized: vi.fn() };
  if (signedIn) dispose = installHostedApiSession(session);
  let release!: () => void;
  const blocker = scheduleRequest("/api/status", {}, () => new Promise<void>(resolve => { release = resolve; }));
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  const queued = studioFetch("/api/listings", { signal: registerBackgroundSignal(new AbortController().signal) });
  dispose?.();
  dispose = installHostedApiSession({ ...session, seller: "second" });
  release();
  await blocker;
  await expect(queued).rejects.toThrow("account changed");
  expect(session.accessToken).not.toHaveBeenCalled();
  expect(fetch).not.toHaveBeenCalled();
});
