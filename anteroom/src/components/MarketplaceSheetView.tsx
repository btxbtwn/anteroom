import { useEffect, useMemo, useRef, useState, useSyncExternalStore, type CSSProperties } from "react";
import { skipToken, useQuery, useQueryClient } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { CheckIcon, ExternalLinkIcon, SquareArrowOutUpRightIcon } from "lucide-react";
import { listingEdits } from "../api/listingEdits";
import type { NativeMarketplace } from "../api/nativeMarketplaces";
import type { ListingDocument, ListingSheetCell, ListingSheetRow } from "../api/types";
import { useWorkspaceRuntime } from "../app/runtime";
import { usePhoneLayout } from "../lib/usePhoneLayout";
import { cn } from "../lib/utils";
import { BulkMarketplaceDialog } from "./BulkMarketplaceDialog";
import { marketplaceCell, type MarketplaceCell, type MarketplaceCellKind } from "./inventoryTable";
import { marketplaceName } from "./marketplaceNames";
import {
  buildScanSummary,
  canListFromSheet,
  formatSheetCellValue,
  gridColumnsForMarketplace,
  nextQuickEditListingId,
  sheetMissingFields,
  sheetPriceLabel,
  type SheetColumn,
} from "./marketplaceSheetConfig";
import {
  DesktopCellPopover,
  MarketplaceQuickEditSheet,
  useSheetListMutation,
  type ActiveDesktopCell,
} from "./MarketplaceSheetEditor";
import { ListingThumb } from "./sidebar/ListingBits";
import { displayCoverUrl } from "./sidebar/coverSrc";
import type { Listing } from "./sidebar/listingShelves";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Popover, PopoverPopup } from "./ui/popover";
import { Skeleton } from "./ui/skeleton";
import { Spinner } from "./ui/spinner";
import { useInventorySheet } from "./useInventorySheet";
import { EmptyRow, Panel } from "./WorkspacePage";

const DESKTOP_ROW_HEIGHT = 56;
const PHONE_ROW_HEIGHT = 72;
const NARROW_BREAKPOINT_PX = 580;

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

interface ConfirmListTarget {
  listing: Listing;
  revisionId: string;
  priceLabel: string | null;
  anchor: HTMLElement;
}

