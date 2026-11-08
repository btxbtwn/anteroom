import { currentHostedSession, studioFetch } from "./transport";

/**
 * Signed-in cloud images, kept for the page's lifetime so a row that scrolls
 * away and back shows its photo at once instead of downloading it again.
 * Image URLs carry the photo's version, so a kept copy is never stale. The
 * cache belongs to one session and is dropped when the account changes.
 */
const LIMIT = 400;
const images = new Map<string, string>();
/**
 * Downloads still running, so a warm-up and the photo that mounts share one. A
 * download stops when every viewer waiting on it has gone and no warm-up wants it.
 */
interface Download { promise: Promise<string>; controller: AbortController; viewers: number; kept: boolean }
const pending = new Map<string, Download>();
let owner: unknown = null;

function forCurrentSession(): Map<string, string> {
  const session = currentHostedSession();
  if (owner !== session) {
    for (const url of images.values()) URL.revokeObjectURL(url);
    images.clear();
    for (const download of pending.values()) download.controller.abort();
    pending.clear();
    owner = session;
  }
  return images;
}

/** The kept copy of a private image, if this page already loaded it. */
export function cachedPrivateImage(src: string): string | undefined {
  const cache = forCurrentSession();
  const url = cache.get(src);
  if (url !== undefined) {
    // Most recently used goes last; the oldest is dropped first.
    cache.delete(src);
    cache.set(src, url);
  }
  return url;
}

/**
 * Loads a private image once and keeps it; resolves to an object URL. A viewer's
 * `signal` stops only its own wait; the download finishes for the next viewer.
 */
export async function loadPrivateImage(src: string, signal?: AbortSignal): Promise<string> {
  const kept = cachedPrivateImage(src);
  if (kept !== undefined) return kept;
  signal?.throwIfAborted();
  forCurrentSession();
  let download = pending.get(src);
  // A new viewport can request the same cover before an aborted warm-up's
  // promise has settled. Start a fresh download rather than sharing that abort.
  if (download?.controller.signal.aborted) download = undefined;
  if (!download) {
    const controller = new AbortController();
    const started: Download = { promise: fetchPrivateImage(src, controller.signal), controller, viewers: 0, kept: false };
    pending.set(src, download = started);
    void started.promise.catch(() => {}).finally(() => { if (pending.get(src) === started) pending.delete(src); });
  }
  if (!signal) {
    download.kept = true;
    return download.promise;
  }
  const shared = download;
  shared.viewers += 1;
  return new Promise((resolve, reject) => {
    const stop = () => {
      shared.viewers -= 1;
      if (shared.viewers === 0 && !shared.kept) shared.controller.abort(signal.reason);
      reject(signal.reason);
    };
    signal.addEventListener("abort", stop, { once: true });
    shared.promise.then(resolve, reject).finally(() => signal.removeEventListener("abort", stop));
  });
}

/**
 * Thumbnails are also kept on this device, per seller, so a reload or a new
 * sign-in token shows them at once. The browser's own cache cannot: hosted
 * responses vary by the sign-in token, which changes every hour. Only versioned
 * thumbnails are kept; the URL changes when the photo does.
 */
const DEVICE_CACHE_PREFIX = "anteroom-thumbnails:";
const DEVICE_LIMIT = 5000;
let devicePuts = 0;

function keptOnDevice(src: string): boolean {
  return /\/thumb\?/.test(src) && /[?&]v=/.test(src);
}

async function deviceCache(): Promise<Cache | null> {
  const seller = currentHostedSession()?.seller;
  if (!seller || typeof caches === "undefined") return null;
  try {
    return await caches.open(DEVICE_CACHE_PREFIX + seller);
  } catch {
    // Private browsing or blocked site data: download as before.
    return null;
  }
}

async function readFromDevice(src: string): Promise<Blob | null> {
  if (!keptOnDevice(src)) return null;
  try {
    const cache = await deviceCache();
    const response = await cache?.match(src);
    return response ? await response.blob() : null;
  } catch {
    return null;
  }
}

async function keepOnDevice(src: string, blob: Blob): Promise<void> {
  if (!keptOnDevice(src)) return;
  try {
    const cache = await deviceCache();
    if (!cache) return;
    await cache.put(src, new Response(blob, { headers: { "Content-Type": blob.type || "image/jpeg" } }));
    // Old versions of edited photos are never asked for again; drop the oldest now and then.
    if (++devicePuts % 200 === 0) {
      const keys = await cache.keys();
      for (const request of keys.slice(0, Math.max(0, keys.length - DEVICE_LIMIT))) await cache.delete(request);
    }
  } catch {
    // A full or blocked store only means the next visit downloads it again.
  }
}

/** Forget every seller's thumbnails kept on this device, as signing out does. */
export async function forgetDeviceImages(): Promise<void> {
  if (typeof caches === "undefined") return;
  try {
    const names = await caches.keys();
    await Promise.all(names.filter(name => name.startsWith(DEVICE_CACHE_PREFIX)).map(name => caches.delete(name)));
  } catch {
    // Nothing kept, or the store is blocked.
  }
}

/** A dropped connection or a busy server is tried again; a missing photo is not. */
const RETRY_DELAYS_MS = [500, 2000];

function wait(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => { clearTimeout(timer); reject(signal.reason); }, { once: true });
  });
}

async function fetchImageBlob(src: string, signal: AbortSignal): Promise<Blob> {
  for (let attempt = 0; ; attempt += 1) {
    let response: Response | undefined;
    try {
      response = await studioFetch(src, { signal });
    } catch (error) {
      if (signal.aborted || attempt >= RETRY_DELAYS_MS.length) throw error;
    }
    if (response?.ok) return response.blob();
    const transient = !response || response.status === 429 || response.status >= 500;
    if (!transient || attempt >= RETRY_DELAYS_MS.length) throw new Error("Photo unavailable");
    await wait(RETRY_DELAYS_MS[attempt], signal);
  }
}

async function fetchPrivateImage(src: string, signal: AbortSignal): Promise<string> {
  const session = currentHostedSession();
  const kept = await readFromDevice(src);
  signal.throwIfAborted();
  const blob = kept ?? await fetchImageBlob(src, signal);
  if (!kept && currentHostedSession() === session) void keepOnDevice(src, blob);
  if (currentHostedSession() !== session) throw new Error("Your account changed.");
  const cache = forCurrentSession();
  const raced = cache.get(src);
  if (raced !== undefined) return raced;
  const url = URL.createObjectURL(blob);
  cache.set(src, url);
  while (cache.size > LIMIT) {
    const [oldest, oldUrl] = cache.entries().next().value!;
    cache.delete(oldest);
    URL.revokeObjectURL(oldUrl);
  }
  return url;
}
