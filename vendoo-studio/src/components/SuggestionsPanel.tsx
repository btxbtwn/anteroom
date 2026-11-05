import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { IDLE_POLL_MS, pollMs } from "../api/polling";
import type { Suggestion, SuggestionKind } from "../api/types";
import { staleSuggestionIds } from "../bulkPriceDrop";
import { BulkPriceDropDialog } from "./BulkPriceDropDialog";
import { useWorkspaceRuntime } from "../app/runtime";
import { Button } from "./ui/button";
import { ListingThumb } from "./sidebar/ListingBits";
import { displayCoverUrl } from "./sidebar/coverSrc";

export const SUGGESTION_KIND_LABELS: Record<SuggestionKind, string> = {
  failed: "Fix failed listing",
  ready_to_generate: "Generate listing",
  fix_validation: "Fix listing fields",
  stale_active: "Refresh or consider price drop",
  ready_to_review: "Review listing",
};

export function suggestionKindLabel(kind: string): string {
  return SUGGESTION_KIND_LABELS[kind as SuggestionKind] || kind.replace(/_/g, " ");
}

export function markDownStaleLabel(count: number): string {
  return count === 1 ? "Mark down 1 stale listing" : `Mark down ${count} stale listings`;
}

export function staleAgeLabel(days: number): string {
  return days === 1 ? "1 day" : `${days} days`;
}

/** Why a stale row ranks where it does: like items selling now, and its season. */
export function staleSignals(suggestion: Pick<Suggestion, "similar_sold" | "season_fit">): string[] {
  const signals: string[] = [];
  if (suggestion.similar_sold) signals.push(`${suggestion.similar_sold} like it sold`);
  if (suggestion.season_fit === "in") signals.push("In season");
  return signals;
}

const ROW_HINTS: Record<SuggestionKind, string> = {
  failed: "Last send failed",
  ready_to_generate: "Ready to generate",
  fix_validation: "Fields to fix",
  stale_active: "Stale",
  ready_to_review: "Needs review",
};

/** The suggestion as a state on the listing's own sidebar row. */
export function suggestionHint(
  suggestion: Pick<Suggestion, "kind" | "age_days" | "similar_sold" | "season_fit">,
): string {
  const hint = ROW_HINTS[suggestion.kind as SuggestionKind] || suggestionKindLabel(suggestion.kind);
  if (suggestion.kind !== "stale_active" || suggestion.age_days == null) return hint;
  return [`${hint} ${staleAgeLabel(suggestion.age_days)}`, ...staleSignals(suggestion)].join(" · ");
}

/** The suggestions feed on the empty Inventory page. The sidebar shows the same state on each row. */
export function SuggestionsPanel({
  selectedConvId,
  onSelect,
}: {
  selectedConvId?: string | null;
  onSelect: (conversationId: string) => void;
}) {
  const { features } = useWorkspaceRuntime();
  const { data, isFetched, error } = useQuery({
    queryKey: ["suggestions"],
    queryFn: api.suggestions.list,
    refetchInterval: () => pollMs(IDLE_POLL_MS),
  });
  const suggestions = data?.suggestions || [];
  const [markdownIds, setMarkdownIds] = useState<string[] | null>(null);
  const staleIds = staleSuggestionIds(suggestions);

  return (
    <section
      className="flex max-h-[min(52vh,28rem)] w-[min(100%,28rem)] flex-col gap-1 overflow-y-auto text-left"
      aria-label="Suggestions"
    >
      {error ? (
        <p role="alert" className="mx-2 my-1 text-xs text-destructive-foreground">
          Could not load suggestions: {error.message}
        </p>
      ) : null}
      {isFetched && suggestions.length === 0 && !error ? (
        <p className="mt-1 text-center text-xs text-muted-foreground">No suggestions right now</p>
      ) : (
        suggestions.map((item) => (
          <SuggestionRow
            key={item.conversation_id}
            suggestion={item}
            selected={selectedConvId === item.conversation_id}
            onSelect={onSelect}
          />
        ))
      )}
      {features.has("bulk_markdowns") && staleIds.length > 0 ? (
        <Button
          type="button"
          variant="ghost"
          size="xs"
          className="mx-2 mt-1 mb-0.5 self-start"
          title="Review a lower price for each stale listing. Saves in Anteroom only."
          onClick={() => setMarkdownIds(staleIds)}
        >
          {markDownStaleLabel(staleIds.length)}
        </Button>
      ) : null}
      {/* Outside the list, so a refetch that empties the feed cannot close the review. */}
      {markdownIds ? (
        <BulkPriceDropDialog conversationIds={markdownIds} onClose={() => setMarkdownIds(null)} />
      ) : null}
    </section>
  );
}

function SuggestionRow({
  suggestion,
  selected,
  onSelect,
}: {
  suggestion: Suggestion;
  selected: boolean;
  onSelect: (conversationId: string) => void;
}) {
  return (
    <button
      type="button"
      data-active={selected}
      className="flex w-full min-w-0 cursor-pointer items-center gap-2.5 rounded-[var(--control-radius)] border border-border bg-card px-2.5 py-2 text-left text-sidebar-foreground/90 transition-colors hover:bg-accent data-[active=true]:bg-accent"
      title={`${suggestionKindLabel(suggestion.kind)}. ${suggestion.reason}`}
      onClick={() => onSelect(suggestion.conversation_id)}
    >
      <ListingThumb src={displayCoverUrl(suggestion.cover_photo_url)} />
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium leading-snug">{suggestion.title}</div>
        <div className="mt-0.5 text-xs font-medium leading-snug text-foreground">{suggestionKindLabel(suggestion.kind)}</div>
        <div className="mt-px text-2xs leading-snug text-muted-foreground">{suggestion.reason}</div>
      </div>
    </button>
  );
}
