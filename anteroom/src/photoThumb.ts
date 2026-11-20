/** Pixel size of a photo shown in a 64px tile. WebKit decodes the file, not the CSS box. */
export const STRIP_THUMB_SIZE = 192;

/** The cached thumbnail for a listing photo. The lightbox keeps `photo.url`. */
export function photoThumbUrl(photo: { id: string; url: string }): string {
  const params = new URLSearchParams({ size: String(STRIP_THUMB_SIZE) });
  try {
    const version = new URL(photo.url, "http://anteroom.local").searchParams.get("v");
    if (version) params.set("v", version);
  } catch {
    // The id still names the thumbnail when the photo URL cannot be read.
  }
  return `/api/photos/${encodeURIComponent(photo.id)}/thumb?${params}`;
}
