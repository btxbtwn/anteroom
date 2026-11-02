import { hasHostedApiSession } from "./transport";
import { loadPrivateImage } from "./privateImages";
import type { QueryClient } from "@tanstack/react-query";
import { api } from "./client";
import { photoThumbUrl } from "../photoThumb";
import type { Photo } from "./types";

const warmingPhotos = new Map<string, HTMLImageElement>();
const warmedPhotos = new Set<string>();

function warmPhotoThumbnails(photos: Photo[]) {
  if (hasHostedApiSession()) {
    // Signed-in photos download with the session; keep them where the photo tray reads them.
    for (const photo of photos.slice(0, 4)) loadPrivateImage(photoThumbUrl(photo)).catch(() => {});
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

// Only reads without side effects belong here. Each key and read matches the
// screen's own query, so opening the listing finds them already answered.
export function prefetchListingReads(client: QueryClient, id: string) {
  const read = (queryKey: unknown[], queryFn: () => Promise<unknown>) =>
    client.prefetchQuery({ queryKey, queryFn, retry: false });
  return Promise.all([
    read(["jobs", id], () => api.jobs.list(id)),
    read(["activity", id], () => api.conversations.activity(id)),
    read(["listing-fields", id], () => api.vendooApi.listingFields(id)),
    read(["marketplace-listings", id], () => api.marketplaceListings.get(id)),
    read(["native-marketplaces", id], () => api.nativeMarketplaces.status(id)),
    read(["marketplace-sales", id], () => api.marketplaceSales.list({ conversationId: id })),
    client.prefetchQuery({ queryKey: ["conversation", id], queryFn: () => api.conversations.get(id), retry: false }),
    client.prefetchQuery({ queryKey: ["listing", id], queryFn: () => api.listings.get(id), retry: false }),
    client.prefetchQuery({ queryKey: ["photos", id], queryFn: () => api.conversations.photos(id), retry: false })
      .then(() => warmPhotoThumbnails(client.getQueryData<Photo[]>(["photos", id]) ?? [])),
    client.prefetchQuery({ queryKey: ["messages", id], queryFn: () => api.conversations.messages(id), retry: false }),
  ]);
}

export function prefetchAssistantThread(client: QueryClient, id: string) {
  return client.prefetchQuery({
    queryKey: ["assistant", "messages", id], queryFn: () => api.assistant.messages(id), retry: false,
  });
}
