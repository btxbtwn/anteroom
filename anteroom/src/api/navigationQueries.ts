import { hasHostedApiSession } from "./transport";
import { loadPrivateImage } from "./privateImages";
import { registerBackgroundSignal } from "./requestScheduler";
import type { QueryClient } from "@tanstack/react-query";
import { api } from "./client";
import { photoThumbUrl } from "../photoThumb";
import type { ListingPhoto } from "./types";

const warmingPhotos = new Map<string, HTMLImageElement>();
const warmedPhotos = new Set<string>();

function combineSignals(
  querySignal: AbortSignal,
  callerSignal: AbortSignal | undefined,
  background: boolean,
): AbortSignal {
  if (!callerSignal && !background) return querySignal;
  const controller = new AbortController();
  if (background) registerBackgroundSignal(controller.signal);
  const forward = (source: AbortSignal) => {
    if (!controller.signal.aborted) controller.abort(source.reason);
  };
  if (querySignal.aborted) {
    forward(querySignal);
  } else if (callerSignal?.aborted) {
    forward(callerSignal);
  } else {
    querySignal.addEventListener("abort", () => forward(querySignal), { once: true });
    callerSignal?.addEventListener("abort", () => forward(callerSignal), { once: true });
  }
  return controller.signal;
}

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
  const link = (rqSignal: AbortSignal) => combineSignals(rqSignal, callerSignal, background);
  const read = (queryKey: unknown[], queryFn: (context: { signal: AbortSignal }) => Promise<unknown>) =>
    client.prefetchQuery({ queryKey, queryFn: ({ signal }) => queryFn({ signal: link(signal) }), retry: false });
  const thumbSignal = background
    ? (() => {
        if (callerSignal) return registerBackgroundSignal(callerSignal);
        const controller = new AbortController();
        return registerBackgroundSignal(controller.signal);
      })()
    : callerSignal;
  return Promise.all([
    read(["jobs", id], ({ signal }) => api.jobs.list(id, { signal })),
    read(["activity", id], ({ signal }) => api.conversations.activity(id, { signal })),
    read(["listing-fields", id], ({ signal }) => api.listings.fields(id, { signal })),
    read(["marketplace-listings", id], ({ signal }) => api.marketplaceListings.get(id, { signal })),
    read(["native-marketplaces", id], ({ signal }) => api.nativeMarketplaces.status(id, { signal })),
    read(["marketplace-sales", id], ({ signal }) => api.marketplaceSales.list({ conversationId: id }, { signal })),
    client.prefetchQuery({ queryKey: ["conversation", id], queryFn: ({ signal }) => api.conversations.get(id, { signal: link(signal) }), retry: false }),
    client.prefetchQuery({ queryKey: ["listing", id], queryFn: ({ signal }) => api.listings.get(id, { signal: link(signal) }), retry: false }),
    client.prefetchQuery({ queryKey: ["photos", id], queryFn: ({ signal }) => api.conversations.photos(id, undefined, { signal: link(signal) }), retry: false })
      .then(() => warmPhotoThumbnails(client.getQueryData<ListingPhoto[]>(["photos", id]) ?? [], thumbSignal)),
    client.prefetchQuery({ queryKey: ["messages", id], queryFn: ({ signal }) => api.conversations.messages(id, { signal: link(signal) }), retry: false }),
  ]);
}

export function prefetchAssistantThread(client: QueryClient, id: string) {
  return client.prefetchQuery({
    queryKey: ["assistant", "messages", id], queryFn: ({ signal }) => api.assistant.messages(id, { signal }), retry: false,
  });
}
