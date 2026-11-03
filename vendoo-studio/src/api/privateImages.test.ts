import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cachedPrivateImage, loadPrivateImage } from "./privateImages";
import { installHostedApiSession } from "./transport";

let uninstall: () => void = () => {};
let created = 0;
const fetchMock = vi.fn(async () => new Response(new Blob(["jpeg"]), { status: 200 }));

function signIn() {
  uninstall();
  uninstall = installHostedApiSession({
    origin: "https://anteroom.test", accessToken: async () => "token", unauthorized: () => {},
  });
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("URL", Object.assign(URL, {
    createObjectURL: () => `blob:${++created}`,
    revokeObjectURL: vi.fn(),
  }));
  fetchMock.mockClear();
  signIn();
});

afterEach(() => { uninstall(); vi.unstubAllGlobals(); });

it("loads a cover once and shows it again without downloading it", async () => {
  const src = "/api/photos/p1/thumb?size=96&v=abc";
  const first = await loadPrivateImage(src);
  expect(cachedPrivateImage(src)).toBe(first);
  expect(await loadPrivateImage(src)).toBe(first);
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("forgets the last account's photos when the account changes", async () => {
  const src = "/api/photos/p2/thumb?size=96&v=abc";
  await loadPrivateImage(src);
  signIn();
  expect(cachedPrivateImage(src)).toBeUndefined();
  await loadPrivateImage(src);
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

it("shares one download between a warm-up and the photo that mounts while it runs", async () => {
  const src = "/api/photos/p3/thumb?size=192&v=abc";
  const viewer = new AbortController();
  const [warm, shown] = await Promise.all([loadPrivateImage(src), loadPrivateImage(src, viewer.signal)]);
  expect(shown).toBe(warm);
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("lets one viewer stop waiting without cancelling the shared download", async () => {
  const src = "/api/photos/p4/thumb?size=192&v=abc";
  const viewer = new AbortController();
  const warm = loadPrivateImage(src);
  const shown = loadPrivateImage(src, viewer.signal);
  viewer.abort();
  await expect(shown).rejects.toThrow();
  const url = await warm;
  expect(cachedPrivateImage(src)).toBe(url);
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("stops a download once its only viewer scrolls away", async () => {
  const src = "/api/photos/p5/thumb?size=96&v=abc";
  let aborted = false;
  fetchMock.mockImplementationOnce(((_input: string, init: RequestInit) => new Promise((_resolve, reject) => {
    init.signal?.addEventListener("abort", () => { aborted = true; reject(new DOMException("gone", "AbortError")); });
  })) as never);
  const viewer = new AbortController();
  const shown = loadPrivateImage(src, viewer.signal);
  await Promise.resolve();
  viewer.abort();
  await expect(shown).rejects.toThrow();
  await vi.waitFor(() => expect(aborted).toBe(true));
});

it("tries a photo again when the network or server fails for a moment", async () => {
  vi.useFakeTimers();
  try {
    fetchMock
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(new Response("busy", { status: 503 }));
    const shown = loadPrivateImage("/api/photos/retry/thumb?size=96&v=abc");
    await vi.runAllTimersAsync();
    expect(await shown).toMatch(/^blob:/);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  } finally { vi.useRealTimers(); }
});

it("does not retry a photo that is gone", async () => {
  fetchMock.mockResolvedValueOnce(new Response("", { status: 404 }));
  await expect(loadPrivateImage("/api/photos/gone/thumb?size=96&v=abc")).rejects.toThrow("Photo unavailable");
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
