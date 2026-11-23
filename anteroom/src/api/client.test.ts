import { afterEach, describe, expect, it, vi } from "vitest";
import { api, errorMessage } from "./client";
import { ApiRequestError, installHostedApiSession, retryAfterMs } from "./transport";
import { reportError } from "./errorReports";
import { uploadComposerDraftPhotos } from "../components/chat/composerDraftPhotos";

vi.mock("./errorReports", () => ({ reportError: vi.fn() }));

it("acknowledges an empty expense deletion response", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", fetch);
  try {
    await expect(api.businessExpenses.remove({ id: "expense", revision: 2 })).resolves.toBeUndefined();
    expect(fetch).toHaveBeenCalledWith(
      "/api/business-expenses/expense?expected_revision=2",
      expect.objectContaining({ method: "DELETE" }),
    );
  } finally {
    vi.unstubAllGlobals();
  }
});

it("keeps hosted upload file identities by returned photo ID despite failures and response order", async () => {
  const uninstall = installHostedApiSession({
    seller: "fixture-alice", origin: "https://anteroom.test", accessToken: async () => "fixture-token", unauthorized: () => {},
  });
  const fetch = vi.fn()
    .mockResolvedValueOnce(new Response("unavailable", { status: 503 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ id: "tag" }), { status: 201 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ id: "front" }), { status: 201 }))
    .mockResolvedValueOnce(new Response(JSON.stringify([
      { id: "earlier", original_filename: "photo.jpg" },
      { id: "front", original_filename: "photo.jpg" },
      { id: "tag", original_filename: "photo.jpg" },
    ])));
  vi.stubGlobal("fetch", fetch);
  try {
    const files = ["lost.jpg", "tag.jpg", "front.jpg"].map(name => new File(["fixture"], name, { type: "image/jpeg" }));
    const result = await api.conversations.uploadPhotos("coat", files);
    expect(result.photos.map(p => [p.id, p.original_filename])).toEqual([["front", "front.jpg"], ["tag", "tag.jpg"]]);
    expect(result.count).toBe(2);
    expect(result.errors).toEqual(["lost.jpg: could not upload this photo."]);
    expect(fetch).toHaveBeenCalledTimes(4);
  } finally {
    uninstall();
    vi.unstubAllGlobals();
  }
});

it("keeps acknowledged hosted writes off the composer retry path after a later request throws", async () => {
  const uninstall = installHostedApiSession({
    seller: "fixture-alice", origin: "https://anteroom.test", accessToken: async () => "fixture-token", unauthorized: () => {},
  });
  const fetch = vi.fn()
    .mockResolvedValueOnce(new Response(null, { status: 201 }))
    .mockRejectedValueOnce(new Error("Disconnected"))
    .mockResolvedValueOnce(new Response(null, { status: 201 }));
  vi.stubGlobal("fetch", fetch);
  try {
    const first = { id: "front", file: new File(["front"], "image.jpg") };
    const second = { id: "tag", file: new File(["tag"], "image.jpg") };
    let attached = [first, second];
    const deps = {
      uploadPhoto: (file: File) => api.conversations.uploadListingPhoto("coat", file),
      invalidatePhotos: vi.fn<() => Promise<void>>()
        .mockRejectedValueOnce(new Error("Refresh unavailable"))
        .mockResolvedValueOnce(undefined),
      clearImages: (ids: readonly string[]) => { attached = attached.filter(({ id }) => !ids.includes(id)); },
      setError: vi.fn(),
    };

    expect(await uploadComposerDraftPhotos(attached, deps)).toBe(false);
    expect(attached).toEqual([second]);
    expect(deps.invalidatePhotos).toHaveBeenCalledTimes(1);
    expect(deps.setError).toHaveBeenCalledWith(expect.stringContaining("Photos were saved"));

    expect(await uploadComposerDraftPhotos(attached, deps)).toBe(true);
    expect(attached).toEqual([]);
    expect(fetch.mock.calls.map(([, options]) => [options.method, options.body])).toEqual([
      ["POST", first.file], ["POST", second.file], ["POST", second.file],
    ]);
  } finally {
    uninstall();
    vi.unstubAllGlobals();
  }
});

