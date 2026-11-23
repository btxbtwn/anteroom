/**
 * Colours a seller paints into a photo: annotation ink, a frame, an AI Studio
 * backdrop. They are image content, not interface chrome, so they are plain
 * colour values rather than theme tokens (scripts/check-design.py exempts this file).
 */

/** Annotation ink: red for flaws, then high-contrast choices. */
export const MARK_COLORS = ["#E5484D", "#FFB224", "#30A46C", "#0090FF", "#FFFFFF", "#111111"] as const;

/** Solid backdrops marketplaces favour: white first (eBay, Etsy), then soft neutrals. */
export const BACKDROP_COLORS = ["#FFFFFF", "#F4F4F5", "#E7E5E4", "#F5F0E6", "#DDE6ED", "#111111"] as const;

/** Frame colours for a border. */
export const BORDER_COLORS = ["#FFFFFF", "#111111", "#E7E5E4", "#F5F0E6"] as const;

export const DEFAULT_MARK_COLOR = MARK_COLORS[0];
export const DEFAULT_BACKDROP = BACKDROP_COLORS[0];
export const DEFAULT_BORDER_COLOR = BORDER_COLORS[0];

/** A colour the server accepts: `#RRGGBB`. */
export function isHexColor(value: string): boolean {
  return /^#[0-9a-fA-F]{6}$/.test(value);
}
