import { MutationObserver, onlineManager } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createStudioQueryClient, readRetryDelay, retryRead, UNAVAILABLE_RETRIES } from "./queryClient";
import { ApiRequestError } from "./transport";
import { reportError } from "./errorReports";

vi.mock("./errorReports", () => ({ reportError: vi.fn() }));

it("reports handled read and mutation failures even with local error callbacks", async () => {
  const client = createStudioQueryClient();
  const error = new Error("Marketplace refused the request");
  const onError = vi.fn();
  try {
    await expect(client.fetchQuery({ queryKey: ["failure"], queryFn: () => Promise.reject(error), retry: false })).rejects.toBe(error);
    const mutation = new MutationObserver(client, { mutationFn: () => Promise.reject(error), onError, retry: false });
    await expect(mutation.mutate(undefined)).rejects.toBe(error);
    expect(onError).toHaveBeenCalledWith(error, undefined, undefined, expect.anything());
    expect(reportError).toHaveBeenCalledWith("query", error);
    expect(reportError).toHaveBeenCalledWith("mutation", error);
  } finally {
    client.clear();
  }
});

afterEach(() => {
  onlineManager.setOnline(true);
});

it("starts a confirmed local action even when the webview reports offline", async () => {
  onlineManager.setOnline(false);
  const client = createStudioQueryClient();
  const confirm = vi.fn().mockResolvedValue({ ok: true });
  const mutation = new MutationObserver(client, { mutationFn: confirm });
  try {
    await expect(mutation.mutate(undefined)).resolves.toEqual({ ok: true });
    expect(confirm).toHaveBeenCalledOnce();
    expect(mutation.getCurrentResult().isPaused).toBe(false);
  } finally {
    client.clear();
  }
});

it("finishes local reads and retries after reset while offline", async () => {
  onlineManager.setOnline(false);
  const client = createStudioQueryClient();
  const read = vi.fn()
    .mockRejectedValueOnce(new Error("Temporary local error"))
    .mockResolvedValueOnce({ listing: {} });
  try {
    await expect(client.fetchQuery({
      queryKey: ["listing", "regenerated"], queryFn: read, retryDelay: 0,
    })).resolves.toEqual({ listing: {} });
    expect(read).toHaveBeenCalledTimes(2);
  } finally {
    client.clear();
  }
});

describe("reads while the hosted server restarts", () => {
  const unavailable = (status = 503, retryAfter: number | null = null) =>
    new ApiRequestError(`Request failed: ${status}`, status, true, retryAfter);

  it("keeps loading through a deploy's 503s and shows the listing once the server is back", async () => {
    const client = createStudioQueryClient();
    const read = vi.fn()
      .mockRejectedValueOnce(unavailable(503))
      .mockRejectedValueOnce(unavailable(502))
      .mockRejectedValueOnce(unavailable(504))
      .mockResolvedValueOnce({ listing: {} });
    try {
      await expect(client.fetchQuery({ queryKey: ["listing", "deploy"], queryFn: read, retryDelay: 0 }))
        .resolves.toEqual({ listing: {} });
      expect(read).toHaveBeenCalledTimes(4);
    } finally {
      client.clear();
    }
  });

  it("retries an unavailable read for a bounded time, and anything else once", () => {
    expect(retryRead(0, unavailable())).toBe(true);
    expect(retryRead(UNAVAILABLE_RETRIES - 1, unavailable())).toBe(true);
    expect(retryRead(UNAVAILABLE_RETRIES, unavailable())).toBe(false);
    const refused = new ApiRequestError("Not found", 404, false);
    expect(retryRead(0, refused)).toBe(true);
    expect(retryRead(1, refused)).toBe(false);
    expect(retryRead(1, new Error("Temporary local error"))).toBe(false);

    let longest = 0; let shortest = 0;
    for (let failure = 0; failure < UNAVAILABLE_RETRIES; failure++) {
      longest += readRetryDelay(failure, unavailable(), () => 1);
      shortest += readRetryDelay(failure, unavailable(), () => 0);
    }
    expect(longest).toBeLessThanOrEqual(90_000);
    expect(shortest).toBeGreaterThanOrEqual(40_000);
    expect(readRetryDelay(0, unavailable(503, 7000))).toBe(7000);
    expect(readRetryDelay(0, unavailable(503, 600_000))).toBe(30_000);
  });

  it("never sends a write twice, even when the server was restarting", async () => {
    const client = createStudioQueryClient();
    const save = vi.fn().mockRejectedValue(new ApiRequestError("Request failed: 503", 503, false));
    const mutation = new MutationObserver(client, { mutationFn: save });
    try {
      await expect(mutation.mutate(undefined)).rejects.toThrow("503");
      expect(save).toHaveBeenCalledOnce();
    } finally {
      client.clear();
    }
  });
});
