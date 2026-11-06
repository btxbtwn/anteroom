/*
 * The inventory as a table: one row per listing, one column per marketplace,
 * each cell that marketplace's status, the way Nifty and List Perfectly lay out
 * a crosslisted inventory. A cell opens the listing's editor on that
 * marketplace's tab. Nothing here lists, updates or delists.
 */
import { useDeferredValue, useMemo, useRef, useState, type CSSProperties } from "react";
import { useQuery } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ExternalLinkIcon, SearchIcon } from "lucide-react";
import { api } from "../api/client";
import { useWorkspaceRuntime } from "../app/runtime";
import { cn } from "../lib/utils";
import { editorTabs } from "./editorTabs";
import { formatMoney } from "./analyticsFormat";
import { LISTING_SORTS, LISTING_STATUS_TABS, statusCounts, matchesSearch, statusForSort, type ListingSortId, type ListingStatusFilter } from "./inventoryFilters";
import { inventoryRows, listedCounts, marketplaceCell, type MarketplaceCellKind } from "./inventoryTable";
import { MarketplaceLogo } from "./MarketplaceLogo";
import { marketplaceName } from "./marketplaceNames";
import { ListingThumb } from "./sidebar/ListingBits";
import { displayCoverUrl } from "./sidebar/coverSrc";
import type { Listing } from "./sidebar/listingShelves";
import { Badge } from "./ui/badge";
import { InputGroup, InputGroupAddon, InputGroupInput } from "./ui/input-group";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "./ui/select";
import { Toggle, ToggleGroup } from "./ui/toggle-group";
import { EmptyRow, Panel, WorkspacePage } from "./WorkspacePage";

const ROW_HEIGHT = 56;
// Narrow enough on a phone that the first marketplace column shows beside it.
const ITEM_MIN = "min(13rem, 42vw)";
const STATUS_LABELS: Record<ListingStatusFilter, string> = { all: "All", draft: "Draft", active: "Active", sold: "Sold", failed: "Failed" };
const BADGE_VARIANT: Record<MarketplaceCellKind, "success" | "info" | "warning" | "error" | "outline" | null> = {
  listed: "success",
  sold: "info",
  needs: "warning",
  failed: "error",
  working: "outline",
  draft: "outline",
  delisted: null,
  not_listed: null,
};