describe("errorMessage", () => {
  it("prefers a string detail", () => {
    expect(errorMessage({ detail: "Conversation not found" }, "fallback")).toBe("Conversation not found");
  });

  it("joins FastAPI validation errors", () => {
    const body = { detail: [{ msg: "field required" }, { message: "too long" }, "bad value"] };
    expect(errorMessage(body, "fallback")).toBe("field required; too long; bad value");
  });

  it("reads structured detail objects", () => {
    expect(errorMessage({ detail: { message: "Chrome is not connected" } }, "fallback")).toBe("Chrome is not connected");
    expect(errorMessage({ detail: { errors: [{ message: "Price is required" }, { msg: "Add photos" }] } }, "fallback"))
      .toBe("Price is required; Add photos");
  });

  it("falls back to a top-level message, then the fallback", () => {
    expect(errorMessage({ message: "Upstream failed" }, "fallback")).toBe("Upstream failed");
    expect(errorMessage({ detail: "   " }, "Request failed: 500")).toBe("Request failed: 500");
    expect(errorMessage(null, "Request failed: 502")).toBe("Request failed: 502");
  });
});

describe("Listing revision protection", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends the revision the editor actually read", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true, revision_id: "next" })));
    vi.stubGlobal("fetch", fetch);
    await api.listings.update("coat", { title: "Edited coat" }, "reviewed-revision");
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
      listing: { title: "Edited coat" }, expected_revision_id: "reviewed-revision",
    });
  });
});

describe("Listing action deadlines", () => {
  afterEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("preserves server errors and clears the deadline", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: "This listing is busy." }), { status: 409 },
    )));
    await expect(api.conversations.reset("listing1", { keepInputs: true })).rejects.toThrow("This listing is busy.");
    expect(vi.getTimerCount()).toBe(0);
    expect(reportError).not.toHaveBeenCalled();
  });

  it.each([
    ["reset", () => api.conversations.reset("listing1", { keepInputs: true })],
    ["price confirmation", () => api.listings.applyPriceDrop("listing1", { price: 20, mode: "custom", expected_revision_id: "reviewed" })],
  ] as const)("releases a stalled %s without starting another write", async (_label, action) => {
    vi.useFakeTimers();
    const fetch = vi.fn().mockImplementation((_url: string, options: RequestInit) =>
      new Promise((_resolve, reject) => {
        options.signal!.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
      }),
    );
    vi.stubGlobal("fetch", fetch);
    const failure = expect(action()).rejects.toThrow("you do not need to restart Anteroom");
    await vi.advanceTimersByTimeAsync(30000);
    await failure;
    expect(reportError).toHaveBeenCalledWith("mutation", expect.objectContaining({
      message: expect.stringContaining("30000ms"),
    }));
    expect(fetch).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });
});


