import { hasHostedApiSession } from "./transport";
import { loadPrivateImage } from "./privateImages";
import { registerBackgroundSignal } from "./requestScheduler";
import type { Query, QueryClient } from "@tanstack/react-query";
import { api } from "./client";
import { photoThumbUrl } from "../photoThumb";
import type { ListingPhoto } from "./types";

const warmingPhotos = new Map<string, HTMLImageElement>();
const warmedPhotos = new Set<string>();

const releaseHover = new WeakMap<Query, () => void>();

function warmPhotoThumbnails(photos: ListingPhoto[], signal?: AbortSignal) {
  if (signal?.aborted) return;
  if (hasHostedApiSession()) {
    // Signed-in photos download with the session; keep them where the photo tray reads them.
    // When warmed via hover/background, pass the background signal so downloads respect
    // the background concurrency cap and abort if hover leaves.
    for (const photo of photos.slice(0, 4)) loadPrivateImage(photoThumbUrl(photo), signal).catch(() => {});
    return;
  }
  if (typeof Image === "undefined") return;
  for (const photo of photos.slice(0, 4)) {
    const src = photoThumbUrl(photo);
    if (warmingPhotos.size >= 4) break;
    if (warmingPhotos.has(src) || warmedPhotos.has(src)) continue;
    const image = new Image();
    image.decoding = "async";
    image.fetchPriority = "low";
    image.onload = () => {
      warmingPhotos.delete(src);
      warmedPhotos.add(src);
      // Bound bookkeeping; the browser owns the actual image cache.
      if (warmedPhotos.size > 32) warmedPhotos.delete(warmedPhotos.values().next().value!);
    };
    image.onerror = () => warmingPhotos.delete(src);
    warmingPhotos.set(src, image);
    image.src = src;
  }
}

export interface PrefetchListingOptions {
  signal?: AbortSignal;
  background?: boolean;
}

// Only reads without side effects belong here. Each key and read matches the
// screen's own query, so opening the listing finds them already answered.
export function prefetchListingReads(client: QueryClient, id: string, options?: PrefetchListingOptions) {
  const callerSignal = options?.signal;
  const background = Boolean(options?.background);
  if (callerSignal?.aborted) return Promise.resolve([]);
  const read = (queryKey: string[], fetch: (signal: AbortSignal) => Promise<unknown>) => {
    const findQuery = () => client.getQueryCache().find({ queryKey, exact: true });
    // Opening can join the existing fetch before the editor has subscribed.
    const existing = findQuery();
    if (!background && !callerSignal && existing) releaseHover.get(existing)?.();
    return client.prefetchQuery({
      queryKey,
      retry: false,
      queryFn: async ({ signal }) => {
        if (background) registerBackgroundSignal(signal);
        const query = callerSignal && findQuery();
        if (!callerSignal || !query) return fetch(signal);

        // The query owns the transport. Leaving the row cancels only work
        // started here that has no active destination using it.
        const cancel = () => {
          if (!signal.aborted && !query.isActive()) void query.cancel({ revert: true });
        };
        const release = () => {
          callerSignal.removeEventListener("abort", cancel);
          if (releaseHover.get(query) === release) releaseHover.delete(query);
        };
        releaseHover.set(query, release);
        callerSignal.addEventListener("abort", cancel, { once: true });
        try {
          return await fetch(signal);
        } finally {
          release();
        }
      },
    });
  };
  const thumbSignal = background
    ? (() => {
        if (callerSignal) return registerBackgroundSignal(callerSignal);
        const controller = new AbortController();
        return registerBackgroundSignal(controller.signal);
      })()
    : callerSignal;
  return Promise.all([
    read(["jobs", id], (signal) => api.jobs.list(id, { signal })),
    read(["activity", id], (signal) => api.conversations.activity(id, { signal })),
    read(["listing-fields", id], (signal) => api.listings.fields(id, { signal })),
    read(["marketplace-listings", id], (signal) => api.marketplaceListings.get(id, { signal })),
    read(["native-marketplaces", id], (signal) => api.nativeMarketplaces.status(id, { signal })),
    read(["marketplace-sales", id], (signal) => api.marketplaceSales.list({ conversationId: id }, { signal })),
    read(["conversation", id], (signal) => api.conversations.get(id, { signal })),
    read(["listing", id], (signal) => api.listings.get(id, { signal })),
    read(["photos", id], (signal) => api.conversations.photos(id, undefined, { signal }))
      .then(() => warmPhotoThumbnails(client.getQueryData<ListingPhoto[]>(["photos", id]) ?? [], thumbSignal)),
    read(["messages", id], (signal) => api.conversations.messages(id, { signal })),
  ]);
}

export function prefetchAssistantThread(client: QueryClient, id: string) {
  return client.prefetchQuery({
    queryKey: ["assistant", "messages", id], queryFn: ({ signal }) => api.assistant.messages(id, { signal }), retry: false,
  });
}
