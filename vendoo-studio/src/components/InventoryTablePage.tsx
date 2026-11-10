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
import type { MissingField, NativeMarketplace } from "../api/nativeMarketplaces";
import { useWorkspaceRuntime } from "../app/runtime";
import { cn } from "../lib/utils";
import { editorTabs } from "./editorTabs";
import { formatMoney } from "./analyticsFormat";
import { LISTING_SORTS, LISTING_STATUS_LABELS, LISTING_STATUS_TABS, statusCounts, matchesSearch, statusForSort, type ListingSortId, type ListingStatusFilter } from "./inventoryFilters";
import { marketplacePrice, marketplacePriceDescription } from "./inventoryPrices";
import { inventoryRows, listedCounts, marketplaceCell, type MarketplaceCellKind } from "./inventoryTable";
import { MarketplaceLogo } from "./MarketplaceLogo";
import { marketplaceName } from "./marketplaceNames";
import { ListingThumb } from "./sidebar/ListingBits";
import { displayCoverUrl } from "./sidebar/coverSrc";
import type { Listing } from "./sidebar/listingShelves";
import { Badge } from "./ui/badge";
import { Skeleton } from "./ui/skeleton";
import { InputGroup, InputGroupAddon, InputGroupInput } from "./ui/input-group";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "./ui/select";
import { Toggle, ToggleGroup } from "./ui/toggle-group";
import { useInventoryReadiness } from "./useInventoryReadiness";
import { EmptyRow, Panel, WorkspacePage } from "./WorkspacePage";
import { setListingSearch, useListingSearch } from "./sidebar/listingSearch";

