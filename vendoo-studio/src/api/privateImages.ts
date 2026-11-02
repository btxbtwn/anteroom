import { currentHostedSession, studioFetch } from "./transport";

/**
 * Signed-in cloud images, kept for the page's lifetime so a row that scrolls
 * away and back shows its photo at once instead of downloading it again.
 * Image URLs carry the photo's version, so a kept copy is never stale. The
 * cache belongs to one session and is dropped when the account changes.
 */
const LIMIT = 400;
const images = new Map<string, string>();
let owner: unknown = null;

function forCurrentSession(): Map<string, string> {
  const session = currentHostedSession();
  if (owner !== session) {
    for (const url of images.values()) URL.revokeObjectURL(url);
    images.clear();
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

/** Loads a private image once and keeps it; resolves to an object URL. */
export async function loadPrivateImage(src: string, signal?: AbortSignal): Promise<string> {
  const kept = cachedPrivateImage(src);
  if (kept !== undefined) return kept;
  const session = currentHostedSession();
  const response = await studioFetch(src, { signal });
  if (!response.ok) throw new Error("Photo unavailable");
  const blob = await response.blob();
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