describe("Reading measurements from photos", () => {
  afterEach(() => vi.unstubAllGlobals());
  const photo = () => new File(["x"], "1.jpg", { type: "image/jpeg" });
  const result = { photos_read: 1, measurements: {} };

  it("tries once more when a restart cuts the read off", async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response("Internal Server Error", { status: 500 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(result)));
    vi.stubGlobal("fetch", fetch);
    await expect(api.measurements.read([photo()], "top")).resolves.toEqual(result);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("tries once more when the connection drops", async () => {
    const fetch = vi.fn()
      .mockRejectedValueOnce(new TypeError("Load failed"))
      .mockResolvedValueOnce(new Response(JSON.stringify(result)));
    vi.stubGlobal("fetch", fetch);
    await expect(api.measurements.read([photo()], "top")).resolves.toEqual(result);
  });

  it("does not repeat a read the model itself failed", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: "OpenRouter stopped responding while looking at the photos." }), { status: 502 },
    ));
    vi.stubGlobal("fetch", fetch);
    await expect(api.measurements.read([photo()], "top")).rejects.toThrow("OpenRouter stopped responding");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("gives up after the second try", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response("", { status: 503 }));
    vi.stubGlobal("fetch", fetch);
    await expect(api.measurements.read([photo()], "top")).rejects.toThrow(
      "The server restarted while reading. Press Read measurements again.",
    );
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("retries hosted upload-slot 429 and 503 busy responses after Retry-After", async () => {
    vi.useFakeTimers();
    try {
      const fetch = vi.fn()
        .mockResolvedValueOnce(new Response(
          JSON.stringify({ detail: "Another of your messages is still uploading. Try again when it finishes." }),
          { status: 429, headers: { "Retry-After": "2" } },
        ))
        .mockResolvedValueOnce(new Response(
          JSON.stringify({ detail: "Image uploads are busy right now. Try again in a moment." }),
          { status: 503, headers: { "Retry-After": "3" } },
        ))
        .mockResolvedValueOnce(new Response(JSON.stringify(result)));
      vi.stubGlobal("fetch", fetch);
      const pending = api.measurements.read([photo()], "top");
      await vi.advanceTimersByTimeAsync(1999);
      expect(fetch).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(1);
      expect(fetch).toHaveBeenCalledTimes(2);
      await vi.advanceTimersByTimeAsync(2999);
      expect(fetch).toHaveBeenCalledTimes(2);
      await vi.advanceTimersByTimeAsync(1);
      await expect(pending).resolves.toEqual(result);
      expect(fetch).toHaveBeenCalledTimes(3);
    } finally {
      vi.useRealTimers();
    }
  });

  it("gives up after 6 upload-slot busy retries and surfaces the server detail", async () => {
    const busy = () => new Response(
      JSON.stringify({ detail: "Another of your messages is still uploading. Try again when it finishes." }),
      { status: 429, headers: { "Retry-After": "0" } },
    );
    const fetch = vi.fn().mockImplementation(async () => busy());
    vi.stubGlobal("fetch", fetch);
    await expect(api.measurements.read([photo()], "top")).rejects.toThrow("Another of your messages is still uploading.");
    expect(fetch).toHaveBeenCalledTimes(7);
  });

  it("fails fast on a rate-limit 429 instead of treating it as an upload-slot wait", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: "Too many requests in a short time. Try again in a moment." }),
      { status: 429, headers: { "Retry-After": "30" } },
    ));
    vi.stubGlobal("fetch", fetch);
    await expect(api.measurements.read([photo()], "top")).rejects.toThrow("Too many requests in a short time.");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("stops waiting and does not retry when aborted during Retry-After", async () => {
    vi.useFakeTimers();
    try {
      const fetch = vi.fn().mockResolvedValue(new Response(
        JSON.stringify({ detail: "Another of your messages is still uploading. Try again when it finishes." }),
        { status: 429, headers: { "Retry-After": "5" } },
      ));
      vi.stubGlobal("fetch", fetch);
      const controller = new AbortController();
      const pending = api.sizeTags.read([photo()], controller.signal);
      const rejection = expect(pending).rejects.toMatchObject({ name: "AbortError" });
      await vi.advanceTimersByTimeAsync(100);
      expect(fetch).toHaveBeenCalledTimes(1);
      controller.abort();
      await rejection;
      await vi.advanceTimersByTimeAsync(10000);
      expect(fetch).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("paces hosted uploads one at a time while letting model reads run concurrently and skipping aborted waiters", async () => {
    const requests: FakeUploadXhr[] = [];
    class FakeUploadXhr {
      status = 0;
      statusText = "";
      responseText = "";
      readyState = 0;
      aborted = false;
      sentBody: FormData | null = null;
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
      constructor() { requests.push(this); }
      open() {}
      setRequestHeader() {}
      getAllResponseHeaders() { return "Content-Type: application/json\r\n"; }
      send(body: FormData) { this.sentBody = body; }
      finishUpload() { this.uploadLoadend?.(); }
      respond(status: number, body: unknown) {
        this.status = status;
        this.responseText = JSON.stringify(body);
        this.readyState = 4;
        this.onreadystatechange?.();
        this.onload?.();
      }
      abort() { this.aborted = true; this.onabort?.(); }
    }

    vi.stubGlobal("XMLHttpRequest", FakeUploadXhr);
    const uninstall = installHostedApiSession({
      seller: "seller-1",
      origin: "https://anteroom.example",
      accessToken: async () => "token",
      unauthorized: vi.fn(),
    });
    try {
      const c2 = new AbortController();
      const r1 = api.measurements.read([new File(["1"], "1.jpg")], "top");
      const r2 = api.measurements.read([new File(["2"], "2.jpg")], "top", c2.signal);
      const r3 = api.measurements.read([new File(["3"], "3.jpg")], "top");

      // Only item 1 starts uploading; items 2 and 3 wait for item 1's upload body to finish.
      await vi.waitFor(() => expect(requests).toHaveLength(1));
      expect((requests[0]!.sentBody!.get("files") as File).name).toBe("1.jpg");

      // Aborting queued item 2 cancels it without ever starting its XHR.
      c2.abort();
      await expect(r2).rejects.toMatchObject({ name: "AbortError" });
      expect(requests).toHaveLength(1);

      // As soon as item 1's upload finishes (while its model read response is still pending),
      // item 3 immediately begins uploading and runs its model read concurrently with item 1.
      requests[0]!.finishUpload();
      await vi.waitFor(() => expect(requests).toHaveLength(2));
      expect((requests[1]!.sentBody!.get("files") as File).name).toBe("3.jpg");
      requests[1]!.finishUpload();

      // Both item 1 and item 3 have their model reads in flight at the same time; resolve out of order.
      requests[1]!.respond(200, { photos_read: 1, measurements: { length: { status: "accepted", value: 28, reads: [28, 28] } } });
      requests[0]!.respond(200, { photos_read: 1, measurements: { length: { status: "accepted", value: 27, reads: [27, 27] } } });
      await expect(r3).resolves.toMatchObject({ photos_read: 1, measurements: { length: { value: 28 } } });
      await expect(r1).resolves.toMatchObject({ photos_read: 1, measurements: { length: { value: 27 } } });
    } finally {
      uninstall();
    }
  });

  it("pins queued hosted uploads and retries to the submission session when accounts switch", async () => {
    const sent: Array<{ auth: string | undefined; file: string }> = [];
    const requests: FakeAuthXhr[] = [];
    class FakeAuthXhr {
      headers: Record<string, string> = {};
      status = 0;
      statusText = "";
      responseText = "";
      readyState = 0;
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
      constructor() { requests.push(this); }
      open() {}
      setRequestHeader(key: string, value: string) { this.headers[key] = value; }
      getAllResponseHeaders() { return "Content-Type: application/json\r\nRetry-After: 0\r\n"; }
      send(body: FormData) {
        sent.push({ auth: this.headers.Authorization, file: (body.get("files") as File).name });
      }
      finishUpload() { this.uploadLoadend?.(); }
      respond(status: number, body: unknown) {
        this.status = status;
        this.responseText = JSON.stringify(body);
        this.readyState = 4;
        this.onreadystatechange?.();
        this.onload?.();
      }
      abort() { this.onabort?.(); }
    }
    vi.stubGlobal("XMLHttpRequest", FakeAuthXhr);
    let uninstall = installHostedApiSession({
      seller: "seller-A",
      origin: "https://anteroom.example",
      accessToken: async () => "token-A",
      unauthorized: vi.fn(),
    });
    try {
      const a1 = api.measurements.read([new File(["a1"], "a1.jpg")], "top");
      const a2Queued = api.measurements.read([new File(["a2"], "a2.jpg")], "top");
      await vi.waitFor(() => expect(requests).toHaveLength(1));

      // Switch to seller B while a1 is uploading and a2Queued is waiting on the upload gate.
      uninstall();
      uninstall = installHostedApiSession({
        seller: "seller-B",
        origin: "https://anteroom.example",
        accessToken: async () => "token-B",
        unauthorized: vi.fn(),
      });
      const b1 = api.measurements.read([new File(["b1"], "b1.jpg")], "top");

      // Finish a1's upload; a2Queued must fail without uploading under seller B's token,
      // and seller B's b1 must immediately be admitted with token-B.
      requests[0]!.finishUpload();
      await expect(a2Queued).rejects.toThrow("account changed");
      await vi.waitFor(() => expect(requests).toHaveLength(2));
      expect(sent).toEqual([
        { auth: "Bearer token-A", file: "a1.jpg" },
        { auth: "Bearer token-B", file: "b1.jpg" },
      ]);

      // Completing a1's response also rejects because the account changed, and b1 succeeds.
      requests[0]!.respond(429, { detail: "Another of your messages is still uploading. Try again when it finishes." });
      await expect(a1).rejects.toThrow("account changed");
      requests[1]!.finishUpload();
      requests[1]!.respond(200, { photos_read: 1, measurements: { length: { status: "accepted", value: 29, reads: [29, 29] } } });
      await expect(b1).resolves.toMatchObject({ photos_read: 1, measurements: { length: { value: 29 } } });
      expect(sent).toHaveLength(2);
    } finally {
      uninstall();
    }
  });

  it("releases the hosted upload gate immediately when aborted during token lookup", async () => {
    const requests: FakeTokenGateXhr[] = [];
    class FakeTokenGateXhr {
      status = 0;
      statusText = "";
      responseText = "";
      readyState = 0;
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
      constructor() { requests.push(this); }
      open() {}
      setRequestHeader() {}
      getAllResponseHeaders() { return "Content-Type: application/json\r\n"; }
      send() {}
      finishUpload() { this.uploadLoadend?.(); }
      respond(status: number, body: unknown) {
        this.status = status;
        this.responseText = JSON.stringify(body);
        this.readyState = 4;
        this.onreadystatechange?.();
        this.onload?.();
      }
      abort() { this.onabort?.(); }
    }
    vi.stubGlobal("XMLHttpRequest", FakeTokenGateXhr);
    let tokenCalls = 0;
    let releaseFirstToken!: (token: string | null) => void;
    const uninstall = installHostedApiSession({
      seller: "seller-1",
      origin: "https://anteroom.example",
      accessToken: () => {
        tokenCalls += 1;
        if (tokenCalls === 1) return new Promise<string | null>((resolve) => { releaseFirstToken = resolve; });
        return Promise.resolve("token-2");
      },
      unauthorized: vi.fn(),
    });
    try {
      const c1 = new AbortController();
      const first = api.sizeTags.read([new File(["1"], "1.jpg")], c1.signal);
      const second = api.sizeTags.read([new File(["2"], "2.jpg")]);
      await vi.waitFor(() => expect(releaseFirstToken).toBeDefined());
      expect(requests).toHaveLength(0);

      c1.abort();
      await expect(first).rejects.toMatchObject({ name: "AbortError" });
      await vi.waitFor(() => expect(requests).toHaveLength(1));
      requests[0]!.finishUpload();
      requests[0]!.respond(200, { photos_read: 1, status: "accepted", size: "M", sizes: ["M"] });
      await expect(second).resolves.toMatchObject({ status: "accepted", size: "M" });
      releaseFirstToken("late");
    } finally {
      uninstall();
    }
  });
});

describe("Reading size tags from photos", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("posts the item's photos and retries a cut-off read once", async () => {
    const result = { photos_read: 2, status: "accepted", size: "0", sizes: ["0"] };
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response("Internal Server Error", { status: 500 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(result)));
    vi.stubGlobal("fetch", fetch);
    const photos = [new File(["a"], "1.jpg"), new File(["b"], "2.jpg")];
    await expect(api.sizeTags.read(photos)).resolves.toEqual(result);
    expect(fetch).toHaveBeenCalledTimes(2);
    const [url, init] = fetch.mock.calls[1]!;
    expect(String(url)).toMatch(/\/api\/size-tags\/read$/);
    expect((init.body as FormData).getAll("files")).toHaveLength(2);
  });

  it("shows a failure the server explains instead of blaming a restart", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: "Anteroom could not read the size in these photos." }), { status: 500 },
    ));
    vi.stubGlobal("fetch", fetch);
    await expect(api.sizeTags.read([new File(["a"], "1.jpg")])).rejects.toThrow("Anteroom could not read the size in these photos.");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("names its own button when the server keeps restarting", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 503 })));
    await expect(api.sizeTags.read([new File(["a"], "1.jpg")])).rejects.toThrow(
      "The server restarted while reading. Press Read size tags again.",
    );
  });

  it("reads a saved listing's size tag from its saved photos", async () => {
    const result = { photos_read: 2, status: "accepted", size: "M", sizes: ["M"] };
    const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(result)));
    vi.stubGlobal("fetch", fetch);
    await expect(api.sizeTags.readSaved("conv-1")).resolves.toEqual(result);
    const [url, init] = fetch.mock.calls[0]!;
    expect(String(url)).toMatch(/\/api\/conversations\/conv-1\/size-tags\/read$/);
    expect(init.method).toBe("POST");
  });
});

