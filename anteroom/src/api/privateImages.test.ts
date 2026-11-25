import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PhotoLoadError, cachedPrivateImage, forgetDeviceImages, loadPrivateImage } from "./privateImages";
import { installHostedApiSession } from "./transport";

let uninstall: () => void = () => {};
let created = 0;
const fetchMock = vi.fn(async () => new Response(new Blob(["jpeg"]), { status: 200 }));

function signIn(seller = "alice") {
  uninstall();
  uninstall = installHostedApiSession({
    seller, origin: "https://anteroom.test", accessToken: async () => "token", unauthorized: () => {},
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

/** Cache Storage as the browser has it: named caches of request URL to response. */
function deviceCaches() {
  const stores = new Map<string, Map<string, Response>>();
  const open = async (name: string) => {
    if (!stores.has(name)) stores.set(name, new Map());
    const store = stores.get(name)!;
    return {
      match: async (url: string) => store.get(url)?.clone(),
      put: async (url: string, response: Response) => { store.set(url, response); },
      keys: async () => [...store.keys()].map(url => new Request(new URL(url, "https://anteroom.test"))),
      delete: async (url: string) => store.delete(url),
    };
  };
  return { stores, api: { open, keys: async () => [...stores.keys()], delete: async (name: string) => stores.delete(name) } };
}

it("shows a thumbnail seen before without downloading it after a reload", async () => {
  const device = deviceCaches();
  vi.stubGlobal("caches", device.api);
  const src = "/api/photos/kept/thumb?size=96&v=abc";
  await loadPrivateImage(src);
  await vi.waitFor(() => expect(device.stores.get("anteroom-thumbnails:alice")?.has(src)).toBe(true));
  // A reload starts with an empty page cache and a new sign-in token.
  signIn("bob");
  signIn("alice");
  expect(cachedPrivateImage(src)).toBeUndefined();
  expect(await loadPrivateImage(src)).toMatch(/^blob:/);
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("keeps each seller's thumbnails apart and forgets them on sign-out", async () => {
  const device = deviceCaches();
  vi.stubGlobal("caches", device.api);
  const src = "/api/photos/private/thumb?size=96&v=abc";
  await loadPrivateImage(src);
  await vi.waitFor(() => expect(device.stores.get("anteroom-thumbnails:alice")?.has(src)).toBe(true));
  signIn("bob");
  await loadPrivateImage(src);
  expect(fetchMock).toHaveBeenCalledTimes(2);
  await forgetDeviceImages();
  expect([...device.stores.keys()]).toEqual([]);
});

it("prunes other sellers' thumbnail caches on sign-in while keeping the current seller's", async () => {
  const device = deviceCaches();
  vi.stubGlobal("caches", device.api);
  const src = "/api/photos/private/thumb?size=96&v=abc";
  await loadPrivateImage(src);
  await vi.waitFor(() => expect(device.stores.get("anteroom-thumbnails:alice")?.has(src)).toBe(true));
  signIn("bob");
  await loadPrivateImage(src);
  await vi.waitFor(() => expect(device.stores.get("anteroom-thumbnails:bob")?.has(src)).toBe(true));

  await forgetDeviceImages("bob");
  expect([...device.stores.keys()]).toEqual(["anteroom-thumbnails:bob"]);
});

it("lets a sign-in prune supersede an earlier signed-out clear without deleting the new seller's cache", async () => {
  const device = deviceCaches();
  let releaseFirstKeys!: (names: string[]) => void;
  let calls = 0;
  vi.stubGlobal("caches", {
    ...device.api,
    keys: () => {
      calls += 1;
      if (calls === 1) return new Promise<string[]>(resolve => { releaseFirstKeys = resolve; });
      return device.api.keys();
    },
  });
  device.stores.set("anteroom-thumbnails:alice", new Map());
  device.stores.set("anteroom-thumbnails:bob", new Map());

  const staleClear = forgetDeviceImages();
  const signInPrune = forgetDeviceImages("bob");
  await signInPrune;
  releaseFirstKeys(["anteroom-thumbnails:alice", "anteroom-thumbnails:bob"]);
  await staleClear;

  expect([...device.stores.keys()]).toEqual(["anteroom-thumbnails:bob"]);
});

it("does not keep full-size photos on the device", async () => {
  const device = deviceCaches();
  vi.stubGlobal("caches", device.api);
  await loadPrivateImage("/api/photos/full?v=abc");
  await Promise.resolve();
  expect(device.stores.size).toBe(0);
});

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
  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
  viewer.abort();
  await expect(shown).rejects.toThrow();
  await vi.waitFor(() => expect(aborted).toBe(true));
});

it("can immediately reload a cover after its warm-up is cancelled", async () => {
  const src = "/api/photos/cancelled-warm/thumb?size=96&v=abc";
  fetchMock.mockImplementationOnce(((_input: string, init: RequestInit) => new Promise((_resolve, reject) => {
    init.signal?.addEventListener("abort", () => reject(new DOMException("gone", "AbortError")));
  })) as never);
  const warm = new AbortController();
  const first = loadPrivateImage(src, warm.signal);
  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  warm.abort();
  const shown = loadPrivateImage(src);
  await expect(first).rejects.toThrow();
  await expect(shown).resolves.toMatch(/^blob:/);
  expect(fetchMock).toHaveBeenCalledTimes(2);
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


it.each([401, 403, 404])("reports a refused %i at once, keeping the status for diagnostics", async (status) => {
  fetchMock.mockResolvedValueOnce(new Response("", { status }));
  const failure = await loadPrivateImage(`/api/conversations/c/photos/p${status}/bytes?v=abc`).catch((error: unknown) => error);
  expect(failure).toBeInstanceOf(PhotoLoadError);
  expect((failure as PhotoLoadError).status).toBe(status);
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("reports a server failure only after trying again, with its status", async () => {
  vi.useFakeTimers();
  try {
    fetchMock.mockResolvedValue(new Response("busy", { status: 503 }));
    const failure = loadPrivateImage("/api/conversations/c/photos/busy/bytes?v=abc").catch((error: unknown) => error);
    await vi.runAllTimersAsync();
    const error = await failure;
    expect(error).toBeInstanceOf(PhotoLoadError);
    expect((error as PhotoLoadError).status).toBe(503);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  } finally {
    vi.useRealTimers();
    fetchMock.mockReset();
    fetchMock.mockImplementation(async () => new Response(new Blob(["jpeg"]), { status: 200 }));
  }
});

it("holds background slots until thumbnail bodies finish downloading", async () => {
  const { registerBackgroundSignal } = await import("./requestScheduler");
  let release!: () => void;
  const body = new Promise<void>(resolve => { release = resolve; });
  const fetch = vi.fn(async () => ({ ok: true, blob: async () => { await body; return new Blob(["jpeg"]); } }));
  vi.stubGlobal("fetch", fetch);
  const signal = registerBackgroundSignal(new AbortController().signal);
  const photos = Promise.all([1, 2, 3, 4].map(i => loadPrivateImage("/api/photos/slow-" + i, signal)));
  try {
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  } finally { release(); await photos; }
  expect(fetch).toHaveBeenCalledTimes(4);
});
