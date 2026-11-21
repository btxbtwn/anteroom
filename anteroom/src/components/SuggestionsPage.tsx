import { useDeferredValue, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronRightIcon } from "lucide-react";
import { api } from "../api/client";
import { IDLE_POLL_MS, pollMs } from "../api/polling";
import { isBrowserLinkClick, listingHref } from "../app/openListing";
import { useWorkspaceRuntime } from "../app/runtime";
import { listingCanRegenerate } from "../bulkRegenerate";
import { suggestionKindLabel } from "./SuggestionsPanel";
import { WorkspacePage, PageHeader, Panel, PageError, PageSkeleton, rowButtonClassName } from "./WorkspacePage";
import { ListingThumb, SelectCheckbox } from "./sidebar/ListingBits";
import { BulkSelectionDialogs } from "./sidebar/BulkSelection";
import { displayCoverUrl } from "./sidebar/coverSrc";
import type { Listing } from "./sidebar/listingShelves";
import { useListingSelection } from "./sidebar/useListingSelection";
import type { BulkRegenerate } from "./useBulkRegenerate";
import { Input } from "./ui/input";
import { Button } from "./ui/button";

/** Kept mounted when a listing is opened so search, scroll and return focus survive. */
export function SuggestionsPage({ active, onOpenListing, conversations, bulk }: {
  active: boolean;
  onOpenListing: (id: string) => void;
  conversations: readonly Listing[] | undefined;
  bulk: BulkRegenerate;
}) {
  const { features } = useWorkspaceRuntime();
  const { data, isPending, error, refetch, isFetching } = useQuery({
    queryKey: ["suggestions"], queryFn: api.suggestions.list, enabled: active,
    refetchInterval: active ? () => pollMs(IDLE_POLL_MS) : false,
  });
  const [search, setSearch] = useState("");
  const needle = useDeferredValue(search.trim().toLowerCase());
  const suggestions = data?.suggestions ?? [];
  const visible = suggestions.filter(item => `${item.title} ${item.reason}`.toLowerCase().includes(needle));
  const listingsById = useMemo(() => new Map(conversations?.map(item => [item.id, item])), [conversations]);
  const pendingIds = new Set(bulk.pendingIds);
  const eligibleIds = features.has("bulk_regenerate") ? visible
    .filter(item => {
      const listing = listingsById.get(item.conversation_id);
      return listing && !pendingIds.has(listing.id) && listingCanRegenerate(listing);
    })
    .map(item => item.conversation_id) : [];
  const eligible = new Set(eligibleIds);
  const selection = useListingSelection({ eligibleIds, listingsById, suggestions, bulk });
  const selectedCount = selection.selectedIds.size;
  const run = bulk.run;
  const container = useRef<HTMLDivElement>(null);
  const returnId = useRef<string | null>(null);
  const returnScroll = useRef(0);
  useLayoutEffect(() => {
    if (!active) return;
    const links = container.current?.querySelectorAll<HTMLAnchorElement>("a[data-suggestion-id]");
    const previous = Array.from(links ?? []).find(link => link.dataset.suggestionId === returnId.current);
    const target = previous ?? container.current?.querySelector<HTMLHeadingElement>("h1");
    const restore = () => {
      const scroller = container.current?.querySelector<HTMLElement>('[role="region"]');
      if (scroller) scroller.scrollTop = returnScroll.current;
      if (target) {
        if (!previous) target.tabIndex = -1;
        target.focus({ preventScroll: true });
      }
    };
    restore();
    // WebKit lays out a previously hidden scroll container on the next frame.
    const frame = requestAnimationFrame(restore);
    return () => cancelAnimationFrame(frame);
  }, [active]);

  return (
    <div ref={container} hidden={!active} className="flex min-h-0 flex-1 flex-col [&[hidden]]:hidden">
      <WorkspacePage role="region" aria-label="Suggested listings">
        <PageHeader title="Suggestions" description="Review older listings worth relisting, ranked by age, recent sales and season." />
        <div className="flex flex-col gap-3">
          <Input type="search" aria-label="Search suggestions" placeholder="Search suggestions" value={search} onChange={event => {
            setSearch(event.target.value);
            selection.exitSelecting();
          }} />
          {data && <p role="status" className="text-xs text-muted-foreground tabular-nums">{visible.length} of {suggestions.length} suggestions · Highest priority first</p>}
        </div>
        {isPending && !error && <PageSkeleton label="Loading suggestions" />}
        {error && <div className="space-y-3"><PageError message={`Could not load suggestions: ${error.message}`} /><Button variant="outline" disabled={isFetching} onClick={() => void refetch()}>Try again</Button></div>}
        {data && visible.length === 0 && <p className="text-sm text-muted-foreground">{suggestions.length ? "No suggestions match your search." : "No suggestions right now. Your listings will appear here when there is something to review."}</p>}
        {(eligibleIds.length > 0 || run) && (
          <div role="region" aria-label="Suggestion actions" className="sticky top-0 z-10 flex flex-col gap-2 border-b border-border bg-background py-2">
            {eligibleIds.length > 0 && (
              <div className="flex flex-wrap items-center gap-2">
                <p role="status" className="mr-auto text-sm text-muted-foreground tabular-nums">{selectedCount} selected</p>
                <Button variant="outline" className="min-h-11" onClick={selection.toggleSelectAll}>
                  {selectedCount === eligibleIds.length ? "Clear selection" : "Select all shown"}
                </Button>
                <Button className="min-h-11" disabled={selectedCount === 0 || needle !== search.trim().toLowerCase()}
                  onClick={() => void selection.regenerateSelected()}>Regenerate selected</Button>
              </div>
            )}
            {run && (
              <div className="flex flex-wrap items-center gap-2">
                <p role="status" className="mr-auto text-sm text-muted-foreground">{run.finished} of {run.total} finished</p>
                <Button variant="outline" className="min-h-11" disabled={run.cancelRequested} onClick={bulk.cancel}>
                  {run.cancelRequested ? "Finishing…" : "Stop batch"}
                </Button>
              </div>
            )}
          </div>
        )}
        {visible.length > 0 && <Panel>
          <ul aria-label="Recommendations" className="divide-y divide-border/50">
            {visible.map(item => <li key={item.conversation_id} className="flex items-stretch">
              {eligibleIds.length > 0 && (
                <div className="flex w-11 shrink-0 items-center justify-center">
                  {eligible.has(item.conversation_id) && (
                    <SelectCheckbox label={`Select ${item.title}`} checked={selection.selectedIds.has(item.conversation_id)}
                      onToggle={shift => selection.toggleSelect(item.conversation_id, shift, eligibleIds)} />
                  )}
                </div>
              )}
              <a href={listingHref(item.conversation_id)} data-suggestion-id={item.conversation_id}
                className={`${rowButtonClassName} min-h-24 flex-1 no-underline`}
                onClick={event => {
                  if (isBrowserLinkClick(event)) return;
                  event.preventDefault();
                  returnId.current = item.conversation_id;
                  returnScroll.current = container.current?.querySelector<HTMLElement>('[role="region"]')?.scrollTop ?? 0;
                  onOpenListing(item.conversation_id);
                }}>
                <ListingThumb src={displayCoverUrl(item.cover_photo_url)} />
                <span className="flex min-w-0 flex-1 flex-col gap-1">
                  <span className="text-sm font-medium text-foreground">{item.title}</span>
                  <span className="text-xs text-foreground">{item.kind === "stale_active" ? "Review for relisting" : suggestionKindLabel(item.kind)}</span>
                  <span className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                    {item.age_days != null && <span>{item.age_days} days listed</span>}
                    {item.season_fit === "in" && <span>In season</span>}
                    {item.season_fit === "off" && <span>Out of season</span>}
                    {!!item.similar_sold && <span>{item.similar_sold} similar items sold recently</span>}
                  </span>
                  {item.kind !== "stale_active" && <span className="text-xs text-muted-foreground">{item.reason}</span>}
                </span>
                <ChevronRightIcon aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
              </a>
            </li>)}
          </ul>
        </Panel>}
      </WorkspacePage>
      {active && <BulkSelectionDialogs selection={selection} onOpenListing={onOpenListing} />}
    </div>
  );
}