export function InventoryTablePage({
  conversations,
  query,
  onQueryChange,
  onOpenListing,
}: {
  conversations: Listing[] | undefined;
  query: string;
  onQueryChange: (query: string) => void;
  /** Opens the listing's editor, on a marketplace's tab when one is named. */
  onOpenListing: (id: string, tab?: string) => void;
}) {
  "use no memo"; // TanStack's mutable virtualizer must be read on every render.
  const runtime = useWorkspaceRuntime();
  const [status, setStatus] = useState<ListingStatusFilter>("all");
  const [sort, setSort] = useState<ListingSortId>("recent");
  const needle = useDeferredValue(query).trim().toLowerCase();
  const { data: marketplaceSettings } = useQuery({ queryKey: ["settings-marketplaces"], queryFn: api.settings.marketplaces });
  const { data: uiPrefs } = useQuery({ queryKey: ["settings-ui"], queryFn: api.settings.ui });
  const nameBySku = uiPrefs?.listing_names === "sku";
  const marketplaces = useMemo(
    () => editorTabs(marketplaceSettings?.selected, runtime).filter((id) => id !== "general"),
    [marketplaceSettings?.selected, runtime],
  );
  const all = useMemo(() => conversations ?? [], [conversations]);
  const searched = useMemo(() => all.filter((listing) => matchesSearch(listing, needle)), [all, needle]);
  const counts = useMemo(() => statusCounts(searched, Boolean(needle)), [searched, needle]);
  const rows = useMemo(() => inventoryRows(all, needle, status, sort), [all, needle, status, sort]);
  const listed = useMemo(() => listedCounts(rows, marketplaces), [rows, marketplaces]);
  const tabs = (["all", ...LISTING_STATUS_TABS] as const).filter((tab) => tab !== "failed" || counts.failed);

  const scrollRef = useRef<HTMLDivElement>(null);
  // oxlint-disable-next-line react/incompatible-library -- This component explicitly opts out of compiler memoization above.
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    getItemKey: (index) => rows[index].id,
    estimateSize: () => ROW_HEIGHT,
    overscan: 8,
    initialRect: { width: 1000, height: 800 },
  });
  const columns = { gridTemplateColumns: `minmax(${ITEM_MIN}, 2fr) 5.5rem repeat(${marketplaces.length}, minmax(8.5rem, 1fr))` } as CSSProperties;
  // The table's own minimum, so a long title cannot widen the item column past it.
  const tableWidth = { minWidth: `calc(${ITEM_MIN} + 5.5rem + ${marketplaces.length} * 8.5rem)` } as CSSProperties;

  return (
    <WorkspacePage width="expanded" className="h-full min-h-0 gap-4 pb-6">
      {/* The breadcrumb and the phone title bar already name the page. */}
      <h1 className="sr-only">Inventory</h1>
      <div className="flex flex-wrap items-center gap-2">
        <InputGroup className="w-full sm:w-64">
          <InputGroupAddon><SearchIcon /></InputGroupAddon>
          <InputGroupInput
            type="search"
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder="Search title, SKU or label"
            aria-label="Search listings by title, SKU or label"
          />
        </InputGroup>
        <ToggleGroup
          aria-label="Listing status"
          value={[status]}
          onValueChange={(next) => {
            const value = next[0] as ListingStatusFilter | undefined;
            if (value) setStatus(value);
          }}
        >
          {tabs.map((tab) => (
            <Toggle key={tab} value={tab}>
              {STATUS_LABELS[tab]}
              <span className="text-2xs text-muted-foreground tabular-nums">{counts[tab] ?? 0}</span>
            </Toggle>
          ))}
        </ToggleGroup>
        <Select value={sort} onValueChange={(value) => {
          if (!value) return;
          const next = value as ListingSortId;
          setSort(next);
          setStatus(statusForSort(next, status));
        }}>
          <SelectTrigger aria-label="Sort listings" className="ms-auto w-56 max-sm:ms-0 max-sm:w-full">
            <SelectValue>{LISTING_SORTS.find((item) => item.id === sort)?.label}</SelectValue>
          </SelectTrigger>
          <SelectPopup>{LISTING_SORTS.map((item) => <SelectItem key={item.id} value={item.id}>{item.label}</SelectItem>)}</SelectPopup>
        </Select>
      </div>

      <Panel divided={false} className="flex min-h-0 flex-1 flex-col bg-background">
        <div ref={scrollRef} className="min-h-0 flex-1 overflow-auto overscroll-contain" role="table" aria-label="Inventory" aria-rowcount={rows.length + 1}>
          <div style={tableWidth}>
            <div role="row" className="sticky top-0 z-20 grid border-b border-border/60 bg-background text-xs font-medium text-muted-foreground" style={columns}>
              <div role="columnheader" className="sticky left-0 z-10 bg-background px-4 py-2.5">Item</div>
              <div role="columnheader" className="px-3 py-2.5 text-right">Price</div>
              {marketplaces.map((id) => (
                <div key={id} role="columnheader" className="flex items-center gap-1.5 px-3 py-2.5">
                  <MarketplaceLogo id={id} label="" size={16} />
                  <span className="truncate text-foreground">{marketplaceName(id)}</span>
                  <span className="tabular-nums" aria-label={`${listed[id]} listed`}>{listed[id]}</span>
                </div>
              ))}
            </div>
            {rows.length === 0 ? (
              <EmptyRow>{all.length ? "No matching listings" : "No listings yet"}</EmptyRow>
            ) : (
              <div className="relative" style={{ height: virtualizer.getTotalSize() }}>
                {virtualizer.getVirtualItems().map((item) => {
                  const listing = rows[item.index];
                  const title = listing.title || "Untitled";
                  const name = (nameBySku && listing.sku?.trim()) || title;
                  return (
                    <div
                      key={item.key}
                      role="row"
                      aria-rowindex={item.index + 2}
                      className="group/row absolute inset-x-0 grid border-b border-border/40"
                      style={{ ...columns, height: ROW_HEIGHT, transform: `translateY(${item.start}px)` }}
                    >
                      <div role="rowheader" className="sticky left-0 z-10 min-w-0 bg-background">
                        <button
                          type="button"
                          className="flex size-full min-w-0 cursor-pointer items-center gap-3 px-4 text-left outline-none group-hover/row:bg-accent focus-visible:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
                          title={name === title ? title : `${name} · ${title}`}
                          onClick={() => onOpenListing(listing.id)}
                        >
                          <ListingThumb src={displayCoverUrl(listing.cover_photo_url || listing.vendoo_cover_url)} className="size-9" />
                          <span className="flex min-w-0 flex-col gap-0.5">
                            <span className="truncate text-sm font-medium text-foreground">{name}</span>
                            {listing.sku && !nameBySku ? <span className="truncate font-mono text-2xs text-muted-foreground">{listing.sku}</span> : null}
                          </span>
                        </button>
                      </div>
                      <div role="cell" className="flex items-center justify-end px-3 text-sm text-foreground tabular-nums group-hover/row:bg-accent">
                        {listing.price != null ? formatMoney(listing.price) : <span className="text-muted-foreground">—</span>}
                      </div>
                      {marketplaces.map((id) => (
                        <MarketplaceCellView key={id} listing={listing} marketplace={id} title={title} onOpen={() => onOpenListing(listing.id, id)} />
                      ))}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </Panel>
    </WorkspacePage>
  );
}

function MarketplaceCellView({ listing, marketplace, title, onOpen }: {
  listing: Listing;
  marketplace: string;
  title: string;
  onOpen: () => void;
}) {
  const cell = marketplaceCell(listing, marketplace);
  const variant = BADGE_VARIANT[cell.kind];
  const name = marketplaceName(marketplace);
  return (
    <div role="cell" className="flex min-w-0 items-center gap-1 px-1.5 group-hover/row:bg-accent">
      <button
        type="button"
        className="flex h-9 min-w-0 flex-1 cursor-pointer items-center rounded-md px-1.5 text-left outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
        aria-label={`${title} on ${name}: ${cell.label}. Open the ${name} form`}
        title={`Open the ${name} form`}
        onClick={onOpen}
      >
        {variant ? (
          <Badge variant={variant} className="truncate">{cell.label}</Badge>
        ) : (
          <span className={cn("truncate text-xs text-muted-foreground", cell.kind === "not_listed" && "text-muted-foreground/70")}>{cell.label}</span>
        )}
      </button>
      {cell.url ? (
        <a
          href={cell.url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground outline-none hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={`View ${title} on ${name}`}
          title={`View on ${name}`}
        >
          <ExternalLinkIcon className="size-3.5" />
        </a>
      ) : null}
    </div>
  );
}
