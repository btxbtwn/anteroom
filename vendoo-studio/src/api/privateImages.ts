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
  const blob = await fetchImageBlob(src, signal);
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
