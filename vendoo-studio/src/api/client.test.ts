import { afterEach, describe, expect, it, vi } from "vitest";
import { api, errorMessage } from "./client";
import { ApiRequestError, installHostedApiSession, retryAfterMs } from "./transport";
import { reportError } from "./errorReports";

vi.mock("./errorReports", () => ({ reportError: vi.fn() }));

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
    expect(await failure(() => api.suggestions.list())).toMatchObject({ retryable: true, message: "Failed to fetch" });

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Not found" }), { status: 404 })));
    expect(await failure(() => api.suggestions.list())).toMatchObject({ status: 404, retryable: false, message: "Not found" });
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
