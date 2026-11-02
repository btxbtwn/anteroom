import { afterEach, describe, expect, it, vi } from "vitest";
import { api, errorMessage } from "./client";

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
  });

  it.each([
    ["reset", () => api.conversations.reset("listing1", { keepInputs: true })],
    ["price confirmation", () => api.listings.applyPriceDrop("listing1", { price: 20, mode: "custom" })],
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
    expect(fetch).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });
});

