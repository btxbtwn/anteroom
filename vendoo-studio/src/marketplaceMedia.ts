// Each marketplace form's photos, like Vendoo's Photo Manager. A form sends the
// item's photos in item order until the seller changes its own pick, kept at
// `marketplace_photos.<marketplace>` on the listing; the server resolves the same
// pick the same way (services/marketplace_media.py) when it sends.
import type { ListingData, Photo } from "./api/types";
import type { ListingVideoRules } from "./api/listingVideos";
import { cloneListing } from "./listingPaths";

/** The marketplaces with a direct form and photos of their own. */
export const MEDIA_MARKETPLACES = new Set(["ebay", "etsy", "poshmark", "mercari", "depop", "vinted", "sellwild"]);

export interface FormPhotos {
  /** What the marketplace is sent, in order, before its limit. */
  picked: Photo[];
  /** Item photos and this form's own photos it leaves off. */
  unticked: Photo[];
  /** The form has its own pick rather than the item's photos. */
  custom: boolean;
}

export function pickedIds(listing: ListingData | undefined, marketplace: string): string[] {
  const picks = listing?.marketplace_photos;
  const chosen = picks && typeof picks === "object" ? (picks as Record<string, unknown>)[marketplace] : undefined;
  if (!Array.isArray(chosen)) return [];
  return [...new Set(chosen.map((id) => String(id).trim()).filter(Boolean))];
}

export function formPhotos(listing: ListingData | undefined, item: Photo[], own: Photo[], marketplace: string): FormPhotos {
  const usable = new Map([...item, ...own].map((photo) => [photo.id, photo]));
  const chosen = pickedIds(listing, marketplace).map((id) => usable.get(id)).filter((photo): photo is Photo => Boolean(photo));
  const picked = chosen.length ? chosen : item;
  const sent = new Set(picked.map((photo) => photo.id));
  return { picked, unticked: [...usable.values()].filter((photo) => !sent.has(photo.id)), custom: chosen.length > 0 };
}

/** The listing with this form's pick set. A pick equal to the item's photos in item order is no pick at all. */
export function withPick(listing: ListingData, marketplace: string, ids: string[], item: Photo[]): ListingData {
  const updated = cloneListing(listing);
  const picks = { ...(updated.marketplace_photos as Record<string, unknown> | undefined) };
  const itemOrder = item.map((photo) => photo.id);
  if (!ids.length || (ids.length === itemOrder.length && ids.every((id, index) => id === itemOrder[index]))) {
    delete picks[marketplace];
  } else {
    picks[marketplace] = ids;
  }
  if (Object.keys(picks).length) updated.marketplace_photos = picks;
  else delete updated.marketplace_photos;
  return updated;
}

/** The marketplace's video rules as one line, for the form's photos. */
export function videoRuleText(rules: ListingVideoRules | undefined): string {
  if (!rules) return "";
  const parts: string[] = [];
  if (rules.mime_types?.length) {
    const named = rules.mime_types.every((type) => type === "video/mp4" || type === "video/quicktime");
    parts.push(named ? "MP4 or MOV" : "MP4, MOV and other common formats");
  }
  if (rules.shorter_than !== undefined) parts.push(`shorter than ${rules.shorter_than} seconds`);
  if (rules.min_seconds !== undefined && rules.max_seconds !== undefined) parts.push(`${rules.min_seconds} to ${rules.max_seconds} seconds`);
  if (rules.size_label) parts.push(`up to ${rules.size_label}`);
  return `One video: ${parts.join(", ")}.`;
}

export function isVideoFile(file: { name?: string; type?: string }): boolean {
  const type = (file.type || "").toLowerCase();
  if (type) return type.startsWith("video/");
  return /\.(mp4|mov|m4v|avi|mpe?g|3gp|flv)$/i.test(file.name || "");
}