export function MarketplaceSheetView({
  marketplace,
  rows,
  allCount,
  needle,
  emptyLabel,
  loading,
  loadFailed,
  nameBySku,
  onOpenListing,
}: {
  marketplace: NativeMarketplace;
  rows: readonly Listing[];
  allCount: number;
  needle: string;
  emptyLabel: string;
  loading: boolean;
  loadFailed: boolean;
  nameBySku: boolean;
  onOpenListing: (id: string, tab?: string) => void;
}) {
  const queryClient = useQueryClient();
  const runtime = useWorkspaceRuntime();
  const isPhone = usePhoneLayout();
  const containerRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState(0);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const update = () => setContainerWidth(el.clientWidth);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const narrow = isPhone || (containerWidth > 0 && containerWidth < NARROW_BREAKPOINT_PX);
  const columns = useMemo(() => gridColumnsForMarketplace(marketplace), [marketplace]);
  const marketLabel = marketplaceName(marketplace);
  const canAct = !runtime.hosted || runtime.actionMarketplaces.has(marketplace);

  // Selection state (checkboxes always visible on desktop; revealed via Select button on phone).
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [phoneSelecting, setPhoneSelecting] = useState(false);
  const [bulkDialogIds, setBulkDialogIds] = useState<string[] | null>(null);

  // Active editor states (mounted outside virtualizer).
  const [activeCell, setActiveCell] = useState<ActiveDesktopCell | null>(null);
  const [quickEditId, setQuickEditId] = useState<string | null>(null);
  const [confirmList, setConfirmList] = useState<ConfirmListTarget | null>(null);
  const listMutation = useSheetListMutation(marketplace);
  const pendingConvId = listMutation.isPending ? (listMutation.variables?.convId ?? null) : null;
  const isListingConfirm = Boolean(confirmList && pendingConvId === confirmList.listing.id);

  const rowIds = useMemo(() => new Set(rows.map((row) => row.id)), [rows]);
  const activeSelectedIds = useMemo(
    () => [...selectedIds].filter((id) => rowIds.has(id)),
    [selectedIds, rowIds],
  );
  const allSelected = rows.length > 0 && activeSelectedIds.length === rows.length;

  const toggleRowSelection = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const toggleAllSelection = () => {
    if (allSelected) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(rows.map((row) => row.id)));
    }
  };

  // oxlint-disable-next-line react/incompatible-library -- React Compiler is not enabled in this app.
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    getItemKey: (index) => rows[index].id,
    estimateSize: () => (narrow ? PHONE_ROW_HEIGHT : DESKTOP_ROW_HEIGHT),
    overscan: 8,
    initialRect: { width: 1000, height: 800 },
  });
  const visible = virtualizer.getVirtualItems();
  const { rows: sheetRows } = useInventorySheet(
    marketplace,
    rows,
    visible[0]?.index ?? 0,
    visible[visible.length - 1]?.index ?? -1,
  );

  const quickEditListing = useMemo(
    () => (quickEditId ? rows.find((row) => row.id === quickEditId) ?? null : null),
    [quickEditId, rows],
  );
  const nextDraftId = useMemo(
    () => (quickEditId ? nextQuickEditListingId(rows, quickEditId, marketplace) : null),
    [quickEditId, rows, marketplace],
  );

  const gridTemplate = useMemo(
    () => `2.75rem minmax(13.5rem, 1.35fr) ${columns.map((col) => col.width).join(" ")} 8.5rem`,
    [columns],
  );
  const gridMinWidth = useMemo(
    () => `calc(2.75rem + 13.5rem + ${columns.map((col) => col.width).join(" + ")} + 8.5rem)`,
    [columns],
  );
  const gridStyle = { gridTemplateColumns: gridTemplate } as CSSProperties;
  const tableWidth = { minWidth: gridMinWidth } as CSSProperties;

  const openBulkList = () => {
    if (!activeSelectedIds.length) return;
    setBulkDialogIds(activeSelectedIds);
  };

  return (
    <div ref={containerRef} className="flex min-h-0 flex-1 flex-col gap-3">
      {/* Contextual action bar: selection bar on desktop when items are checked, or phone Select toggle */}
      {narrow ? (
        <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
          <span className="tabular-nums">
            {loading ? "Loading…" : `${rows.length} ${rows.length === 1 ? "item" : "items"}`}
          </span>
          {!loading && rows.length > 0 ? (
            phoneSelecting ? (
              <div className="flex items-center gap-2">
                <Button type="button" variant="ghost" size="sm" className="min-h-11" onClick={toggleAllSelection}>
                  {allSelected ? "Clear all" : "Select all"}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="min-h-11"
                  onClick={() => {
                    setPhoneSelecting(false);
                    setSelectedIds(new Set());
                  }}
                >
                  Done
                </Button>
              </div>
            ) : (
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="min-h-11"
                onClick={() => setPhoneSelecting(true)}
              >
                Select
              </Button>
            )
          ) : null}
        </div>
      ) : activeSelectedIds.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border/60 bg-card/40 px-3 py-2 text-xs">
          <span className="font-medium text-foreground tabular-nums">
            {activeSelectedIds.length} selected
          </span>
          {canAct ? (
            <Button type="button" size="xs" onClick={openBulkList}>
              List selected on {marketLabel}
            </Button>
          ) : null}
          <Button type="button" variant="ghost" size="xs" onClick={() => setSelectedIds(new Set())}>
            Clear
          </Button>
        </div>
      ) : null}

      <Panel divided={false} className="flex min-h-0 flex-1 flex-col bg-background">
        {narrow ? (
          <div
            ref={scrollRef}
            className="min-h-0 flex-1 overflow-y-auto overscroll-contain"
            role="list"
            aria-label={`${marketLabel} inventory`}
          >
            {loading && loadFailed ? (
              <EmptyRow>Could not load listings. Retrying…</EmptyRow>
            ) : loading ? (
              <PhoneScanSkeleton />
            ) : rows.length === 0 ? (
              <EmptyRow>{!allCount ? "No listings yet" : needle ? "No matching listings" : emptyLabel}</EmptyRow>
            ) : (
              <div className="relative" style={{ height: virtualizer.getTotalSize() }}>
                {visible.map((item) => {
                  const listing = rows[item.index];
                  const sheetRow = sheetRows[listing.id];
                  const title = listing.title || "Untitled";
                  const name = (nameBySku && listing.sku?.trim()) || title;
                  const summary = buildScanSummary(marketplace, sheetRow);
                  const status = marketplaceCell(listing, marketplace, sheetMissingFields(sheetRow));
                  const cachedDoc = queryClient.getQueryData<ListingDocument>(["listing", listing.id]);
                  const priceLabel = sheetPriceLabel(marketplace, sheetRow, cachedDoc?.listing, listing.price);
                  const checked = selectedIds.has(listing.id);

                  return (
                    <div
                      key={item.key}
                      role="listitem"
                      className={cn(
                        "absolute inset-x-0 flex items-center border-b border-border/40 px-3 transition-colors",
                        checked && "bg-accent",
                      )}
                      style={{ height: PHONE_ROW_HEIGHT, transform: `translateY(${item.start}px)` }}
                    >
                      {phoneSelecting ? (
                        <label className="me-1 flex size-11 shrink-0 cursor-pointer items-center justify-center">
                          <Checkbox
                            aria-label={`Select ${title}`}
                            checked={checked}
                            onCheckedChange={() => toggleRowSelection(listing.id)}
                          />
                        </label>
                      ) : null}
                      <button
                        type="button"
                        className="flex min-h-11 min-w-0 flex-1 cursor-pointer items-center gap-3 py-2 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        onClick={() => {
                          if (phoneSelecting) {
                            toggleRowSelection(listing.id);
                          } else {
                            setQuickEditId(listing.id);
                          }
                        }}
                      >
                        <ListingThumb
                          src={displayCoverUrl(listing.cover_photo_url || listing.vendoo_cover_url)}
                          className="size-10 shrink-0"
                        />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-baseline justify-between gap-2">
                            <span className="truncate text-sm font-medium text-foreground">{name}</span>
                            {priceLabel ? (
                              <span className="shrink-0 text-xs font-medium text-foreground tabular-nums">
                                {priceLabel}
                              </span>
                            ) : null}
                          </div>
                          <p className="mt-0.5 truncate text-xs">
                            {summary.alert ? (
                              <span
                                className={cn(
                                  "font-medium",
                                  summary.alertTone === "error"
                                    ? "text-destructive-foreground"
                                    : "text-warning-foreground",
                                )}
                              >
                                {summary.alert}
                              </span>
                            ) : null}
                            {summary.alert && summary.meta ? (
                              <span className="text-muted-foreground"> · </span>
                            ) : null}
                            {summary.meta ? (
                              <span className="text-muted-foreground">{summary.meta}</span>
                            ) : !summary.alert ? (
                              <span className="text-muted-foreground">
                                {sheetRow?.needs_load
                                  ? `Tap to load ${marketLabel} form`
                                  : `Tap to edit ${marketLabel} fields`}
                              </span>
                            ) : null}
                          </p>
                        </div>
                      </button>
                      {!phoneSelecting ? (
                        <div className="ms-2 flex shrink-0 items-center">
                          <SheetStatusAction
                            mode="phone"
                            listing={listing}
                            marketplace={marketplace}
                            status={status}
                            sheetRow={sheetRow}
                            canAct={canAct}
                            pending={pendingConvId === listing.id}
                            onListClick={() => setQuickEditId(listing.id)}
                            onSecondaryClick={() => setQuickEditId(listing.id)}
                          />
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        ) : (
          <div
            ref={scrollRef}
            className="min-h-0 flex-1 overflow-auto overscroll-contain"
            role="table"
            aria-label={`${marketLabel} inventory spreadsheet`}
            aria-rowcount={rows.length + 1}
            onScroll={() => {
              if (activeCell) setActiveCell(null);
              if (confirmList) setConfirmList(null);
            }}
          >
            <div style={tableWidth}>
              <div
                role="row"
                className="sticky top-0 z-20 grid border-b border-border/60 bg-background text-xs font-medium text-muted-foreground"
                style={gridStyle}
              >
                <div role="columnheader" className="sticky left-0 z-20 flex items-center justify-center bg-background">
                  <Checkbox
                    aria-label="Select all listings"
                    checked={allSelected}
                    disabled={loading || rows.length === 0}
                    onCheckedChange={toggleAllSelection}
                  />
                </div>
                <div role="columnheader" className="sticky left-11 z-20 bg-background px-3 py-2.5">
                  Item
                </div>
                {columns.map((col) => (
                  <div
                    key={col.key}
                    role="columnheader"
                    className={cn("truncate px-3 py-2.5", col.align === "right" && "text-right")}
                  >
                    {col.label}
                  </div>
                ))}
                <div role="columnheader" className="sticky right-0 z-20 border-l border-border/40 bg-background px-3 py-2.5 text-right">
                  {marketLabel}
                </div>
              </div>

              {loading && loadFailed ? (
                <EmptyRow>Could not load listings. Retrying…</EmptyRow>
              ) : loading ? (
                <DesktopSheetSkeleton gridStyle={gridStyle} columnCount={columns.length} />
              ) : rows.length === 0 ? (
                <EmptyRow>{!allCount ? "No listings yet" : needle ? "No matching listings" : emptyLabel}</EmptyRow>
              ) : (
                <div className="relative" style={{ height: virtualizer.getTotalSize() }}>
                  {visible.map((item) => {
                    const listing = rows[item.index];
                    const sheetRow = sheetRows[listing.id];
                    const title = listing.title || "Untitled";
                    const name = (nameBySku && listing.sku?.trim()) || title;
                    const checked = selectedIds.has(listing.id);
                    const status = marketplaceCell(listing, marketplace, sheetMissingFields(sheetRow));

                    return (
                      <div
                        key={item.key}
                        role="row"
                        aria-rowindex={item.index + 2}
                        className={cn(
                          "group/row absolute inset-x-0 grid border-b border-border/40",
                          checked && "bg-accent",
                        )}
                        style={{ ...gridStyle, height: DESKTOP_ROW_HEIGHT, transform: `translateY(${item.start}px)` }}
                      >
                        <div role="cell" className="sticky left-0 z-10 bg-background">
                          <div
                            className={cn(
                              "flex size-full items-center justify-center group-hover/row:bg-accent",
                              checked && "bg-accent",
                            )}
                          >
                            <Checkbox
                              aria-label={`Select ${title}`}
                              checked={checked}
                              onCheckedChange={() => toggleRowSelection(listing.id)}
                            />
                          </div>
                        </div>
                        <div role="rowheader" className="sticky left-11 z-10 min-w-0 bg-background">
                          <button
                            type="button"
                            className={cn(
                              "flex size-full min-w-0 cursor-pointer items-center gap-3 px-3 text-left outline-none group-hover/row:bg-accent focus-visible:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
                              checked && "bg-accent",
                            )}
                            title={name === title ? title : `${name} · ${title}`}
                            onClick={() => onOpenListing(listing.id, marketplace)}
                          >
                            <ListingThumb
                              src={displayCoverUrl(listing.cover_photo_url || listing.vendoo_cover_url)}
                              className="size-9 shrink-0"
                            />
                            <span className="flex min-w-0 flex-col gap-0.5">
                              <span
                                className={cn(
                                  "text-sm font-medium text-foreground",
                                  listing.sku && !nameBySku ? "truncate" : "line-clamp-2 leading-tight",
                                )}
                              >
                                {name}
                              </span>
                              {listing.sku && !nameBySku ? (
                                <span className="truncate font-mono text-2xs text-muted-foreground">{listing.sku}</span>
                              ) : null}
                            </span>
                          </button>
                        </div>

                        {columns.map((col) => {
                          const cell = sheetRow?.cells?.[col.key];
                          return (
                            <SheetGridCell
                              key={col.key}
                              column={col}
                              cell={cell}
                              loading={!sheetRow}
                              needsLoad={Boolean(sheetRow?.needs_load)}
                              active={activeCell?.listing.id === listing.id && activeCell.column.key === col.key}
                              marketplace={marketplace}
                              onClick={(anchor) => {
                                if (col.kind === "complex" || cell?.editable === false) {
                                  onOpenListing(listing.id, marketplace);
                                } else {
                                  setActiveCell({ listing, column: col, anchor });
                                }
                              }}
                            />
                          );
                        })}

                        <div
                          role="cell"
                          className="sticky right-0 z-10 border-l border-border/40 bg-background"
                        >
                          <div
                            className={cn(
                              "flex size-full items-center justify-end gap-1.5 px-3 group-hover/row:bg-accent",
                              checked && "bg-accent",
                            )}
                          >
                            <SheetStatusAction
                              mode="desktop"
                              listing={listing}
                              marketplace={marketplace}
                              status={status}
                              sheetRow={sheetRow}
                              canAct={canAct}
                              pending={pendingConvId === listing.id}
                              onListClick={(anchor, revisionId, priceLabel) => {
                                setConfirmList({
                                  listing,
                                  revisionId,
                                  priceLabel,
                                  anchor,
                                });
                              }}
                              onSecondaryClick={() => onOpenListing(listing.id, marketplace)}
                            />
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        )}
      </Panel>

      {/* Phone sticky batch action bar when selecting */}
      {narrow && phoneSelecting && activeSelectedIds.length > 0 ? (
        <div className="flex items-center justify-between gap-2 rounded-xl border border-border/60 bg-background px-3 py-2">
          <span className="text-xs font-medium text-foreground tabular-nums">
            {activeSelectedIds.length} selected
          </span>
          {canAct ? (
            <Button type="button" size="sm" className="min-h-11" onClick={openBulkList}>
              List on {marketLabel}
            </Button>
          ) : null}
        </div>
      ) : null}

      {/* Single desktop inline cell editor popover mounted outside the virtualizer */}
      {!narrow ? (
        <DesktopCellPopover
          active={activeCell}
          marketplace={marketplace}
          sheetRow={activeCell ? sheetRows[activeCell.listing.id] : undefined}
          onClose={() => setActiveCell(null)}
          onOpenListing={onOpenListing}
        />
      ) : null}

      {/* Single desktop 2-step List confirmation popover naming marketplace and per-marketplace price */}
      <Popover
        open={Boolean(confirmList)}
        onOpenChange={(open) => {
          if (!open) setConfirmList(null);
        }}
      >
        <PopoverPopup
          anchor={confirmList?.anchor ?? null}
          align="end"
          side="bottom"
          sideOffset={6}
          width="sm"
          padding="compact"
          touchBackdrop
        >
          {confirmList ? (
            <DesktopConfirmListBody
              confirmList={confirmList}
              liveListing={rows.find((row) => row.id === confirmList.listing.id)}
              canAct={canAct}
              sheetRow={sheetRows[confirmList.listing.id]}
              marketplace={marketplace}
              marketLabel={marketLabel}
              isListing={isListingConfirm}
              onCancel={() => setConfirmList(null)}
              onConfirm={(revisionId) => {
                const submittedId = confirmList.listing.id;
                listMutation.mutate(
                  { convId: submittedId, revisionId },
                  {
                    onSuccess: () =>
                      setConfirmList((cur) =>
                        cur?.listing.id === submittedId && cur?.revisionId === revisionId ? null : cur,
                      ),
                  },
                );
              }}
            />
          ) : null}
        </PopoverPopup>
      </Popover>

      {/* Phone Quick Edit bottom sheet */}
      {narrow ? (
        <MarketplaceQuickEditSheet
          listing={quickEditListing}
          marketplace={marketplace}
          sheetRow={quickEditListing ? sheetRows[quickEditListing.id] : undefined}
          nextDraftId={nextDraftId}
          onClose={() => setQuickEditId(null)}
          onSelectListing={(id) => setQuickEditId(id)}
          onOpenListing={onOpenListing}
        />
      ) : null}

      {/* Bulk marketplace dialog pre-selected with this marketplace tab */}
      {bulkDialogIds ? (
        <BulkMarketplaceDialog
          conversationIds={bulkDialogIds}
          initialAction="list"
          initialMarketplaces={[marketplace]}
          onClose={() => {
            setBulkDialogIds(null);
            setSelectedIds(new Set());
            setPhoneSelecting(false);
          }}
        />
      ) : null}
    </div>
  );
}

/** Shared status / List action cell for desktop and phone rows, wired to live listingEdits state. */
function SheetStatusAction({
  mode,
  listing,
  marketplace,
  status,
  sheetRow,
  canAct,
  pending,
  onListClick,
  onSecondaryClick,
}: {
  mode: "desktop" | "phone";
  listing: Listing;
  marketplace: NativeMarketplace;
  status: MarketplaceCell;
  sheetRow: ListingSheetRow | undefined;
  canAct: boolean;
  pending: boolean;
  onListClick: (anchor: HTMLElement, revisionId: string, priceLabel: string | null) => void;
  onSecondaryClick: () => void;
}) {
  const queryClient = useQueryClient();
  const edits = useMemo(() => listingEdits(queryClient, listing.id), [queryClient, listing.id]);
  const editState = useSyncExternalStore(edits.subscribe, edits.getSnapshot, edits.getSnapshot);
  const cachedDoc = queryClient.getQueryData<ListingDocument>(["listing", listing.id]);
  const effectiveRevision = cachedDoc?.current_revision_id ?? sheetRow?.revision_id ?? null;
  const hasUnsavedDraft = Boolean(editState.draft);
  const priceLabel = sheetPriceLabel(
    marketplace,
    sheetRow,
    editState.draft ?? cachedDoc?.listing,
    listing.price,
    cachedDoc?.current_revision_id ?? null,
  );
  const canList = canListFromSheet(listing, status, sheetRow, canAct, effectiveRevision);
  const variant = status.inactive ? "outline" : BADGE_VARIANT[status.kind];
  const marketLabel = marketplaceName(marketplace);
  const title = listing.title || "Untitled";

  if (status.kind === "listed" || status.kind === "sold") {
    if (mode === "phone") {
      return <Badge variant={variant ?? "outline"}>{status.label}</Badge>;
    }
    return (
      <>
        <Badge variant={variant ?? "outline"} className="truncate">{status.label}</Badge>
        {status.url ? (
          <a
            href={status.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground outline-none hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
            aria-label={`View ${title} on ${marketLabel}`}
            title={`View on ${marketLabel}`}
          >
            <ExternalLinkIcon className="size-3.5" />
          </a>
        ) : null}
      </>
    );
  }

  if (status.kind === "working") {
    return (
      <Badge variant="outline">
        <Spinner size="sm" tone="muted" /> {status.label}
      </Badge>
    );
  }

  if (canList && effectiveRevision) {
    return (
      <Button
        type="button"
        size={mode === "phone" ? "sm" : "xs"}
        className={mode === "phone" ? "min-h-11 px-3" : undefined}
        disabled={pending || editState.saving || hasUnsavedDraft}
        onClick={(event) => onListClick(event.currentTarget, effectiveRevision, priceLabel)}
      >
        {editState.saving ? "Saving…" : "List"}
      </Button>
    );
  }

  return (
    <button
      type="button"
      className={cn(
        "inline-flex cursor-pointer items-center outline-none focus-visible:ring-2 focus-visible:ring-ring",
        mode === "phone" ? "min-h-11 px-1" : "h-8 rounded-md px-1.5 hover:bg-accent",
      )}
      title={status.detail || `Open ${marketLabel} form`}
      aria-label={mode === "phone" ? `Edit ${title} for ${marketLabel}` : undefined}
      onClick={onSecondaryClick}
    >
      {variant ? (
        <Badge variant={variant} className="truncate">{status.label}</Badge>
      ) : (
        <span className="truncate text-xs text-muted-foreground">{status.label}</span>
      )}
    </button>
  );
}

function DesktopConfirmListBody({
  confirmList,
  liveListing,
  canAct,
  sheetRow,
  marketplace,
  marketLabel,
  isListing,
  onCancel,
  onConfirm,
}: {
  confirmList: ConfirmListTarget;
  liveListing: Listing | undefined;
  canAct: boolean;
  sheetRow: ListingSheetRow | undefined;
  marketplace: NativeMarketplace;
  marketLabel: string;
  isListing: boolean;
  onCancel: () => void;
  onConfirm: (revisionId: string) => void;
}) {
  const queryClient = useQueryClient();
  const edits = useMemo(() => listingEdits(queryClient, confirmList.listing.id), [queryClient, confirmList.listing.id]);
  const editState = useSyncExternalStore(edits.subscribe, edits.getSnapshot, edits.getSnapshot);
  const { data: cachedDoc } = useQuery<ListingDocument>({
    queryKey: ["listing", confirmList.listing.id],
    queryFn: skipToken,
  });
  const liveRevision = cachedDoc?.current_revision_id ?? sheetRow?.revision_id ?? null;
  const liveStatus = liveListing ? marketplaceCell(liveListing, marketplace, sheetMissingFields(sheetRow)) : null;
  const canList = canListFromSheet(liveListing, liveStatus, sheetRow, canAct, liveRevision);
  const livePrice = sheetPriceLabel(
    marketplace,
    sheetRow,
    editState.draft ?? cachedDoc?.listing,
    liveListing?.price,
    cachedDoc?.current_revision_id ?? null,
  );
  const hasUnsavedDraft = Boolean(editState.draft);
  const snapshotDrifted =
    liveRevision !== confirmList.revisionId
    || !canList
    || livePrice !== confirmList.priceLabel
    || hasUnsavedDraft;
  const priceLabel = confirmList.priceLabel;

  return (
    <div className="flex flex-col gap-2.5 px-3 py-2">
      <p className="text-xs text-foreground">
        List <span className="font-medium">{confirmList.listing.title || "this item"}</span> on{" "}
        <span className="font-medium">{marketLabel}</span>
        {priceLabel ? (
          <> for <span className="font-medium tabular-nums">{priceLabel}</span></>
        ) : null}?
      </p>
      {snapshotDrifted && !isListing && !editState.saving ? (
        <p className="text-xs text-warning-foreground">
          This listing changed. Cancel and click List again to review the latest details.
        </p>
      ) : null}
      <div className="flex items-center justify-end gap-1.5">
        <Button
          type="button"
          variant="outline"
          size="xs"
          disabled={isListing}
          onClick={onCancel}
        >
          Cancel
        </Button>
        <Button
          type="button"
          size="xs"
          disabled={isListing || editState.saving || snapshotDrifted}
          onClick={() => onConfirm(confirmList.revisionId)}
        >
          {isListing || editState.saving ? <Spinner size="sm" /> : <CheckIcon className="size-3.5" />}
          {editState.saving ? "Saving…" : `List on ${marketLabel}`}
        </Button>
      </div>
    </div>
  );
}

function SheetGridCell({
  column,
  cell,
  loading,
  needsLoad,
  active,
  marketplace,
  onClick,
}: {
  column: SheetColumn;
  cell: ListingSheetCell | undefined;
  loading: boolean;
  needsLoad: boolean;
  active: boolean;
  marketplace: NativeMarketplace;
  onClick: (anchor: HTMLElement) => void;
}) {
  if (loading) {
    return (
      <div role="cell" className="flex items-center px-3 group-hover/row:bg-accent">
        <Skeleton className="h-4 w-16" />
      </div>
    );
  }
  const text = formatSheetCellValue(column, cell);
  const isComplex = column.kind === "complex" || cell?.editable === false;
  const marketLabel = marketplaceName(marketplace);
  const fullTooltip = [
    cell?.error,
    cell?.value && cell.value !== text ? cell.value : null,
    isComplex
      ? `Open ${marketLabel} form to edit ${column.label.toLowerCase()}`
      : needsLoad
        ? `Click to load ${marketLabel} form and edit ${column.label.toLowerCase()}`
        : `Edit ${column.label.toLowerCase()}`,
  ].filter(Boolean).join(" · ");

  return (
    <div role="cell" className="flex min-w-0 items-center px-1.5 group-hover/row:bg-accent">
      <button
        type="button"
        className={cn(
          "flex h-9 w-full min-w-0 cursor-pointer items-center gap-1.5 rounded-md px-2 text-left text-xs outline-none transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring",
          column.align === "right" && "justify-end tabular-nums",
          active && "bg-accent ring-2 ring-ring",
          cell?.rejected && "text-destructive-foreground",
          !cell?.rejected && cell?.missing && "text-warning-foreground",
        )}
        title={fullTooltip}
        aria-label={`${column.label}: ${text || "empty"}${cell?.rejected ? " (rejected)" : cell?.missing ? " (missing)" : ""}`}
        onClick={(event) => onClick(event.currentTarget)}
      >
        {cell?.rejected ? (
          <span className="size-1.5 shrink-0 rounded-full bg-destructive" aria-hidden="true" />
        ) : cell?.missing ? (
          <span className="size-1.5 shrink-0 rounded-full bg-warning" aria-hidden="true" />
        ) : null}
        <span
          className={cn(
            "truncate",
            text ? (cell?.rejected ? "text-destructive-foreground" : "text-foreground") : "text-muted-foreground/70",
            column.key === "price" && "font-medium tabular-nums",
          )}
        >
          {text || (cell?.missing ? `Add ${column.label.toLowerCase()}` : "—")}
        </span>
        {isComplex ? (
          <SquareArrowOutUpRightIcon className="ms-auto size-3 shrink-0 text-muted-foreground/60" aria-hidden="true" />
        ) : null}
      </button>
    </div>
  );
}

function DesktopSheetSkeleton({ gridStyle, columnCount }: { gridStyle: CSSProperties; columnCount: number }) {
  return (
    <div role="status" aria-label="Loading listings" aria-busy="true">
      {Array.from({ length: 6 }, (_, index) => (
        <div key={index} aria-hidden="true" className="grid border-b border-border/40" style={{ ...gridStyle, height: DESKTOP_ROW_HEIGHT }}>
          <div className="flex items-center justify-center"><Skeleton className="size-4" /></div>
          <div className="flex min-w-0 items-center gap-3 px-3">
            <Skeleton className="size-9 flex-none rounded-md" />
            <Skeleton className={cn("h-4", index % 2 ? "w-1/2" : "w-2/3")} />
          </div>
          {Array.from({ length: columnCount }, (_, cell) => (
            <div key={cell} className="flex items-center px-3"><Skeleton className="h-4 w-16" /></div>
          ))}
          <div className="flex items-center justify-end px-3"><Skeleton className="h-6 w-14" /></div>
        </div>
      ))}
    </div>
  );
}

function PhoneScanSkeleton() {
  return (
    <div role="status" aria-label="Loading listings" aria-busy="true">
      {Array.from({ length: 6 }, (_, index) => (
        <div key={index} aria-hidden="true" className="flex items-center gap-3 border-b border-border/40 px-3" style={{ height: PHONE_ROW_HEIGHT }}>
          <Skeleton className="size-10 flex-none rounded-md" />
          <div className="flex min-w-0 flex-1 flex-col gap-1.5">
            <Skeleton className={cn("h-4", index % 2 ? "w-3/5" : "w-3/4")} />
            <Skeleton className="h-3 w-1/2" />
          </div>
          <Skeleton className="h-7 w-14" />
        </div>
      ))}
    </div>
  );
}
