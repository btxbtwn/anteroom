import { ListingTemplatesDialog } from "../ListingTemplatesDialog";
import { CopyListingDialog } from "../CopyListingDialog";
import { memo, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { MarketplaceLogo } from "../MarketplaceLogo";
import { LayoutTemplateIcon, CopyIcon, EllipsisIcon, PencilIcon, Trash2Icon, XIcon } from "lucide-react";
import { Menu, MenuItem, MenuPopup, MenuTrigger } from "../ui/menu";
import { cn } from "../../lib/utils";
import { confirmDialog } from "../../ui/confirmDialog";
import { ListingStatusLabel, ListingTag, ListingThumb, SelectCheckbox } from "./ListingBits";
import { displayCoverUrl } from "./coverSrc";
import { statusIsListed } from "../fillLog/marketplaces";
import { inventoryStatus, listedFromImport, listingRowPrice, partialSale, quantityLabel, statusHint, withDirectListings } from "./listingStatus";
import { compactRelativeTime, type Listing } from "./listingShelves";
import { prefetchListingReads } from "../../api/navigationQueries";
import { preloadListingCode } from "../../app/pageLoaders";
import { isBrowserLinkClick, listingHref } from "../../app/openListing";
import { useNavigationIntent } from "../../lib/useNavigationIntent";

function canHover() {
  return typeof window === "undefined" || !window.matchMedia || window.matchMedia("(hover: hover)").matches;
}

export const ListingRow = memo(function ListingRow({
  listing,
  selected,
  settled,
  visibleMarketplaces,
  nameBySku = false,
  hint,
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
  visibleMarketplaces?: Set<string>;
  nameBySku?: boolean;
  /** What Anteroom suggests doing next, such as "Ready to generate". */
  hint?: string;
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
  // A bulk-imported listing has no local photos yet; the imported image stands in.
  const coverUrl = displayCoverUrl(listing.cover_photo_url || listing.vendoo_cover_url);
  const status = inventoryStatus(listing);
  const partial = partialSale(listing, status);
  const quantity = quantityLabel(listing, status);
  const price = listingRowPrice(listing, status);
  const queryClient = useQueryClient();
  const navigationIntent = useNavigationIntent(() => {
    if (selecting) return;
    preloadListingCode();
    void prefetchListingReads(queryClient, listing.id);
  });
  const statusTitle = partial ? `Active · ${partial}`
    : status === "active" && String(listing.status || "draft") !== "active"
      ? "Listed directly from Anteroom."
      : statusHint(status);
  const settledAt = settled ? compactRelativeTime(listing.settled_at || listing.updated_at) : "";
  const itemRef = useRef<HTMLDivElement>(null);
  const renameInputRef = useRef<HTMLInputElement>(null);
  const skipRenameBlur = useRef(false);
  const [templatesOpen, setTemplatesOpen] = useState(false);
  const [copyOpen, setCopyOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [draftTitle, setDraftTitle] = useState(title);

  const renameListing = useMutation({
    mutationFn: (nextTitle: string) => api.conversations.update(listing.id, { title: nextTitle }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      setRenaming(false);
    },
  });

  // What the import recorded, plus Anteroom's own listings.
  const importedRows = useMemo(
    () => listedFromImport(listing, visibleMarketplaces),
    [listing, visibleMarketplaces],
  );
  const popupStatuses = useMemo(
    () => withDirectListings(
      importedRows,
      listing.direct_marketplaces,
      visibleMarketplaces,
      listing.removed_marketplaces,
    ),
    [importedRows, listing.direct_marketplaces, visibleMarketplaces, listing.removed_marketplaces],
  );
  const listedMarketplaces = popupStatuses.filter((row) => row.id !== "general" && statusIsListed(row.status));
  const activate = () => {
    // While selecting on a phone the whole row is the checkbox, as in Mail's edit mode.
    if (selecting && !canHover()) {
      if (canSelect && !selectLocked) onToggleSelect?.(listing.id, false);
      return;
    }
    onSelect(listing.id);
  };

  const startRename = () => {
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

  useEffect(() => {
    if (!renaming) return;
    const input = renameInputRef.current;
    if (!input) return;
    input.focus();
    input.select();
  }, [renaming]);

  const actionClass =
    "inline-flex size-6 cursor-pointer items-center justify-center rounded-md text-sidebar-muted-foreground outline-none hover:bg-sidebar-row-hover hover:text-sidebar-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-40";

  return (
    <div className="group/row relative mb-px" ref={itemRef} {...navigationIntent}>
      {/* Row look follows T3 Code's sidebar thread rows (MIT, (c) 2026 T3 Tools Inc.). */}
      <div
        data-active={selected}
        className={cn(
          "flex w-full min-w-0 cursor-pointer min-h-22 items-center gap-4 rounded-[var(--control-radius)] px-3 py-2 max-[900px]:min-h-24 max-[900px]:py-3 max-[900px]:pr-11 pointer-coarse:pr-11 text-left text-sidebar-foreground/90 transition-colors hover:bg-sidebar-row-hover",
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
            title={canSelect ? "Include in bulk actions" : "Unavailable for bulk actions"}
            disabled={!canSelect || selectLocked}
            onToggle={(shift) => onToggleSelect?.(listing.id, shift)}
          />
        ) : null}
        <ListingThumb
          src={coverUrl}
          dimmed={settled && !selected}
          className={cn("size-14 rounded-lg max-[900px]:size-16", settled && "group-hover/row:[&_img]:opacity-100 group-focus-within/row:[&_img]:opacity-100")}
        />
        <div className="min-w-0 flex-1">
          {renaming ? (
            <input
              ref={renameInputRef}
              className="w-full bg-transparent p-0 text-base font-medium leading-snug text-sidebar-foreground outline-none"
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
            <a
              href={listingHref(listing.id)}
              draggable={false}
              className={cn(
                "block w-full cursor-pointer truncate text-left text-base font-medium leading-snug text-inherit no-underline outline-none",
                // Stretch the link over the row so right-click anywhere offers "Open in new tab".
                !selecting && "after:absolute after:inset-0 after:content-['']",
                // The four hover actions (4 x 24px, 8px from the edge) sit over the
                // title's end; make room so it ends in an ellipsis before them.
                !selecting && "min-[901px]:pointer-fine:group-hover/row:pr-24 min-[901px]:pointer-fine:group-focus-within/row:pr-24",
              )}
              title={name === title ? title : `${name} · ${title}`}
              onClick={(e) => {
                e.stopPropagation();
                // Cmd/Ctrl-click and friends open a new tab the browser's way.
                if (isBrowserLinkClick(e)) return;
                e.preventDefault();
                activate();
              }}
              onDoubleClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                startRename();
              }}
            >
              {name}
            </a>
          )}
          <div className="mt-1 flex items-center gap-2">
            <ListingStatusLabel status={status} title={statusTitle} />
            {status === "sold" && listing.missing_sale_data?.length ? (
              <ListingTag tone="warning" title={`Missing: ${listing.missing_sale_data.join(", ")}`}>
                Missing sale data
              </ListingTag>
            ) : null}
            {partial ? <span className="truncate text-xs text-sidebar-muted-foreground">{partial}</span> : null}
            {quantity ? <span className="shrink-0 text-xs tabular-nums text-sidebar-muted-foreground">· {quantity}</span> : null}
            {hint ? <span className="min-w-0 truncate text-xs text-sidebar-muted-foreground" title={hint}>· {hint}</span> : null}
            {settledAt ? <span className="text-xs font-medium text-sidebar-muted-foreground">{settledAt}</span> : null}
            {price ? <span className="ml-auto shrink-0 pl-1 text-xs font-medium tabular-nums text-sidebar-foreground/80">{price}</span> : null}
          </div>
          {listedMarketplaces.length ? (
            <div
              className="mt-2 flex flex-wrap items-center gap-2.5"
              aria-label={`Listed on ${listedMarketplaces.map((row) => row.label).join(", ")}`}
            >
              {listedMarketplaces.map((row) => (
                <span key={row.id} className={cn("inline-flex", row.status === "SOLD" && "opacity-55")}>
                  <MarketplaceLogo id={row.id} label={`${row.label} · ${row.status.toLowerCase()}`} size={20} fit="wordmark" />
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </div>
      {!renaming && !selecting && (
        <div className="pointer-events-none absolute top-2 right-2 flex items-center opacity-0 transition-opacity group-hover/row:pointer-events-auto group-hover/row:opacity-100 group-focus-within/row:pointer-events-auto group-focus-within/row:opacity-100 max-[900px]:hidden pointer-coarse:hidden">
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
            <PencilIcon className="size-3.5" />
          </button>
          <button type="button" className={actionClass} title="Listing templates" aria-label={`Templates for ${title}`} onClick={event=>{event.stopPropagation();setTemplatesOpen(true);}}><LayoutTemplateIcon className="size-3.5"/></button>
          <button type="button" className={actionClass} title="Copy listing" aria-label={`Copy ${title}`} onClick={(event) => { event.stopPropagation(); setCopyOpen(true); }}><CopyIcon className="size-3.5" /></button>
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
            <XIcon className="size-3.5" />
          </button>
        </div>
      )}
      {/* Phones and tablets have no hover: the row's actions live behind one full-size button. */}
      {!renaming && !selecting && (
        <Menu>
          <MenuTrigger
            className="absolute top-1/2 right-0 hidden size-11 -translate-y-1/2 cursor-pointer items-center justify-center rounded-lg text-sidebar-muted-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring data-[popup-open]:bg-sidebar-row-hover max-[900px]:inline-flex pointer-coarse:inline-flex"
            aria-label={`Actions for ${title}`}
          >
            <EllipsisIcon className="size-4.5" />
          </MenuTrigger>
          <MenuPopup align="end" width="content">
            <MenuItem disabled={renameListing.isPending} onClick={startRename}>
              <PencilIcon />
              Rename
            </MenuItem>
            <MenuItem onClick={()=>setTemplatesOpen(true)}><LayoutTemplateIcon/>Templates</MenuItem>
            <MenuItem onClick={() => setCopyOpen(true)}><CopyIcon />Copy listing</MenuItem>
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
      {templatesOpen?<ListingTemplatesDialog conversationId={listing.id} onClose={()=>setTemplatesOpen(false)}/>:null}
      {copyOpen ? <CopyListingDialog conversationId={listing.id} onClose={() => setCopyOpen(false)} onCopied={(id) => { setCopyOpen(false); onSelect(id); }} /> : null}
    </div>
  );
});
