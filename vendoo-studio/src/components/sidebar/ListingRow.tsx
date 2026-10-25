import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { MarketplaceLogo } from "../MarketplaceLogo";
import { marketplacesNeedingRelist, relistStage } from "../relistStatus";
import { marketplaceName } from "../marketplaceNames";
import { EllipsisIcon, PencilIcon, Trash2Icon, XIcon } from "lucide-react";
import { Menu, MenuItem, MenuPopup, MenuTrigger } from "../ui/menu";
import { cn } from "../../lib/utils";
import { confirmDialog } from "../../ui/confirmDialog";
import { ListingStatusLabel, ListingTag, ListingThumb, SelectCheckbox } from "./ListingBits";
import { mergeStatusDrafts, statusIsListed } from "../fillLog/marketplaces";
import { inventoryStatus, listedFromImport, liveStatusClass, marketplaceStatusesFromDraft, statusHint, withDirectListings } from "./listingStatus";
import { compactRelativeTime, type Listing } from "./listingShelves";

const HOVER_STATUS_DELAY_MS = 280;

function canHover() {
  return typeof window === "undefined" || !window.matchMedia || window.matchMedia("(hover: hover)").matches;
}

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
  const status = inventoryStatus(listing);
  const statusTitle = status === "active" && String(listing.status || "draft") !== "active"
    ? "Listed from Anteroom. Vendoo still has this item as a draft."
    : statusHint(status);
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
        const existing = queryClient.getQueryData(["vendoo-item", jobId]) as { item?: { generalDetails?: unknown } } | undefined;
        // The hover peek is the slim status cache. Writing it over the fields
        // panel's full item drops the listing records the status is read from.
        if (!existing?.item?.generalDetails) {
          queryClient.setQueryData(["vendoo-item", jobId], data);
        }
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

  const draft = useMemo(
    () => mergeStatusDrafts(cachedDraftQuery.data, peekQuery.data, statusDraft),
    [cachedDraftQuery.data, peekQuery.data, statusDraft],
  );
  const marketplaceStatuses = useMemo(
    () => marketplaceStatusesFromDraft(draft, visibleMarketplaces),
    [draft, visibleMarketplaces],
  );
  // A cached Vendoo draft that names any marketplace is the live word and wins,
  // even when it says the listing is live nowhere. Only a draft with nothing to
  // say hands over to what the import recorded. Anteroom's own publish still
  // shows, because Vendoo does not know about that listing.
  const importedRows = useMemo(
    () => listedFromImport(listing, visibleMarketplaces),
    [listing, visibleMarketplaces],
  );
  const popupStatuses = useMemo(
    () => withDirectListings(
      marketplaceStatuses.length ? marketplaceStatuses : importedRows,
      listing.direct_marketplaces,
      visibleMarketplaces,
    ),
    [marketplaceStatuses, importedRows, listing.direct_marketplaces, visibleMarketplaces],
  );
  const listedMarketplaces = popupStatuses.filter((row) => row.id !== "general" && statusIsListed(row.status));
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
    // A tap focuses the row too; without a hovering pointer the peek only covers the list.
    if (!jobId || renaming || !canHover()) return;
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

  const activate = () => {
    // While selecting on a phone the whole row is the checkbox, as in Mail's edit mode.
    if (selecting && !canHover()) {
      if (canSelect && !selectLocked) onToggleSelect?.(listing.id, false);
      return;
    }
    onSelect(listing.id);
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
          "flex w-full min-w-0 cursor-pointer items-center gap-2.5 rounded-[var(--control-radius)] px-2.5 py-1.5 max-[900px]:py-2 max-[900px]:pr-11 text-left text-sidebar-foreground/90 transition-colors hover:bg-sidebar-row-hover",
          "data-[active=true]:bg-sidebar-row-selected data-[active=true]:text-sidebar-foreground",
          settled && "text-sidebar-muted-foreground group-hover/row:text-sidebar-foreground group-focus-within/row:text-sidebar-foreground data-[active=true]:text-sidebar-foreground",
        )}
        onClick={() => {
          if (!renaming) activate();
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
                activate();
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
            <ListingStatusLabel status={status} title={statusTitle} />
            {listing.unsent_edits ? (
              <ListingTag
                tone="info"
                title="Edited in Anteroom since Vendoo last had this listing. Press Update Vendoo to write it onto the Vendoo form."
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
                  : `Updated in Anteroom after ${relistMarketplaces.map(marketplaceName).join(", ")} went live.`
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
        <div className="pointer-events-none absolute top-1.5 right-1.5 flex items-center opacity-0 transition-opacity group-hover/row:pointer-events-auto group-hover/row:opacity-100 group-focus-within/row:pointer-events-auto group-focus-within/row:opacity-100 max-[900px]:hidden">
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
      {/* Phones have no hover: the row's actions live behind one full-size button. */}
      {!renaming && !selecting && (
        <Menu>
          <MenuTrigger
            className="absolute top-1/2 right-0 hidden size-11 -translate-y-1/2 cursor-pointer items-center justify-center rounded-lg text-sidebar-muted-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring data-[popup-open]:bg-sidebar-row-hover max-[900px]:inline-flex"
            aria-label={`Actions for ${title}`}
          >
            <EllipsisIcon className="size-4.5" />
          </MenuTrigger>
          <MenuPopup align="end" width="content">
            <MenuItem disabled={renameListing.isPending} onClick={startRename}>
              <PencilIcon />
              Rename
            </MenuItem>
            <MenuItem
              variant="destructive"
              onClick={async () => {
                const ok = await confirmDialog(`Delete "${title}"?`, { variant: "destructive", confirmLabel: "Delete" });
                if (ok) onDelete(listing.id, title);
              }}
            >
              <Trash2Icon />
              Delete
            </MenuItem>
          </MenuPopup>
        </Menu>
      )}
      {hoverOpen && jobId && popupPos ? (
        <div
          className="dropdown-glass pointer-events-none fixed z-80 w-55 rounded-lg p-2 text-popover-foreground shadow-lg/10"
          style={{ top: popupPos.top, left: popupPos.left }}
          role="tooltip"
        >
          <div className="mb-1.5 px-0.5 text-3xs font-semibold tracking-wider text-muted-foreground uppercase">Listing status</div>
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
