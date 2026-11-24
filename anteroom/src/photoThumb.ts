/** Pixel size of a photo shown in a 64px tile. WebKit decodes the file, not the CSS box. */
export const STRIP_THUMB_SIZE = 192;

/** The version is shared by the hosted and desktop URLs for the same photo. */
export function photoVersion(url: string): string {
  try {
    return new URL(url, "http://anteroom.local").searchParams.get("v") ?? "";
  } catch {
    return "";
  }
}

/** The cached thumbnail for a listing photo. The lightbox keeps `photo.url`. */
export function photoThumbUrl(photo: { id: string; url: string }): string {
  const params = new URLSearchParams({ size: String(STRIP_THUMB_SIZE) });
  const version = photoVersion(photo.url);
  if (version) params.set("v", version);
  return `/api/photos/${encodeURIComponent(photo.id)}/thumb?${params}`;
}
