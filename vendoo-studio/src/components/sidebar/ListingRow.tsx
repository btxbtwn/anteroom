import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { MarketplaceLogo } from "../MarketplaceLogo";
import { marketplacesNeedingRelist, relistStage } from "../relistStatus";
import { marketplaceName } from "../marketplaceNames";
import { PencilIcon, XIcon } from "lucide-react";
import { cn } from "../../lib/utils";
import { confirmDialog } from "../../ui/confirmDialog";
import { ListingStatusLabel, ListingTag, ListingThumb, SelectCheckbox } from "./ListingBits";
import { listedFromImport, liveStatusClass, marketplaceStatusesFromDraft, statusHint } from "./listingStatus";
import { compactRelativeTime, type Listing } from "./listingShelves";

const HOVER_STATUS_DELAY_MS = 280;
const LISTED_LIVE_STATUSES = new Set(["LISTED", "SOLD"]);

export function ListingRow({
  listing,
  selected,
  settled,
  jobId,
  statusDraft,
  visibleMarketplaces,
  nameBySku = false,
  selecting = false,
  checked = false,
  canSelect = false,
  selectLocked = false,
  onToggleSelect,
  onSelect,
  onDelete,
}: {
  listing: Listing;
  selected: boolean;
  settled: boolean;
  jobId?: string;
  statusDraft?: Record<string, unknown>;
  visibleMarketplaces?: Set<string>;
  nameBySku?: boolean;
  selecting?: boolean;
  checked?: boolean;
  canSelect?: boolean;
  selectLocked?: boolean;
  onToggleSelect?: (id: string, shift: boolean) => void;
  onSelect: (id: string) => void;
  onDelete: (id: string, title: string) => void;
}) {
  const title = listing.title || "Untitled";
  // Named by SKU, a listing without one still shows its title.
  const name = (nameBySku && listing.sku?.trim()) || title;
  // A bulk-imported listing has no local photos yet; Vendoo's own image stands in.
  const coverUrl = listing.cover_photo_url || listing.vendoo_cover_url || null;
  const status = String(listing.status || "draft");
  const settledAt = settled ? compactRelativeTime(listing.settled_at || listing.updated_at) : "";
  const queryClient = useQueryClient();
  const itemRef = useRef<HTMLDivElement>(null);
  const renameInputRef = useRef<HTMLInputElement>(null);
  const skipRenameBlur = useRef(false);
  const hoverTimer = useRef<number | null>(null);
  const [hoverOpen, setHoverOpen] = useState(false);
  const [popupPos, setPopupPos] = useState<{ top: number; left: number } | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [draftTitle, setDraftTitle] = useState(title);

  const renameListing = useMutation({
    mutationFn: (nextTitle: string) => api.conversations.update(listing.id, { title: nextTitle }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      setRenaming(false);
    },
  });

  // Observe Fields-panel cache without fetching.
  const cachedDraftQuery = useQuery({
    queryKey: ["vendoo-item", jobId || ""],
    queryFn: () => api.jobs.vendooItem(jobId || ""),
    enabled: false,
  });

  const peekQuery = useQuery({
    queryKey: ["vendoo-item-peek", jobId],
    queryFn: async () => {
      const data = await api.jobs.vendooItem(jobId || "", { cacheOnly: true });
      if (data?.ok && (data.item || data.form || data.statuses)) {
        queryClient.setQueryData(["vendoo-item", jobId], data);
      }
      return data;
    },
    // Not gated on the Fields-panel cache: that observer is disabled, so a pull's
    // invalidate never refetches it. Re-peeking after invalidation is what picks
    // up the refreshed server draft.
    enabled: Boolean(hoverOpen && jobId),
    staleTime: Infinity,
    retry: 0,
  });

  const fullDraft = (
    peekQuery.data?.ok && peekQuery.dataUpdatedAt >= cachedDraftQuery.dataUpdatedAt
      ? peekQuery.data
      : cachedDraftQuery.data || (peekQuery.data?.ok ? peekQuery.data : undefined)
  ) as Record<string, unknown> | undefined;
  const draft = fullDraft || statusDraft;
  const marketplaceStatuses = useMemo(
    () => marketplaceStatusesFromDraft(draft, visibleMarketplaces),
    [draft, visibleMarketplaces],
  );
  // A cached draft that names any marketplace is the live word and wins, even
  // when it says the listing is live nowhere. Only a draft with nothing to say
  // hands over to what the import recorded.
  const importedRows = useMemo(
    () => listedFromImport(listing, visibleMarketplaces),
    [listing, visibleMarketplaces],
  );
  const draftRows = marketplaceStatuses.filter((row) => row.id !== "general");
  const listedMarketplaces = draftRows.length
    ? draftRows.filter((row) => LISTED_LIVE_STATUSES.has(row.status))
    : importedRows;
  const popupStatuses = marketplaceStatuses.length ? marketplaceStatuses : importedRows;
  // "active" alone would read as done. These listings are live on copy older
  // than Studio's last write, and only a delist + relist in Vendoo fixes that.
  const relistMarketplaces = useMemo(() => marketplacesNeedingRelist(listing), [listing]);
  const relistStageNow = useMemo(() => relistStage(listing), [listing]);
  const loadingStatus = Boolean(
    hoverOpen && jobId && !draft && peekQuery.isFetching,
  );
  const missingStatus = Boolean(
    hoverOpen && jobId && !draft && peekQuery.isFetched && !peekQuery.isFetching,
  );

  const clearHoverTimer = () => {
    if (hoverTimer.current != null) {
      window.clearTimeout(hoverTimer.current);
      hoverTimer.current = null;
    }
  };

  const updatePopupPos = useCallback(() => {
    const el = itemRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    setPopupPos({
      top: Math.max(8, Math.min(rect.top, window.innerHeight - 220)),
      left: Math.min(rect.right + 8, window.innerWidth - 220),
    });
  }, []);

  const openHover = () => {
    if (!jobId || renaming) return;
    clearHoverTimer();
    hoverTimer.current = window.setTimeout(() => {
      updatePopupPos();
      setHoverOpen(true);
    }, HOVER_STATUS_DELAY_MS);
  };

  const closeHover = () => {
    clearHoverTimer();
    setHoverOpen(false);
  };

  const startRename = () => {
    closeHover();
    skipRenameBlur.current = false;
    setDraftTitle(title);
    setRenaming(true);
  };

  const cancelRename = () => {
    skipRenameBlur.current = true;
    setDraftTitle(title);
    setRenaming(false);
  };

  const commitRename = () => {
    if (skipRenameBlur.current) {
      skipRenameBlur.current = false;
      return;
    }
    const next = draftTitle.trim();
    if (!next || next === title) {
      setRenaming(false);
      return;
    }
    renameListing.mutate(next);
  };

  useEffect(() => () => clearHoverTimer(), []);

  useEffect(() => {
    if (!renaming) return;
    const input = renameInputRef.current;
    if (!input) return;
    input.focus();
    input.select();
  }, [renaming]);

  useEffect(() => {
    if (!hoverOpen) return;
    const onScroll = () => updatePopupPos();
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
    };
  }, [hoverOpen, updatePopupPos]);

  const actionClass =
    "inline-flex size-5.5 cursor-pointer items-center justify-center rounded-md text-sidebar-muted-foreground outline-none hover:bg-sidebar-row-hover hover:text-sidebar-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-40";

  return (
    <div
      className="group/row relative mb-px"
      ref={itemRef}
      onMouseEnter={openHover}
      onMouseLeave={closeHover}
      onFocus={openHover}
      onBlur={(event) => {
        if (!itemRef.current?.contains(event.relatedTarget as Node | null)) closeHover();
      }}
    >
      {/* Row look follows T3 Code's sidebar thread rows (MIT, (c) 2026 T3 Tools Inc.). */}
      <div
        data-active={selected}
        className={cn(
          "flex w-full min-w-0 cursor-pointer items-center gap-2.5 rounded-[var(--control-radius)] px-2.5 py-1.5 text-left text-sidebar-foreground/90 transition-colors hover:bg-sidebar-row-hover",
          "data-[active=true]:bg-sidebar-row-selected data-[active=true]:text-sidebar-foreground",
          settled && "text-sidebar-muted-foreground group-hover/row:text-sidebar-foreground group-focus-within/row:text-sidebar-foreground data-[active=true]:text-sidebar-foreground",
        )}
        onClick={() => {
          if (!renaming) onSelect(listing.id);
        }}
      >
        {selecting ? (
          <SelectCheckbox
            checked={checked}
            label={`${checked ? "Deselect" : "Select"} ${title}`}
            title={canSelect ? "Include in regenerate" : "Add photos before regenerating"}
            disabled={!canSelect || selectLocked}
            onToggle={(shift) => onToggleSelect?.(listing.id, shift)}
          />
        ) : null}
        <ListingThumb
          src={coverUrl}
          dimmed={settled && !selected}
          className={settled ? "group-hover/row:[&_img]:opacity-100 group-focus-within/row:[&_img]:opacity-100" : undefined}
        />
        <div className="min-w-0 flex-1">
          {renaming ? (
            <input
              ref={renameInputRef}
              className="w-full bg-transparent p-0 text-sm font-medium leading-snug text-sidebar-foreground outline-none"
              value={draftTitle}
              aria-label={`Rename ${title}`}
              disabled={renameListing.isPending}
              onChange={(e) => setDraftTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  e.currentTarget.blur();
                } else if (e.key === "Escape") {
                  e.preventDefault();
                  cancelRename();
                }
              }}
              onBlur={commitRename}
            />
          ) : (
            <button
              type="button"
              className={cn(
                "block w-full cursor-pointer truncate text-left text-sm font-medium leading-snug text-inherit outline-none",
                // Hover actions float over the title; fade it under them.
                !selecting &&
                  "group-hover/row:[mask-image:linear-gradient(to_right,#000_calc(100%-76px),transparent_calc(100%-56px))] group-focus-within/row:[mask-image:linear-gradient(to_right,#000_calc(100%-76px),transparent_calc(100%-56px))]",
              )}
              title={name === title ? title : `${name} · ${title}`}
              onClick={(e) => {
                e.stopPropagation();
                onSelect(listing.id);
              }}
              onDoubleClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                startRename();
              }}
            >
              {name}
            </button>
          )}
          <div className="mt-0.5 flex items-center gap-2">
            <ListingStatusLabel status={status} title={statusHint(status)} />
            {listing.unsent_edits ? (
              <ListingTag
                tone="info"
                title="Edited in Studio since Vendoo last had this listing. Press Update Vendoo to write it onto the Vendoo form."
              >
                unsent
              </ListingTag>
            ) : null}
            {relistMarketplaces.length ? (
              <ListingTag
                tone="warning"
                title={relistStageNow === "list"
                  ? "Delisted in Vendoo and not listed again yet, so it is live nowhere."
                    + ` List it again to finish: ${relistMarketplaces.map(marketplaceName).join(", ")}.`
                  : `Updated in Studio after ${relistMarketplaces.map(marketplaceName).join(", ")} went live.`
                    + " Delist and relist in Vendoo to publish the new version."}
              >
                relist
              </ListingTag>
            ) : null}
            {settledAt ? <span className="text-2xs font-medium text-sidebar-muted-foreground">{settledAt}</span> : null}
          </div>
          {listedMarketplaces.length ? (
            <div
              className="mt-1 flex flex-wrap gap-0.75"
              aria-label={`Listed on ${listedMarketplaces.map((row) => row.label).join(", ")}`}
            >
              {listedMarketplaces.map((row) => (
                <span key={row.id} className={cn("inline-flex", row.status === "SOLD" && "opacity-55")}>
                  <MarketplaceLogo id={row.id} label={`${row.label} · ${row.status.toLowerCase()}`} size={14} />
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </div>
      {!renaming && !selecting && (
        <div className="pointer-events-none absolute top-1.5 right-1.5 flex items-center opacity-0 transition-opacity group-hover/row:pointer-events-auto group-hover/row:opacity-100 group-focus-within/row:pointer-events-auto group-focus-within/row:opacity-100 max-[900px]:pointer-events-auto max-[900px]:opacity-100">
          <button
            type="button"
            className={actionClass}
            title="Rename listing"
            aria-label={`Rename ${title}`}
            disabled={renameListing.isPending}
            onClick={(e) => {
              e.stopPropagation();
              startRename();
            }}
          >
            <PencilIcon className="size-3" />
          </button>
          <button
            type="button"
            className={actionClass}
            title="Delete"
            aria-label={`Delete ${title}`}
            onClick={async (e) => {
              e.stopPropagation();
              const ok = await confirmDialog(`Delete "${title}"?`, { variant: "destructive", confirmLabel: "Delete" });
              if (ok) onDelete(listing.id, title);
            }}
          >
            <XIcon className="size-3" />
          </button>
        </div>
      )}
      {hoverOpen && jobId && popupPos ? (
        <div
          className="dropdown-glass pointer-events-none fixed z-80 w-55 rounded-lg p-2 text-popover-foreground shadow-lg/10"
          style={{ top: popupPos.top, left: popupPos.left }}
          role="tooltip"
        >
          <div className="mb-1.5 px-0.5 text-3xs font-semibold tracking-wider text-muted-foreground uppercase">Vendoo status</div>
          {loadingStatus ? (
            <div className="px-0.5 py-1 text-2xs text-muted-foreground">Reading…</div>
          ) : popupStatuses.length ? (
            <ul className="m-0 flex list-none flex-col gap-1 p-0">
              {popupStatuses.map((row) => (
                <li key={row.id} className="flex min-h-5.5 items-center gap-2 px-0.5">
                  <MarketplaceLogo id={row.id} label={row.label} size={16} />
                  <span className="min-w-0 flex-1 truncate text-xs text-foreground/90">{row.label}</span>
                  <span className={`pr-live-status ${liveStatusClass(row.status)}`}>{row.status}</span>
                </li>
              ))}
            </ul>
          ) : (
            <div className="px-0.5 py-1 text-2xs leading-snug text-muted-foreground">
              {missingStatus
                ? "Pull from Vendoo to load live status."
                : "No marketplace status yet."}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
