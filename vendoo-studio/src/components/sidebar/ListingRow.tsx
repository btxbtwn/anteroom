import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { MarketplaceLogo } from "../MarketplaceLogo";
import { marketplacesNeedingRelist, relistStage } from "../relistStatus";
import { marketplaceName } from "../marketplaceNames";
import { SelectCheck } from "./SidebarIcons";
import { listedFromImport, liveStatusClass, marketplaceStatusesFromDraft, statusHint } from "./listingStatus";
import { compactRelativeTime, type Listing } from "./listingShelves";

const HOVER_STATUS_DELAY_MS = 280;
const LISTED_LIVE_STATUSES = new Set(["LISTED", "SOLD"]);

export function ListingRow({
  listing,
  selected,
  settled,
  busy,
  settling,
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
  onSettle,
  onUnsettle,
}: {
  listing: Listing;
  selected: boolean;
  settled: boolean;
  busy: boolean;
  settling: boolean;
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
  onSettle?: (id: string) => void;
  onUnsettle?: (id: string) => void;
}) {
  const title = listing.title || "Untitled";
  // Named by SKU, a listing without one still shows its title.
  const name = (nameBySku && listing.sku?.trim()) || title;
  // A bulk-imported listing has no local photos yet; Vendoo's own image stands in.
  const coverUrl = listing.cover_photo_url || listing.vendoo_cover_url || null;
  const status = String(listing.status || "draft");
  const statusClass = status.replace(/_/g, "-");
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

  return (
    <div
      className={`nav-item${selecting ? " is-selecting" : ""}`}
      ref={itemRef}
      onMouseEnter={openHover}
      onMouseLeave={closeHover}
      onFocus={openHover}
      onBlur={(event) => {
        if (!itemRef.current?.contains(event.relatedTarget as Node | null)) closeHover();
      }}
    >
      <div
        className={`nav-link${selected ? " selected" : ""}${settled ? " settled" : ""}`}
        onClick={() => {
          if (!renaming) onSelect(listing.id);
        }}
      >
        {selecting ? (
          <button
            type="button"
            role="checkbox"
            className={`nav-select${checked ? " is-checked" : ""}`}
            aria-checked={checked}
            aria-label={`${checked ? "Deselect" : "Select"} ${title}`}
            disabled={!canSelect || selectLocked}
            title={canSelect ? "Include in regenerate" : "Add photos before regenerating"}
            onClick={(event) => {
              event.stopPropagation();
              if (!canSelect || selectLocked) return;
              onToggleSelect?.(listing.id, event.shiftKey);
            }}
          >
            {checked ? <SelectCheck /> : null}
          </button>
        ) : null}
        <div className="nav-thumb" aria-hidden="true">
          {coverUrl ? (
            <img className="nav-thumb-img" src={coverUrl} alt="" loading="lazy" draggable={false} />
          ) : (
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <rect x="1.5" y="2.5" width="13" height="11" rx="2" stroke="currentColor" strokeWidth="1.2" />
              <path d="M2 11l3.2-3.2a1.2 1.2 0 011.7 0L10 10.9" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
              <circle cx="10.4" cy="6" r="1.1" fill="currentColor" />
            </svg>
          )}
        </div>
        <div className="nav-link-body">
          {renaming ? (
            <input
              ref={renameInputRef}
              className="nav-rename-input"
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
              className="nav-link-title"
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
          <div className="nav-link-meta">
            <span className={`nav-status nav-status-${statusClass}`} title={statusHint(status)}>
              {status.replace(/_/g, " ")}
            </span>
            {listing.unsent_edits ? (
              <span
                className="nav-status nav-status-unsent"
                title="Edited in Studio since Vendoo last had this listing. Press Update Vendoo to write it onto the Vendoo form."
              >
                unsent
              </span>
            ) : null}
            {relistMarketplaces.length ? (
              <span
                className="nav-status nav-status-relist"
                title={relistStageNow === "list"
                  ? "Delisted in Vendoo and not listed again yet, so it is live nowhere."
                    + ` List it again to finish: ${relistMarketplaces.map(marketplaceName).join(", ")}.`
                  : `Updated in Studio after ${relistMarketplaces.map(marketplaceName).join(", ")} went live.`
                    + " Delist and relist in Vendoo to publish the new version."}
              >
                relist
              </span>
            ) : null}
            {settledAt ? <span className="nav-time">{settledAt}</span> : null}
          </div>
          {listedMarketplaces.length ? (
            <div
              className="nav-listed-logos"
              aria-label={`Listed on ${listedMarketplaces.map((row) => row.label).join(", ")}`}
            >
              {listedMarketplaces.map((row) => (
                <span key={row.id} className={row.status === "SOLD" ? "nav-listed-logo is-sold" : "nav-listed-logo"}>
                  <MarketplaceLogo id={row.id} label={`${row.label} · ${row.status.toLowerCase()}`} size={14} />
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </div>
      {!renaming && (
        <div className="nav-actions">
          <button
            className="nav-action"
            title="Rename listing"
            aria-label={`Rename ${title}`}
            disabled={renameListing.isPending}
            onClick={(e) => {
              e.stopPropagation();
              startRename();
            }}
          >
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <path
                d="M11.5 2.5l2 2L5.5 12.5H3.5v-2L11.5 2.5z"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
          {!busy && onSettle && (
            <button
              className="nav-action"
              title="Settle listing"
              aria-label={`Settle ${title}`}
              disabled={settling}
              onClick={(e) => {
                e.stopPropagation();
                onSettle(listing.id);
              }}
            >
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path d="M3.5 8.5l3 3 6-7" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          )}
          {onUnsettle && (
            <button
              className="nav-action"
              title="Un-settle listing"
              aria-label={`Un-settle ${title}`}
              disabled={settling}
              onClick={(e) => {
                e.stopPropagation();
                onUnsettle(listing.id);
              }}
            >
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path d="M4.5 7.5H12a3 3 0 010 6H9" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
                <path d="M7 4.5L4.5 7.5 7 10.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          )}
          <button
            className="nav-action"
            title="Delete"
            aria-label={`Delete ${title}`}
            onClick={(e) => {
              e.stopPropagation();
              if (confirm(`Delete "${title}"?`)) onDelete(listing.id, title);
            }}
          >
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          </button>
        </div>
      )}
      {hoverOpen && jobId && popupPos ? (
        <div
          className="nav-vendoo-status-popup"
          style={{ top: popupPos.top, left: popupPos.left }}
          role="tooltip"
        >
          <div className="nav-vendoo-status-title">Vendoo status</div>
          {loadingStatus ? (
            <div className="nav-vendoo-status-empty">Reading…</div>
          ) : popupStatuses.length ? (
            <ul className="nav-vendoo-status-list">
              {popupStatuses.map((row) => (
                <li key={row.id} className="nav-vendoo-status-row">
                  <MarketplaceLogo id={row.id} label={row.label} size={16} />
                  <span className="nav-vendoo-status-market">{row.label}</span>
                  <span className={`pr-live-status ${liveStatusClass(row.status)}`}>{row.status}</span>
                </li>
              ))}
            </ul>
          ) : (
            <div className="nav-vendoo-status-empty">
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
