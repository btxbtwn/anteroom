import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import {
  bulkRegenerateToast,
  listingCanRegenerate,
  mergeRegenerateIds,
  toggleListingSelection,
  type BulkPriceDrop,
  type BulkRegenerateListing,
} from "../bulkRegenerate";
import { isConfirmDialogOpen } from "../ui/confirmDialog";
import { addToast } from "../ui/toast";
import { jobsPollMs } from "../api/polling";
import { UpdateButton } from "./UpdateButton";
import { VendooImportButton } from "./VendooImportButton";
import {
  SETTINGS_NAV_ITEMS,
  SETTINGS_SECTION_LABELS,
  searchSettings,
  type SettingsSearchItem,
  type SettingsSectionId,
} from "./settingsNav";
import { ListingFilters } from "./ListingFilters";
import { SuggestionsPanel } from "./SuggestionsPanel";
import type { BulkRegenerate, BulkRegenerateRun } from "./useBulkRegenerate";
import { BulkRegeneratePriceDialog } from "./BulkRegeneratePriceDialog";
import { BulkPriceDropDialog } from "./BulkPriceDropDialog";
import { RegenerateReviewDialog } from "./RegenerateReviewDialog";
import { stopTitlebarDrag } from "./WorkspaceTopbar";
import type { WorkspaceView } from "./workspaceCrumbs";
import type { Suggestion } from "../api/types";
import {
  DEFAULT_LISTING_FILTERS,
  filterListings,
  labelCounts,
  labelOptions,
  inStatusTab,
  matchesSearch,
  marketplaceCounts,
  marketplaceOptions,
  notListedCount,
  notListedOnCounts,
  needsRelistCount,
  unsentCount,
  staleCounts,
  sortListings,
  statusCounts,
  type ListingFilters as Filters,
} from "./inventoryFilters";
import {
  AnalyticsIcon,
  AssistantIcon,
  BackIcon,
  MarketingIcon,
  QueueIcon,
  SearchIcon,
  SettingsIcon,
  SettingsSectionIcon,
  SourcingIcon,
} from "./sidebar/SidebarIcons";
import { ListingRow } from "./sidebar/ListingRow";
import {
  activeAnchorMs,
  clearLegacySettledExpanded,
  readLegacySettledExpanded,
  settledAnchorMs,
  type Listing,
} from "./sidebar/listingShelves";

const SETTLED_TAIL_INITIAL_COUNT = 10;
const SETTLED_TAIL_PAGE_COUNT = 25;
const BUSY_STATUSES = new Set(["in_progress", "listing"]);
const NO_SUGGESTIONS: Suggestion[] = [];

interface Props {
  conversations: Listing[] | undefined;
  selectedConvId: string | null;
  activeView: WorkspaceView;
  settingsSection: SettingsSectionId;
  mobileOpen?: boolean;
  listingQuery: string;
  onSearchQueryChange: (query: string) => void;
  onSelect: (id: string) => void;
  onDelete: (id: string, title: string) => void;
  onOpenSettings: () => void;
  onOpenAnalytics: () => void;
  onOpenMarketing: () => void;
  onOpenAssistant: () => void;
  onOpenSourcing: () => void;
  onOpenQueue: () => void;
  queueCount: number;
  onCloseSettings: () => void;
  onSettingsSectionChange: (section: SettingsSectionId) => void;
  onSettingsSearchResult: (item: SettingsSearchItem) => void;
  /** The bulk Regenerate / Generate run, owned by App so it outlives the sidebar. */
  bulk: BulkRegenerate;
}