describe("Requests while the server restarts", () => {
  afterEach(() => vi.unstubAllGlobals());

  const failure = async (call: () => Promise<unknown>) => {
    try { await call(); } catch (error) { return error; }
    throw new Error("Expected the request to fail");
  };

  it("marks a read that met a gateway answer or a dropped connection as safe to ask again", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>Service Unavailable</html>", {
      status: 503, headers: { "Retry-After": "5" },
    })));
    const unavailable = await failure(() => api.suggestions.list());
    expect(unavailable).toBeInstanceOf(ApiRequestError);
    expect(unavailable).toMatchObject({ status: 503, retryable: true, retryAfterMs: 5000, message: "Request failed: 503" });

    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    expect(await failure(() => api.suggestions.list())).toMatchObject({
      retryable: true,
      message: "Could not reach the server. Check your connection and try again.",
    });

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Not found" }), { status: 404 })));
    expect(await failure(() => api.suggestions.list())).toMatchObject({ status: 404, retryable: false, message: "Not found" });

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>Bad Gateway</html>", { status: 502 })));
    expect(await failure(() => api.suggestions.list())).toMatchObject({ status: 502, retryable: true });

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "eBay is not connected." }), { status: 502 })));
    expect(await failure(() => api.suggestions.list())).toMatchObject({ status: 502, retryable: false, message: "eBay is not connected." });
  });

  it("never marks a write as safe to repeat", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 503 })));
    expect(await failure(() => api.listings.update("coat", { title: "Edited" }, "r1"))).toMatchObject({ status: 503, retryable: false });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const dropped = await failure(() => api.listings.update("coat", { title: "Edited" }, "r1"));
    expect(dropped).toBeInstanceOf(TypeError);
  });

  it("reads Retry-After as seconds or a date", () => {
    expect(retryAfterMs("7")).toBe(7000);
    expect(retryAfterMs(new Date(60_000).toUTCString(), 40_000)).toBe(20_000);
    expect(retryAfterMs("soon")).toBeNull();
    expect(retryAfterMs(null)).toBeNull();
  });
});

