import type { SizeTagReadResult } from "./api/types";

interface SizeTagOutcome {
  /** The row's Size: what the seller typed, or the tag's size when the field was blank. */
  size: string;
  /** True when the size came from the tag and the seller has not touched it. */
  suggested: boolean;
  /** True when the seller has to look at the tag: nothing was filled, or it disagrees with what was typed. */
  needsReview: boolean;
  /** Plain-words status for the item. Never names the provider or model. */
  note: string;
}

const same = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();

/**
 * Fold a size-tag reading into what the seller typed. A typed size always wins (0 is a
 * size, not a blank); only a blank Size takes the tag's size, and only when both reads
 * agreed. A missing, unreadable or conflicting tag leaves Size blank for the seller.
 */
export function applySizeTagReading(typed: string, result: SizeTagReadResult): SizeTagOutcome {
  const hasTyped = typed.trim() !== "";
  if (result.status === "accepted" && result.size != null && result.size.trim() !== "") {
    if (!hasTyped) return { size: result.size, suggested: true, needsReview: false, note: "Size filled from the tag. Check it." };
    if (same(typed, result.size)) return { size: typed, suggested: false, needsReview: false, note: "Size matches the tag." };
    return { size: typed, suggested: false, needsReview: true, note: `The tag reads ${result.size}. Check the size you typed.` };
  }
  if (hasTyped) return { size: typed, suggested: false, needsReview: false, note: "" };
  const note = result.status === "conflict" && result.sizes.length > 1
    ? `The photos show different sizes (${result.sizes.join(", ")}). Check the tag and type the size.`
    : result.status === "not_found"
      ? "No size tag found in the photos. Type the size from the tag."
      : "The size tag could not be read. Type the size from the tag.";
  return { size: typed, suggested: false, needsReview: true, note };
}
