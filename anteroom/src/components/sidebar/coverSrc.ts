/**
 * Where a 40px cover should load from.
 *
 * A path Anteroom already serves (a local thumbnail) is used as-is. Anything
 * else must be https, and is fetched through the cover thumbnail so WebKit
 * never decodes the remote original. A URL we will not rewrite is dropped.
 */
export function displayCoverUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  if (url.startsWith("/") && !url.startsWith("//")) return url;
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  if (parsed.protocol !== "https:") return null;
  return `/api/cover-thumb?url=${encodeURIComponent(url)}`;
}