describe("Reloading marketplace fields", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("retries once when Safari drops the connection with TypeError: Load failed", async () => {
    const fetch = vi.fn()
      .mockRejectedValueOnce(new TypeError("Load failed"))
      .mockResolvedValueOnce(new Response(JSON.stringify({ revision_id: "r2" }), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    await expect(api.nativeMarketplaces.refreshForm("item", "mercari", "r1")).resolves.toEqual({ revision_id: "r2" });
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("retries once on an unexplained proxy 503 and fails fast on explained 409 or 502", async () => {
    const proxyFetch = vi.fn()
      .mockResolvedValueOnce(new Response("<html>503</html>", { status: 503 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ revision_id: "r2" }), { status: 200 }));
    vi.stubGlobal("fetch", proxyFetch);
    await expect(api.nativeMarketplaces.refreshForm("item", "ebay", "r1")).resolves.toEqual({ revision_id: "r2" });
    expect(proxyFetch).toHaveBeenCalledTimes(2);

    const conflictFetch = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: "The listing changed while fields loaded. Reload its fields again." }),
      { status: 409 },
    ));
    vi.stubGlobal("fetch", conflictFetch);
    await expect(api.nativeMarketplaces.refreshForm("item", "ebay", "r1")).rejects.toMatchObject({ status: 409 });
    expect(conflictFetch).toHaveBeenCalledTimes(1);

    const explained502 = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: "eBay's category fields could not be loaded." }),
      { status: 502 },
    ));
    vi.stubGlobal("fetch", explained502);
    await expect(api.nativeMarketplaces.refreshForm("item", "ebay", "r1")).rejects.toMatchObject({ status: 502 });
    expect(explained502).toHaveBeenCalledTimes(1);
  });

  it("replaces Safari's Load failed with a clear connection message if the retry also drops", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Load failed")));
    await expect(api.nativeMarketplaces.refreshForm("item", "mercari", "r1")).rejects.toThrow(
      "Could not reach the server to reload fields. Check your connection and try again.",
    );
  });

  it("retries once when the connection drops while reading a 200 response body", async () => {
    const brokenBody = {
      ok: true,
      status: 200,
      json: vi.fn().mockRejectedValue(new TypeError("Load failed")),
    } as unknown as Response;
    const fetch = vi.fn()
      .mockResolvedValueOnce(brokenBody)
      .mockResolvedValueOnce(new Response(JSON.stringify({ revision_id: "r2" }), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    await expect(api.nativeMarketplaces.refreshForm("item", "mercari", "r1")).resolves.toEqual({ revision_id: "r2" });
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});

describe("Run due relists", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("replaces Safari Load failed with a clear message that points at the log", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Load failed")));
    await expect(api.automations.runAutoRelist()).rejects.toThrow(
      "Could not reach the server while running due relists. Check the auto-relist log — the pass may still have finished.",
    );
  });
});
