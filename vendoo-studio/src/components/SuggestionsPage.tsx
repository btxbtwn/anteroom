import { useDeferredValue, useLayoutEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronRightIcon } from "lucide-react";
import { api } from "../api/client";
import { IDLE_POLL_MS, pollMs } from "../api/polling";
import { isBrowserLinkClick, listingHref } from "../app/openListing";
import { suggestionKindLabel } from "./SuggestionsPanel";
import { WorkspacePage, PageHeader, Panel, PageError, PageSkeleton, rowButtonClassName } from "./WorkspacePage";
import { ListingThumb } from "./sidebar/ListingBits";
import { displayCoverUrl } from "./sidebar/coverSrc";
import { Input } from "./ui/input";
import { Button } from "./ui/button";

/** Kept mounted when a listing is opened so search, scroll and return focus survive. */
export function SuggestionsPage({ active, onOpenListing }: { active: boolean; onOpenListing: (id: string) => void }) {
  const { data, isPending, error, refetch, isFetching } = useQuery({
    queryKey: ["suggestions"], queryFn: api.suggestions.list, enabled: active,
    refetchInterval: active ? () => pollMs(IDLE_POLL_MS) : false,
  });
  const [search, setSearch] = useState("");
  const needle = useDeferredValue(search.trim().toLowerCase());
  const suggestions = data?.suggestions ?? [];
  const visible = suggestions.filter(item => `${item.title} ${item.reason}`.toLowerCase().includes(needle));
  const container = useRef<HTMLDivElement>(null);
  const returnId = useRef<string | null>(null);
  useLayoutEffect(() => {
    if (!active) return;
    const links = container.current?.querySelectorAll<HTMLAnchorElement>("a[data-suggestion-id]");
    const previous = Array.from(links ?? []).find(link => link.dataset.suggestionId === returnId.current);
    const target = previous ?? container.current?.querySelector<HTMLHeadingElement>("h1");
    if (target) {
      if (!previous) target.tabIndex = -1;
      target.focus({ preventScroll: true });
    }
  }, [active]);

  return (
    <div ref={container} hidden={!active} className="flex min-h-0 flex-1 flex-col [&[hidden]]:hidden">
      <WorkspacePage role="region" aria-label="Suggested listings">
        <PageHeader title="Suggestions" description="Review older listings worth relisting, ranked by age, recent sales and season." />
        <div className="flex flex-col gap-3">
          <Input type="search" aria-label="Search suggestions" placeholder="Search suggestions" value={search} onChange={event => setSearch(event.target.value)} />
          {data && <p role="status" className="text-xs text-muted-foreground tabular-nums">{visible.length} of {suggestions.length} suggestions · Highest priority first</p>}
        </div>
        {isPending && !error && <PageSkeleton label="Loading suggestions" />}
        {error && <div className="space-y-3"><PageError message={`Could not load suggestions: ${error.message}`} /><Button variant="outline" disabled={isFetching} onClick={() => void refetch()}>Try again</Button></div>}
        {data && visible.length === 0 && <p className="text-sm text-muted-foreground">{suggestions.length ? "No suggestions match your search." : "No suggestions right now. Your listings will appear here when there is something to review."}</p>}
        {visible.length > 0 && <Panel>
          <ul aria-label="Recommendations" className="divide-y divide-border/50">
            {visible.map(item => <li key={item.conversation_id}>
              <a href={listingHref(item.conversation_id)} aria-label={item.title} data-suggestion-id={item.conversation_id}
                className={`${rowButtonClassName} min-h-24 no-underline`}
                onClick={event => {
                  if (isBrowserLinkClick(event)) return;
                  event.preventDefault(); returnId.current = item.conversation_id; onOpenListing(item.conversation_id);
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
    </div>
  );
}
