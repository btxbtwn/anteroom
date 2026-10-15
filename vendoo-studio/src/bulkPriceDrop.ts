/**
 * Mark down several stale listings at once. Each row starts on the suggestion
 * the listing's own sales and prior drops imply; the seller can edit the price
 * or skip the row. Applying saves Studio revisions only — nothing is sent to
 * Vendoo until the seller presses Update Vendoo on each listing.
 */

import type { MarkdownApplyResult, MarkdownDropRequest, MarkdownPreviewRow, Suggestion } from "./api/types";

export type MarkdownDraft = {
  id: string;
  title: string;
  coverUrl: string | null;
  revisionId: string | null;
  current: number | null;
  suggested: number | null;
  reason: string;
  /** Why this listing cannot be marked down; such a row is always skipped. */
  unavailable: string | null;
  priceText: string;
  skip: boolean;
};

export function draftsFromPreview(rows: readonly MarkdownPreviewRow[]): MarkdownDraft[] {
  return rows.map((row) => ({
    id: row.conversation_id,
    title: row.title,
    coverUrl: row.cover_photo_url,
    revisionId: row.revision_id,
    current: row.current_price,
    suggested: row.suggested_price,
    reason: row.suggested_reason,
    unavailable: row.skip_reason,
    priceText: row.suggested_price != null ? String(row.suggested_price) : "",
    skip: Boolean(row.skip_reason),
  }));
}

/** Why the typed price cannot be applied, or null when it can (or the row is skipped). */
export function draftError(draft: MarkdownDraft): string | null {
  if (draft.skip || draft.unavailable) return null;
  const text = draft.priceText.trim();
  if (!text) return "Enter a price or skip";
  const price = Number(text);
  if (!Number.isFinite(price) || !Number.isInteger(price) || price < 1) return "Whole dollars only";
  if (draft.current != null && price >= draft.current) return "Must be lower than now";
  return null;
}

export function markdownDrops(drafts: readonly MarkdownDraft[]): MarkdownDropRequest[] {
  return drafts
    .filter((draft) => !draft.skip && !draft.unavailable && draftError(draft) === null)
    .map((draft) => ({
      conversation_id: draft.id,
      price: Number(draft.priceText.trim()),
      expected_revision_id: draft.revisionId,
    }));
}

/** Apply needs at least one drop, and every row not skipped must be valid. */
export function canApplyMarkdowns(drafts: readonly MarkdownDraft[]): boolean {
  return markdownDrops(drafts).length > 0 && drafts.every((draft) => draftError(draft) === null);
}

export function dropPercentLabel(current: number | null, priceText: string): string {
  const price = Number(priceText.trim());
  if (current == null || !(current > 0) || !(price > 0) || price >= current) return "";
  return `−${Math.round((1 - price / current) * 1000) / 10}%`;
}

export function applyMarkdownsLabel(count: number): string {
  if (count === 0) return "Apply markdowns";
  return count === 1 ? "Apply 1 markdown" : `Apply ${count} markdowns`;
}

export type MarkdownOutcome = {
  id: string;
  title: string;
  /** Set once the drop saved. */
  from: number | null;
  to: number | null;
  /** Set when the drop could not be saved. */
  reason: string | null;
};

/** Results in the order the seller reviewed them. */
export function markdownOutcomes(
  drafts: readonly MarkdownDraft[],
  result: MarkdownApplyResult,
): MarkdownOutcome[] {
  const applied = new Map(result.applied.map((row) => [row.conversation_id, row]));
  const skipped = new Map(result.skipped.map((row) => [row.conversation_id, row.reason]));
  const outcomes: MarkdownOutcome[] = [];
  for (const draft of drafts) {
    const saved = applied.get(draft.id);
    if (saved) {
      outcomes.push({ id: draft.id, title: draft.title, from: saved.previous_price, to: saved.price, reason: null });
      continue;
    }
    const reason = skipped.get(draft.id);
    if (reason) outcomes.push({ id: draft.id, title: draft.title, from: null, to: null, reason });
  }
  return outcomes;
}

export function markdownSummary(outcomes: readonly MarkdownOutcome[]): string {
  const saved = outcomes.filter((outcome) => outcome.reason === null).length;
  const failed = outcomes.length - saved;
  const parts: string[] = [];
  if (saved) parts.push(`${saved} ${saved === 1 ? "price" : "prices"} dropped in Studio`);
  if (failed) parts.push(`${failed} not changed`);
  return parts.join(", ") || "No prices changed";
}

/** The stale listings the Suggestions feed is asking the seller to refresh or mark down. */
export function staleSuggestionIds(suggestions: readonly Pick<Suggestion, "conversation_id" | "kind">[]): string[] {
  return suggestions.filter((item) => item.kind === "stale_active").map((item) => item.conversation_id);
}