const ROW_HEIGHT = 56;
// Narrow enough on a phone that the first marketplace column shows beside it.
const ITEM_MIN = "min(13rem, 42vw)";
// Room for a status badge and the live price beside it. Marketplace columns
// stay this wide; the item column takes whatever the page has left.
const CELL_WIDTH = "9.5rem";
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
  loadFailed = false,
  onOpenListing,
  sidebarSearch = false,
}: {
  conversations: Listing[] | undefined;
  /** The listings sidebar is open beside the table; its search box already filters the table. */
  sidebarSearch?: boolean;
  /** The inventory could not be read yet. */
  loadFailed?: boolean;
  /** Opens the listing's editor, on a marketplace's tab when one is named. */
  onOpenListing: (id: string, tab?: string) => void;
}) {
  const query = useListingSearch();
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
  // Until the inventory arrives nothing is known: no counts and no "No listings yet".
  const loading = conversations === undefined;
  const all = useMemo(() => conversations ?? [], [conversations]);
  const searched = useMemo(() => all.filter((listing) => matchesSearch(listing, needle)), [all, needle]);
  const counts = useMemo(() => statusCounts(searched, Boolean(needle)), [searched, needle]);
  const rows = useMemo(() => inventoryRows(all, needle, status, sort), [all, needle, status, sort]);
  const listed = useMemo(() => listedCounts(rows, marketplaces), [rows, marketplaces]);
  const tabs = (["all", ...LISTING_STATUS_TABS] as const).filter((tab) => tab !== "failed" || counts.failed);

  const scrollRef = useRef<HTMLDivElement>(null);
  // oxlint-disable-next-line react/incompatible-library -- The app does not use the React Compiler; nothing memoizes this component.
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    getItemKey: (index) => rows[index].id,
    estimateSize: () => ROW_HEIGHT,
    overscan: 8,
    initialRect: { width: 1000, height: 800 },
  });
  const visible = virtualizer.getVirtualItems();
  const readiness = useInventoryReadiness(rows, visible[0]?.index ?? 0, visible[visible.length - 1]?.index ?? -1);
  const columns = { gridTemplateColumns: `minmax(${ITEM_MIN}, 1fr) 5.5rem repeat(${marketplaces.length}, ${CELL_WIDTH})` } as CSSProperties;
  // The table's own minimum, so a long title cannot widen the item column past it.
  const tableWidth = { minWidth: `calc(${ITEM_MIN} + 5.5rem + ${marketplaces.length} * ${CELL_WIDTH})` } as CSSProperties;

  return (
    <WorkspacePage width="expanded" className="h-full min-h-0 gap-4 pb-6">
      {/* The breadcrumb and the phone title bar already name the page. */}
      <h1 className="sr-only">Inventory</h1>
      <div className="flex flex-wrap items-center gap-2">
        {/* One search box on screen: the sidebar's shares this text. */}
        {sidebarSearch ? null : (
          <InputGroup className="w-full sm:w-64">
            <InputGroupAddon><SearchIcon /></InputGroupAddon>
            <InputGroupInput
              type="search"
              value={query}
              onChange={(event) => setListingSearch(event.target.value)}
              placeholder="Search title, SKU or label"
              aria-label="Search listings by title, SKU or label"
            />
          </InputGroup>
        )}
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
              {LISTING_STATUS_LABELS[tab]}
              {loading ? null : " "}
              {loading ? null : <span className="text-2xs text-muted-foreground tabular-nums">{counts[tab] ?? 0}</span>}
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
                  {loading ? null : <span className="tabular-nums" aria-label={`${listed[id]} listed`}>{listed[id]}</span>}
                </div>
              ))}
            </div>
            {loading && loadFailed ? (
              <EmptyRow>Could not load listings. Retrying…</EmptyRow>
            ) : loading ? (
              <InventoryRowsSkeleton columns={columns} marketplaces={marketplaces.length} />
            ) : rows.length === 0 ? (
              <EmptyRow>{all.length ? "No matching listings" : "No listings yet"}</EmptyRow>
            ) : (
              <div className="relative" style={{ height: virtualizer.getTotalSize() }}>
                {visible.map((item) => {
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
                            {/* Two lines fit the row; the button's tooltip has the whole title. */}
                            <span className={cn("text-sm font-medium text-foreground", listing.sku && !nameBySku ? "truncate" : "line-clamp-2 leading-tight")}>{name}</span>
                            {listing.sku && !nameBySku ? <span className="truncate font-mono text-2xs text-muted-foreground">{listing.sku}</span> : null}
                          </span>
                        </button>
                      </div>
                      <div role="cell" className="flex items-center justify-end px-3 text-sm text-foreground tabular-nums group-hover/row:bg-accent">
                        {listing.price != null ? formatMoney(listing.price) : <span className="text-muted-foreground">—</span>}
                      </div>
                      {marketplaces.map((id) => (
                        <MarketplaceCellView key={id} listing={listing} marketplace={id} title={title} onOpen={() => onOpenListing(listing.id, id)}
                          missing={readiness[listing.id]?.marketplaces[id as NativeMarketplace]?.missing} />
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

function MarketplaceCellView({ listing, marketplace, title, onOpen, missing }: {
  listing: Listing;
  marketplace: string;
  title: string;
  onOpen: () => void;
  /** The fields this marketplace still needs, once read. */
  missing?: readonly MissingField[];
}) {
  const cell = marketplaceCell(listing, marketplace, missing);
  const variant = BADGE_VARIANT[cell.kind];
  const name = marketplaceName(marketplace);
  const price = marketplacePrice(listing, marketplace);
  const priceText = price ? marketplacePriceDescription(price, name, listing.price) : null;
  return (
    <div role="cell" className="flex min-w-0 items-center gap-1 px-1.5 group-hover/row:bg-accent">
      <button
        type="button"
        className="flex h-9 min-w-0 flex-1 cursor-pointer items-center rounded-md px-1.5 text-left outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
        aria-label={`${title} on ${name}: ${cell.detail ?? cell.label}${priceText ? `, ${priceText}` : ""}. Open the ${name} form`}
        title={[cell.detail, priceText, `Open the ${name} form`].filter(Boolean).join(". ")}
        onClick={onOpen}
      >
        {variant ? (
          <Badge variant={variant} className="truncate">{cell.label}</Badge>
        ) : (
          <span className={cn("truncate text-xs text-muted-foreground", cell.kind === "not_listed" && "text-muted-foreground/70")}>{cell.label}</span>
        )}
        {price ? (
          <span
            className={cn(
              "ms-auto shrink-0 ps-2 text-xs tabular-nums",
              price.differs ? "font-medium text-foreground" : "text-muted-foreground",
              price.amount == null && "text-muted-foreground/70",
            )}
            data-differs={price.differs || undefined}
          >
            {price.label}
          </span>
        ) : null}
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

/** Table rows while the inventory loads, in the same grid and height as the real ones. */
function InventoryRowsSkeleton({ columns, marketplaces }: { columns: CSSProperties; marketplaces: number }) {
  return (
    <div role="status" aria-label="Loading listings" aria-busy="true">
      {Array.from({ length: 6 }, (_, index) => (
        <div key={index} aria-hidden="true" className="grid border-b border-border/40" style={{ ...columns, height: ROW_HEIGHT }}>
          <div className="flex min-w-0 items-center gap-3 px-4">
            <Skeleton className="size-9 flex-none rounded-md" />
            <Skeleton className={cn("h-4", index % 2 ? "w-1/2" : "w-2/3")} />
          </div>
          <div className="flex items-center justify-end px-3"><Skeleton className="h-4 w-10" /></div>
          {Array.from({ length: marketplaces }, (_, cell) => (
            <div key={cell} className="flex items-center px-3"><Skeleton shape="pill" className="h-5 w-16" /></div>
          ))}
        </div>
      ))}
    </div>
  );
}