export function ListingSidebar({
  conversations,
  selectedConvId,
  activeView,
  settingsSection,
  mobileOpen,
  listingQuery,
  onSearchQueryChange,
  onSelect,
  onDelete,
  onOpenSettings,
  onOpenAnalytics,
  onOpenMarketing,
  onOpenAssistant,
  onOpenSourcing,
  onOpenQueue,
  queueCount,
  onCloseSettings,
  onSettingsSectionChange,
  onSettingsSearchResult,
  bulk,
}: Props) {
  const queryClient = useQueryClient();
  const legacySettled = readLegacySettledExpanded();
  const [settledExpanded, setSettledExpanded] = useState(legacySettled ?? true);
  const [settledVisibleCount, setSettledVisibleCount] = useState(SETTLED_TAIL_INITIAL_COUNT);
  const [settingsQuery, setSettingsQuery] = useState("");
  const [filters, setFilters] = useState<Filters>(DEFAULT_LISTING_FILTERS);
  const [activeResultIndex, setActiveResultIndex] = useState(0);
  const [selecting, setSelecting] = useState(false);
  const [pricePrompt, setPricePrompt] = useState<BulkRegenerateListing[] | null>(null);
  // Photos, labels and measurements are checked before anything is wiped.
  const [reviewPrompt, setReviewPrompt] = useState<
    { rows: BulkRegenerateListing[]; drops?: ReadonlyMap<string, BulkPriceDrop> } | null
  >(null);
  const [pickedIds, setPickedIds] = useState<Set<string>>(() => new Set());
  const [markdownIds, setMarkdownIds] = useState<string[] | null>(null);
  const settingsSearchRef = useRef<HTMLInputElement>(null);
  const selectionAnchor = useRef<string | null>(null);
  const migratedSettledRef = useRef(false);
  const settingsMode = activeView === "settings";

  const { data: uiPrefs } = useQuery({
    queryKey: ["settings-ui"],
    queryFn: api.settings.ui,
  });
  const nameBySku = uiPrefs?.listing_names === "sku";

  useEffect(() => {
    if (!uiPrefs) return;
    if (!migratedSettledRef.current) {
      migratedSettledRef.current = true;
      const legacy = readLegacySettledExpanded();
      if (legacy !== null) {
        clearLegacySettledExpanded();
        if (legacy !== uiPrefs.settled_shelf_expanded) {
          setSettledExpanded(legacy);
          void api.settings
            .setUi({ settled_shelf_expanded: legacy })
            .then(() => {
              queryClient.invalidateQueries({ queryKey: ["settings-ui"] });
            })
            .catch(() => {
              migratedSettledRef.current = false;
            });
          return;
        }
      }
    }
    setSettledExpanded(uiPrefs.settled_shelf_expanded);
  }, [uiPrefs, queryClient]);

  const settleListing = useMutation({
    mutationFn: (id: string) => api.conversations.settle(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["conversations"] }),
  });
  const unsettleListing = useMutation({
    mutationFn: (id: string) => api.conversations.unsettle(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["conversations"] }),
  });
  const { data: jobs } = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.jobs.list(),
    refetchInterval: (query) => jobsPollMs(query.state.data),
  });
  const jobIdByConversation = useMemo(() => {
    const map = new Map<string, string>();
    for (const job of jobs || []) {
      if (!job?.conversation_id || job.status === "cancelled") continue;
      if (!job.vendoo_item_id && !job.vendoo_url) continue;
      if (!map.has(job.conversation_id)) map.set(job.conversation_id, job.id);
    }
    return map;
  }, [jobs]);

  const needle = listingQuery.trim().toLowerCase();
  const { activeListings, settledListings } = useMemo(() => {
    const visible = filterListings(conversations || [], needle, filters);
    const active = visible.filter((listing) => !listing.settled_at).sort((left, right) => activeAnchorMs(right) - activeAnchorMs(left));
    const settled = visible.filter((listing) => listing.settled_at).sort((left, right) => settledAnchorMs(right) - settledAnchorMs(left) || left.id.localeCompare(right.id));
    // Every sort but "recent" replaces each shelf's own recency order.
    return {
      activeListings: sortListings(active, filters.sort),
      settledListings: sortListings(settled, filters.sort),
    };
  }, [conversations, filters, needle]);

  const listingEligibleIds = useMemo(
    () => activeListings.filter((listing) => listingCanRegenerate(listing)).map((listing) => listing.id),
    [activeListings],
  );
  const { data: suggestionData } = useQuery({
    queryKey: ["suggestions"],
    queryFn: api.suggestions.list,
  });
  const suggestions = suggestionData?.suggestions ?? NO_SUGGESTIONS;
  const listingsById = useMemo(
    () => new Map((conversations || []).map((listing) => [listing.id, listing])),
    [conversations],
  );
  const suggestionEligibleIds = useMemo(
    () => suggestions.filter((suggestion) => {
      const listing = listingsById.get(suggestion.conversation_id);
      if (listing) return listingCanRegenerate(listing);
      return Boolean(suggestion.cover_photo_url);
    }).map((suggestion) => suggestion.conversation_id),
    [listingsById, suggestions],
  );
  const eligibleIds = useMemo(
    () => mergeRegenerateIds(suggestionEligibleIds, listingEligibleIds),
    [listingEligibleIds, suggestionEligibleIds],
  );
  const selectedIds = useMemo(() => {
    if (pickedIds.size === 0) return pickedIds;
    const eligible = new Set(eligibleIds);
    let changed = false;
    const next = new Set<string>();
    for (const id of pickedIds) {
      if (eligible.has(id)) next.add(id);
      else changed = true;
    }
    return changed ? next : pickedIds;
  }, [eligibleIds, pickedIds]);

  useEffect(() => {
    if (!selecting || bulk.running) return;
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape" || isConfirmDialogOpen() || pricePrompt || reviewPrompt || markdownIds) return;
      setSelecting(false);
      setPickedIds(new Set());
      selectionAnchor.current = null;
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selecting, bulk.running, pricePrompt, reviewPrompt, markdownIds]);

  const exitSelecting = useCallback(() => {
    setSelecting(false);
    setPickedIds(new Set());
    selectionAnchor.current = null;
  }, []);

  const toggleSelect = useCallback((id: string, shift: boolean, orderedIds: readonly string[]) => {
    setPickedIds((prev) => {
      const eligible = new Set(eligibleIds);
      const visible = new Set<string>();
      for (const picked of prev) {
        if (eligible.has(picked)) visible.add(picked);
      }
      const next = toggleListingSelection(visible, id, orderedIds, shift, selectionAnchor.current);
      selectionAnchor.current = next.anchorId;
      return next.selected;
    });
  }, [eligibleIds]);

  const toggleSelectAll = useCallback(() => {
    setPickedIds((prev) => {
      const allSelected = eligibleIds.length > 0 && eligibleIds.every((id) => prev.has(id));
      selectionAnchor.current = null;
      return allSelected ? new Set() : new Set(eligibleIds);
    });
  }, [eligibleIds]);

  const finishRegenerate = useCallback(async (
    ids: string[],
    drops?: ReadonlyMap<string, BulkPriceDrop>,
  ) => {
    const titles = new Map(ids.map((id) => {
      const listing = listingsById.get(id);
      const suggestion = suggestions.find((item) => item.conversation_id === id);
      return [id, listing?.title || suggestion?.title || "Untitled"];
    }));
    // The bulk bar tracks the run on its own; checkboxes would only sit locked.
    exitSelecting();
    const result = await bulk.start(ids, titles, "rewrite", drops);
    if (!result) return;
    if (result.failed.length > 0) {
      setPickedIds(new Set(result.failed.map((item) => item.id)));
      setSelecting(true);
    }
    addToast(bulkRegenerateToast(result));
  }, [bulk, exitSelecting, listingsById, suggestions]);

  const regenerateSelected = useCallback(async () => {
    const ids = eligibleIds.filter((id) => selectedIds.has(id));
    if (!ids.length || bulk.running) return;
    const rows: BulkRegenerateListing[] = ids.map((id) => {
      const listing = listingsById.get(id);
      const suggestion = suggestions.find((item) => item.conversation_id === id);
      const price = listing?.price;
      return {
        id,
        title: listing?.title || suggestion?.title || "Untitled",
        price: price != null && price > 0 ? price : null,
      };
    });
    if (!rows.some((row) => row.price != null)) {
      setReviewPrompt({ rows });
      return;
    }
    setPricePrompt(rows);
  }, [bulk.running, eligibleIds, listingsById, selectedIds, suggestions]);

  const visibleSettled = useMemo(() => {
    const visible = settledListings.slice(0, settledVisibleCount);
    const selected = settledListings.find((listing) => listing.id === selectedConvId && activeView === "listings");
    if (selected && !visible.some((listing) => listing.id === selected.id)) visible.push(selected);
    return visible;
  }, [activeView, selectedConvId, settledListings, settledVisibleCount]);

  const renderedSettled = settledExpanded
    ? visibleSettled
    : visibleSettled.filter((listing) => listing.id === selectedConvId && activeView === "listings");
  const hiddenSettledCount = Math.max(0, settledListings.length - visibleSettled.length);

  const { data: marketplaceSettings } = useQuery({
    queryKey: ["settings-marketplaces"],
    queryFn: api.settings.marketplaces,
  });
  const visibleMarketplaces = useMemo(
    () => (marketplaceSettings ? new Set(marketplaceSettings.selected) : undefined),
    [marketplaceSettings],
  );
  // The filter bar offers what the inventory actually holds: the statuses it
  // wears, the marketplaces it sits on, and the Vendoo labels it carries.
  const searched = useMemo(
    () => (conversations || []).filter((listing) => matchesSearch(listing, needle)),
    [conversations, needle],
  );
  const counts = useMemo(() => statusCounts(searched), [searched]);
  // Each filter row carries how many listings it would keep, counted over the
  // status tab in front of the reader rather than the whole inventory.
  const facetSource = useMemo(
    () => searched.filter((listing) => inStatusTab(listing, filters.status)),
    [searched, filters.status],
  );
  const filterMarketplaces = useMemo(
    () => marketplaceOptions(conversations || [], marketplaceSettings?.selected || [], filters.notListed),
    [conversations, marketplaceSettings, filters.notListed],
  );
  // Under "Not listed" a marketplace row keeps what is missing from it.
  const filterMarketplaceCounts = useMemo(
    () => (filters.notListed ? notListedOnCounts(facetSource, filterMarketplaces) : marketplaceCounts(facetSource)),
    [facetSource, filterMarketplaces, filters.notListed],
  );
  const filterLabelCounts = useMemo(() => labelCounts(facetSource), [facetSource]);
  const filterNotListedCount = useMemo(() => notListedCount(facetSource), [facetSource]);
  const filterStaleCounts = useMemo(() => staleCounts(facetSource), [facetSource]);
  const filterRelistCount = useMemo(() => needsRelistCount(facetSource), [facetSource]);
  const filterUnsentCount = useMemo(() => unsentCount(facetSource), [facetSource]);
  const labels = useMemo(() => labelOptions(conversations || []), [conversations]);
  // Cached Vendoo statuses for every rendered thread, so rows can badge where
  // they're listed without hovering. Reads the server cache only.
  const renderedJobIds = useMemo(
    () =>
      [...activeListings, ...renderedSettled]
        .map((listing) => jobIdByConversation.get(listing.id))
        .filter((id): id is string => Boolean(id))
        .sort(),
    [activeListings, renderedSettled, jobIdByConversation],
  );
  const { data: statusDrafts } = useQuery({
    queryKey: ["marketplace-statuses", renderedJobIds.join(",")],
    queryFn: () => api.jobs.marketplaceStatuses(renderedJobIds),
    enabled: renderedJobIds.length > 0,
    refetchInterval: 15000,
    placeholderData: (previous) => previous,
  });
  const statusDraftFor = (conversationId: string) => {
    const jobId = jobIdByConversation.get(conversationId);
    return jobId ? statusDrafts?.[jobId] : undefined;
  };
  const settingsResults = useMemo(() => searchSettings(settingsQuery), [settingsQuery]);
  const isSettingsSearching = settingsQuery.trim().length > 0;

  useEffect(() => {
    if (!settingsMode) {
      setSettingsQuery("");
      setActiveResultIndex(0);
    }
  }, [settingsMode]);

  useEffect(() => {
    setActiveResultIndex((index) => Math.min(index, Math.max(settingsResults.length - 1, 0)));
  }, [settingsResults.length]);

  useEffect(() => {
    if (!settingsMode) return;
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target;
      if (
        target instanceof HTMLElement &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable ||
          target.closest('[role="dialog"], [aria-modal="true"]') !== null)
      ) {
        return;
      }
      event.preventDefault();
      settingsSearchRef.current?.focus();
      settingsSearchRef.current?.select();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [settingsMode]);

  const toggleSettledShelf = useCallback(() => {
    setSettledExpanded((value) => {
      const next = !value;
      void api.settings
        .setUi({ settled_shelf_expanded: next })
        .then(() => {
          queryClient.invalidateQueries({ queryKey: ["settings-ui"] });
        })
        .catch(() => {
          /* keep in-memory toggle if disk write fails */
        });
      return next;
    });
  }, [queryClient]);

  const handleSearchChange = (value: string) => {
    onSearchQueryChange(value);
    setSettledVisibleCount(SETTLED_TAIL_INITIAL_COUNT);
  };

  const handleFiltersChange = (next: Filters) => {
    setFilters(next);
    setSettledVisibleCount(SETTLED_TAIL_INITIAL_COUNT);
  };

  const clearSettingsSearch = useCallback(() => {
    setSettingsQuery("");
    setActiveResultIndex(0);
  }, []);

  const handleSettingsResult = useCallback(
    (item: SettingsSearchItem) => {
      clearSettingsSearch();
      onSettingsSearchResult(item);
    },
    [clearSettingsSearch, onSettingsSearchResult],
  );

  const handleSettingsSearchKeyDown = useCallback(
    (event: KeyboardEvent<HTMLInputElement>) => {
      if (event.key === "Escape" && isSettingsSearching) {
        event.preventDefault();
        event.stopPropagation();
        clearSettingsSearch();
        return;
      }
      if (settingsResults.length === 0) return;
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setActiveResultIndex((index) => (index + 1) % settingsResults.length);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setActiveResultIndex((index) => (index - 1 + settingsResults.length) % settingsResults.length);
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        const result = settingsResults[activeResultIndex];
        if (result) handleSettingsResult(result);
      }
    },
    [activeResultIndex, clearSettingsSearch, handleSettingsResult, isSettingsSearching, settingsResults],
  );

  return (
    <aside
      id="listings-sidebar"
      className={`panel sidebar${settingsMode ? " sidebar-settings" : ""}`}
      aria-label={settingsMode ? "Settings" : "Listings"}
      aria-hidden={mobileOpen === false ? true : undefined}
    >
      <div className="sidebar-header pywebview-drag-region">
        <div className="sidebar-brand">
          <span className="sidebar-wordmark">Vendoo</span>
          <span className="sidebar-product">Studio</span>
        </div>
        {!settingsMode ? (
          <button
            type="button"
            className="sidebar-icon-btn sidebar-header-settings"
            title="Settings"
            aria-label="Settings"
            onMouseDown={stopTitlebarDrag}
            onClick={onOpenSettings}
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <circle cx="3.5" cy="8" r="1.15" fill="currentColor" />
              <circle cx="8" cy="8" r="1.15" fill="currentColor" />
              <circle cx="12.5" cy="8" r="1.15" fill="currentColor" />
            </svg>
          </button>
        ) : (
          <button
            type="button"
            className="sidebar-icon-btn sidebar-header-settings"
            title="Back to listings"
            aria-label="Back to listings"
            onMouseDown={stopTitlebarDrag}
            onClick={onCloseSettings}
          >
            <BackIcon />
          </button>
        )}
      </div>

      {settingsMode ? (
        <>
          <div className="sidebar-toolbar">
            <label className="sidebar-search settings-sidebar-search">
              <SearchIcon />
              <input
                ref={settingsSearchRef}
                type="search"
                value={settingsQuery}
                onChange={(e) => {
                  setSettingsQuery(e.target.value);
                  setActiveResultIndex(0);
                }}
                onKeyDown={handleSettingsSearchKeyDown}
                placeholder="Search"
                aria-label="Search settings"
                role="combobox"
                aria-autocomplete="list"
                aria-expanded={isSettingsSearching && settingsResults.length > 0}
                aria-controls={isSettingsSearching && settingsResults.length > 0 ? "settings-search-results" : undefined}
                aria-activedescendant={
                  isSettingsSearching && settingsResults[activeResultIndex]
                    ? `settings-search-result-${settingsResults[activeResultIndex].id}`
                    : undefined
                }
              />
              {isSettingsSearching ? (
                <button
                  type="button"
                  className="settings-search-clear"
                  aria-label="Clear search"
                  onClick={() => {
                    clearSettingsSearch();
                    settingsSearchRef.current?.focus();
                  }}
                >
                  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                    <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
                  </svg>
                </button>
              ) : (
                <kbd className="settings-search-kbd">/</kbd>
              )}
            </label>
          </div>

          <div className="sidebar-list settings-sidebar-nav">
            {isSettingsSearching && settingsResults.length === 0 ? (
              <div className="sidebar-empty">No settings found</div>
            ) : null}
            {isSettingsSearching ? (
              <div id="settings-search-results" role="listbox" aria-label="Settings search results">
                {settingsResults.map((item, index) => (
                  <button
                    key={item.id}
                    id={`settings-search-result-${item.id}`}
                    type="button"
                    role="option"
                    aria-selected={index === activeResultIndex}
                    className={`settings-nav-result${index === activeResultIndex ? " selected" : ""}`}
                    onMouseEnter={() => setActiveResultIndex(index)}
                    onClick={() => handleSettingsResult(item)}
                  >
                    <span className="settings-nav-result-title">{item.title}</span>
                    <span className="settings-nav-result-section">{SETTINGS_SECTION_LABELS[item.section]}</span>
                  </button>
                ))}
              </div>
            ) : (
              <nav aria-label="Settings sections">
                {SETTINGS_NAV_ITEMS.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={`settings-nav-item${settingsSection === item.id ? " selected" : ""}`}
                    onClick={() => onSettingsSectionChange(item.id)}
                  >
                    <SettingsSectionIcon section={item.id} />
                    <span>{item.label}</span>
                  </button>
                ))}
              </nav>
            )}
          </div>
        </>
      ) : (
        <>
          <div className="sidebar-toolbar">
            <label className="sidebar-search">
              <SearchIcon />
              <input
                type="search"
                value={listingQuery}
                onChange={(e) => handleSearchChange(e.target.value)}
                placeholder="Search by title, SKU or label"
                aria-label="Search listings by title, SKU or label"
              />
            </label>
            <button
              type="button"
              className={`sidebar-select-btn${selecting ? " selected" : ""}`}
              title={selecting ? "Stop selecting" : "Select listings to regenerate"}
              aria-label={selecting ? "Stop selecting" : "Select listings to regenerate"}
              aria-pressed={selecting}
              onClick={() => {
                if (bulk.running) return;
                if (selecting) exitSelecting();
                else setSelecting(true);
              }}
            >
              Select
            </button>
          </div>

          <SuggestionsPanel
            variant="sidebar"
            selectedConvId={selectedConvId}
            onSelect={onSelect}
            selecting={selecting}
            selectedIds={selectedIds}
            selectLocked={bulk.running}
            canSelect={(suggestion) => suggestionEligibleIds.includes(suggestion.conversation_id)}
            onToggleSelect={(id, shift) => toggleSelect(id, shift, suggestionEligibleIds)}
          />

          <ListingFilters
            filters={filters}
            counts={counts}
            labels={labels}
            labelCounts={filterLabelCounts}
            marketplaces={filterMarketplaces}
            marketplaceCounts={filterMarketplaceCounts}
            notListedCount={filterNotListedCount}
            needsRelistCount={filterRelistCount}
            unsentCount={filterUnsentCount}
            staleCounts={filterStaleCounts}
            onChange={handleFiltersChange}
          />

          <div className="sidebar-list">
            {activeListings.map((listing) => (
              <ListingRow
                key={listing.id}
                listing={listing}
                selected={selectedConvId === listing.id && activeView === "listings"}
                settled={false}
                busy={BUSY_STATUSES.has(String(listing.status || "draft"))}
                settling={settleListing.isPending}
                jobId={jobIdByConversation.get(listing.id)}
                statusDraft={statusDraftFor(listing.id)}
                visibleMarketplaces={visibleMarketplaces}
                nameBySku={nameBySku}
                selecting={selecting}
                checked={selectedIds.has(listing.id)}
                canSelect={listingCanRegenerate(listing)}
                selectLocked={bulk.running}
                onToggleSelect={(id, shift) => toggleSelect(id, shift, listingEligibleIds)}
                onSelect={onSelect}
                onDelete={onDelete}
                onSettle={(id) => settleListing.mutate(id)}
              />
            ))}

            {settledListings.length > 0 && (
              <>
                <button
                  type="button"
                  className="sidebar-shelf-header"
                  aria-expanded={settledExpanded}
                  onClick={toggleSettledShelf}
                >
                  <span className="sidebar-shelf-label">
                    {settledExpanded ? "Settled" : `Settled (${settledListings.length})`}
                  </span>
                  <span className="sidebar-shelf-rule" aria-hidden="true" />
                  <svg className="sidebar-shelf-chevron" width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                    <path d="M4 6l4 4 4-4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </button>
                {renderedSettled.map((listing) => (
                  <ListingRow
                    key={listing.id}
                    listing={listing}
                    selected={selectedConvId === listing.id && activeView === "listings"}
                    settled
                    busy={false}
                    settling={unsettleListing.isPending}
                    jobId={jobIdByConversation.get(listing.id)}
                    statusDraft={statusDraftFor(listing.id)}
                    visibleMarketplaces={visibleMarketplaces}
                    nameBySku={nameBySku}
                    onSelect={onSelect}
                    onDelete={onDelete}
                    onUnsettle={(id) => unsettleListing.mutate(id)}
                  />
                ))}
                {settledExpanded && hiddenSettledCount > 0 && (
                  <button
                    type="button"
                    className="sidebar-show-more"
                    onClick={() => setSettledVisibleCount((count) => count + SETTLED_TAIL_PAGE_COUNT)}
                  >
                    Show {Math.min(hiddenSettledCount, SETTLED_TAIL_PAGE_COUNT)} more
                  </button>
                )}
              </>
            )}

            {(!conversations || conversations.length === 0) && (
              <div className="sidebar-empty">No listings yet</div>
            )}
            {conversations && conversations.length > 0 && activeListings.length === 0 && settledListings.length === 0 && (
              <div className="sidebar-empty">No matching listings</div>
            )}
          </div>
        </>
      )}

      {!settingsMode && (selecting || bulk.running) && (
        <div className="sidebar-bulk-bar" role="region" aria-label="Regenerate listings">
          <p className="sidebar-bulk-count">{bulkStatusLabel(bulk.run, selectedIds.size, eligibleIds.length)}</p>
          <div className="sidebar-bulk-actions">
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={bulk.running || eligibleIds.length === 0}
              onClick={toggleSelectAll}
            >
              {eligibleIds.length > 0 && eligibleIds.every((id) => selectedIds.has(id)) ? "None" : "All"}
            </button>
            <button
              type="button"
              className="btn btn-danger btn-sm"
              disabled={bulk.running || selectedIds.size === 0}
              onClick={() => { void regenerateSelected(); }}
            >
              Regenerate
            </button>
            <button
              type="button"
              className="btn btn-outline btn-sm"
              title="Review a lower price for each selected listing. Saves in Studio only; nothing is sent to Vendoo."
              disabled={bulk.running || selectedIds.size === 0}
              onClick={() => setMarkdownIds(eligibleIds.filter((id) => selectedIds.has(id)))}
            >
              Mark down
            </button>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={Boolean(bulk.run?.cancelRequested)}
              onClick={() => {
                if (bulk.running) bulk.cancel();
                else exitSelecting();
              }}
            >
              {bulk.running ? (bulk.run?.cancelRequested ? "Finishing…" : "Stop batch") : "Cancel"}
            </button>
          </div>
        </div>
      )}

      <div className="sidebar-footer">
        <div className="sidebar-footer-actions">
          {settingsMode ? (
            <button
              type="button"
              className="sidebar-back-btn"
              aria-label="Back"
              onClick={onCloseSettings}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M19 12H5M12 19l-7-7 7-7" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              <span>Back</span>
            </button>
          ) : (
            <>
              <button
                type="button"
                className={`sidebar-icon-btn queue-nav${activeView === "queue" ? " selected" : ""}`}
                title={queueCount ? `Queue (${queueCount})` : "Queue"}
                aria-label={`Queue${queueCount ? ` (${queueCount})` : ""}`}
                aria-pressed={activeView === "queue"}
                onClick={onOpenQueue}
              >
                <QueueIcon />
                {queueCount ? <span className="queue-nav-dot" aria-hidden="true" /> : null}
              </button>
              <VendooImportButton />
              <button
                type="button"
                className={`sidebar-icon-btn${activeView === "sourcing" ? " selected" : ""}`}
                title="Sourcing"
                aria-label="Sourcing"
                aria-pressed={activeView === "sourcing"}
                onClick={onOpenSourcing}
              >
                <SourcingIcon />
              </button>
              <button
                type="button"
                className={`sidebar-icon-btn${activeView === "analytics" ? " selected" : ""}`}
                title="Analytics"
                aria-label="Analytics"
                aria-pressed={activeView === "analytics"}
                onClick={onOpenAnalytics}
              >
                <AnalyticsIcon />
              </button>
              <button
                type="button"
                className={`sidebar-icon-btn${activeView === "marketing" ? " selected" : ""}`}
                title="Marketing"
                aria-label="Marketing"
                aria-pressed={activeView === "marketing"}
                onClick={onOpenMarketing}
              >
                <MarketingIcon />
              </button>
              <button
                type="button"
                className={`sidebar-icon-btn${activeView === "assistant" ? " selected" : ""}`}
                title="Assistant"
                aria-label="Assistant"
                aria-pressed={activeView === "assistant"}
                onClick={onOpenAssistant}
              >
                <AssistantIcon />
              </button>
              <button
                type="button"
                className="sidebar-icon-btn"
                title="Settings"
                aria-label="Settings"
                onClick={onOpenSettings}
              >
                <SettingsIcon />
              </button>
            </>
          )}
          <UpdateButton />
        </div>
      </div>
      {pricePrompt ? (
        <BulkRegeneratePriceDialog
          listings={pricePrompt}
          onClose={() => setPricePrompt(null)}
          onConfirm={(drops) => {
            const rows = pricePrompt;
            setPricePrompt(null);
            setReviewPrompt({ rows, drops });
          }}
        />
      ) : null}
      {markdownIds ? (
        <BulkPriceDropDialog
          conversationIds={markdownIds}
          onClose={() => {
            setMarkdownIds(null);
            exitSelecting();
          }}
        />
      ) : null}
      {reviewPrompt ? (
        <RegenerateReviewDialog
          listings={reviewPrompt.rows.map((row) => ({ id: row.id, title: row.title }))}
          onClose={() => setReviewPrompt(null)}
          onRewrite={() => {
            const { rows, drops } = reviewPrompt;
            setReviewPrompt(null);
            void finishRegenerate(rows.map((row) => row.id), drops);
          }}
        />
      ) : null}
    </aside>
  );
}

function bulkStatusLabel(
  run: BulkRegenerateRun | null,
  selectedCount: number,
  eligibleCount: number,
): string {
  if (run) {
    if (run.mode === "generate") {
      if (run.cancelRequested) return `Finishing ${run.active.size} active listings…`;
      return `Generating ${run.active.size} listings · ${run.finished} of ${run.total} finished`;
    }
    const { title } = run;
    const place = `${run.index + 1} of ${run.total}`;
    if (run.cancelRequested) return `Finishing ${title}…`;
    if (run.phase === "dropping") return `Dropping the price on ${title} (${place})`;
    if (run.phase === "finishing") return `Finishing fields on ${title} (${place})`;
    return `Rewriting ${title} (${place})`;
  }
  if (eligibleCount === 0) return "No listings with photos to rewrite";
  return `${selectedCount} selected`;
}
