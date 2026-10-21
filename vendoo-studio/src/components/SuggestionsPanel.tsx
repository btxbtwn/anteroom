import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { IDLE_POLL_MS, pollMs } from "../api/polling";
import type { Suggestion, SuggestionKind } from "../api/types";
import { staleSuggestionIds } from "../bulkPriceDrop";
import { BulkPriceDropDialog } from "./BulkPriceDropDialog";
import { Button } from "./ui/button";
import { cn } from "../lib/utils";
import { ListingThumb, SelectCheckbox, ShelfHeader } from "./sidebar/ListingBits";

export const SUGGESTION_KIND_LABELS: Record<SuggestionKind, string> = {
  failed: "Fix failed listing",
  ready_to_generate: "Generate listing",
  fix_validation: "Fix listing fields",
  stale_active: "Refresh or consider price drop",
  ready_to_review: "Review and send",
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

export function suggestionCaption(
  suggestion: Pick<Suggestion, "kind" | "age_days" | "similar_sold" | "season_fit">,
  compact: boolean,
): string {
  const label = suggestionKindLabel(suggestion.kind);
  if (!compact || suggestion.kind !== "stale_active" || suggestion.age_days == null) {
    return label;
  }
  const signals = staleSignals(suggestion);
  return [staleAgeLabel(suggestion.age_days), ...(signals.length ? signals : [label])].join(" · ");
}

export function SuggestionsPanel({
  variant,
  selectedConvId,
  onSelect,
  selecting = false,
  selectedIds,
  selectLocked = false,
  canSelect,
  onToggleSelect,
}: {
  variant: "workspace" | "sidebar";
  selectedConvId?: string | null;
  onSelect: (conversationId: string) => void;
  selecting?: boolean;
  selectedIds?: ReadonlySet<string>;
  selectLocked?: boolean;
  canSelect?: (suggestion: Suggestion) => boolean;
  onToggleSelect?: (id: string, shift: boolean) => void;
}) {
  const { data, isFetched } = useQuery({
    queryKey: ["suggestions"],
    queryFn: api.suggestions.list,
    refetchInterval: () => pollMs(IDLE_POLL_MS),
  });
  const suggestions = data?.suggestions || [];
  const [expanded, setExpanded] = useState(true);
  const [markdownIds, setMarkdownIds] = useState<string[] | null>(null);
  const staleIds = staleSuggestionIds(suggestions);
  const markdown = staleIds.length > 0 && !selecting ? (
    <Button
      type="button"
      variant="ghost"
      size="xs"
      className="mx-2 mt-1 mb-0.5 self-start"
      title="Review a lower price for each stale listing. Saves in Studio only; nothing is sent to Vendoo."
      onClick={() => setMarkdownIds(staleIds)}
    >
      {markDownStaleLabel(staleIds.length)}
    </Button>
  ) : null;
  // Outside the shelf, so a refetch that empties the feed cannot close the review.
  const markdownDialog = markdownIds ? (
    <BulkPriceDropDialog conversationIds={markdownIds} onClose={() => setMarkdownIds(null)} />
  ) : null;

  if (variant === "sidebar") {
    if (!suggestions.length) return markdownDialog;
    return (
      <>
        <section
          className="flex max-h-[42%] min-h-0 flex-[0_1_auto] flex-col px-(--sidebar-content-inset) pb-1 max-[900px]:max-h-none max-[900px]:flex-none"
          aria-label="Suggestions"
        >
          <ShelfHeader
            label={expanded ? "Suggestions" : `Suggestions (${suggestions.length})`}
            expanded={expanded}
            onToggle={() => setExpanded((value) => !value)}
          />
          {expanded ? (
            <div className="flex min-h-0 flex-col gap-px overflow-y-auto overscroll-contain max-[900px]:overflow-visible">
              {suggestions.map((item) => (
                <SuggestionRow
                  key={item.conversation_id}
                  suggestion={item}
                  selected={selectedConvId === item.conversation_id}
                  compact
                  selecting={selecting}
                  checked={Boolean(selectedIds?.has(item.conversation_id))}
                  canSelect={canSelect ? canSelect(item) : true}
                  selectLocked={selectLocked}
                  onToggleSelect={onToggleSelect}
                  onSelect={onSelect}
                />
              ))}
              {markdown}
            </div>
          ) : null}
        </section>
        {markdownDialog}
      </>
    );
  }

  return (
    <section
      className="flex max-h-[min(52vh,28rem)] w-[min(100%,28rem)] flex-col gap-1 overflow-y-auto text-left"
      aria-label="Suggestions"
    >
      {isFetched && suggestions.length === 0 ? (
        <p className="mt-1 text-center text-xs text-muted-foreground">No suggestions right now</p>
      ) : (
        suggestions.map((item) => (
          <SuggestionRow
            key={item.conversation_id}
            suggestion={item}
            selected={selectedConvId === item.conversation_id}
            compact={false}
            onSelect={onSelect}
          />
        ))
      )}
      {markdown}
      {markdownDialog}
    </section>
  );
}

function SuggestionRow({
  suggestion,
  selected,
  compact,
  selecting = false,
  checked = false,
  canSelect = true,
  selectLocked = false,
  onToggleSelect,
  onSelect,
}: {
  suggestion: Suggestion;
  selected: boolean;
  compact: boolean;
  selecting?: boolean;
  checked?: boolean;
  canSelect?: boolean;
  selectLocked?: boolean;
  onToggleSelect?: (id: string, shift: boolean) => void;
  onSelect: (conversationId: string) => void;
}) {
  const label = suggestionCaption(suggestion, compact);
  const body = (
    <>
      <ListingThumb src={suggestion.cover_photo_url} />
      <div className="min-w-0 flex-1">
        <div className={cn("text-sm font-medium leading-snug", compact && "truncate")}>{suggestion.title}</div>
        {compact ? (
          <div className="mt-px truncate text-2xs leading-snug text-sidebar-muted-foreground">{label}</div>
        ) : (
          <>
            <div className="mt-0.5 text-xs font-medium leading-snug text-foreground">{label}</div>
            <div className="mt-px text-2xs leading-snug text-muted-foreground">{suggestion.reason}</div>
          </>
        )}
      </div>
    </>
  );
  const rowClass = cn(
    "flex w-full min-w-0 items-center gap-2.5 rounded-[var(--control-radius)] text-left text-sidebar-foreground/90 transition-colors",
    compact
      ? "px-2 py-1.5 hover:bg-sidebar-row-hover data-[active=true]:bg-sidebar-row-selected"
      : "border border-border bg-card px-2.5 py-2 hover:bg-accent data-[active=true]:bg-accent",
    !selecting && "cursor-pointer",
  );
  const title = `${suggestionKindLabel(suggestion.kind)}. ${suggestion.reason}`;

  if (!selecting) {
    return (
      <button
        type="button"
        data-active={selected}
        className={rowClass}
        title={title}
        onClick={() => onSelect(suggestion.conversation_id)}
      >
        {body}
      </button>
    );
  }

  return (
    <div data-active={selected} className={rowClass}>
      <SelectCheckbox
        checked={checked}
        label={`${checked ? "Deselect" : "Select"} ${suggestion.title}`}
        title={canSelect ? "Include in regenerate" : "Add photos before regenerating"}
        disabled={!canSelect || selectLocked}
        onToggle={(shift) => onToggleSelect?.(suggestion.conversation_id, shift)}
      />
      <button
        type="button"
        className="flex min-w-0 flex-1 cursor-pointer items-center gap-2.5 text-left"
        title={title}
        onClick={() => onSelect(suggestion.conversation_id)}
      >
        {body}
      </button>
    </div>
  );
}
