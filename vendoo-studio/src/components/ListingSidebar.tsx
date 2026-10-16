import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { listingCanRegenerate, mergeRegenerateIds } from "../bulkRegenerate";
import { jobsPollMs } from "../api/polling";
import type { SettingsSearchItem, SettingsSectionId } from "./settingsNav";
import { ListingFilters } from "./ListingFilters";
import { SuggestionsPanel } from "./SuggestionsPanel";
import type { BulkRegenerate } from "./useBulkRegenerate";
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
import { BackIcon, SearchIcon } from "./sidebar/SidebarIcons";
import { ListingRow } from "./sidebar/ListingRow";
import { activeAnchorMs, settledAnchorMs, type Listing } from "./sidebar/listingShelves";
import { BulkActionBar, BulkSelectionDialogs } from "./sidebar/BulkSelection";
import { SettingsSidebarNav } from "./sidebar/SettingsSidebarNav";
import { SidebarFooter } from "./sidebar/SidebarFooter";
import { useListingSelection } from "./sidebar/useListingSelection";
import { useSettledShelf } from "./sidebar/useSettledShelf";
import { useSettingsSearch } from "./sidebar/useSettingsSearch";

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
  const [settledVisibleCount, setSettledVisibleCount] = useState(SETTLED_TAIL_INITIAL_COUNT);
  const [filters, setFilters] = useState<Filters>(DEFAULT_LISTING_FILTERS);
  const settingsMode = activeView === "settings";

  const { data: uiPrefs } = useQuery({
    queryKey: ["settings-ui"],
    queryFn: api.settings.ui,
  });
  const nameBySku = uiPrefs?.listing_names === "sku";
  const { settledExpanded, toggleSettledShelf } = useSettledShelf(uiPrefs);

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
  const selection = useListingSelection({ eligibleIds, listingsById, suggestions, bulk });
  const { selecting, setSelecting, selectedIds, exitSelecting, toggleSelect } = selection;

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
  const settingsSearch = useSettingsSearch(settingsMode, onSettingsSearchResult);
  const {
    settingsSearchRef,
    settingsQuery,
    setSettingsQuery,
    activeResultIndex,
    setActiveResultIndex,
    settingsResults,
    isSettingsSearching,
    clearSettingsSearch,
    handleSettingsSearchKeyDown,
  } = settingsSearch;

  const handleSearchChange = (value: string) => {
    onSearchQueryChange(value);
    setSettledVisibleCount(SETTLED_TAIL_INITIAL_COUNT);
  };

  const handleFiltersChange = (next: Filters) => {
    setFilters(next);
    setSettledVisibleCount(SETTLED_TAIL_INITIAL_COUNT);
  };

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

          <SettingsSidebarNav
            search={settingsSearch}
            settingsSection={settingsSection}
            onSettingsSectionChange={onSettingsSectionChange}
          />
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
        <BulkActionBar bulk={bulk} selection={selection} eligibleIds={eligibleIds} />
      )}

      <SidebarFooter
        settingsMode={settingsMode}
        activeView={activeView}
        queueCount={queueCount}
        onCloseSettings={onCloseSettings}
        onOpenQueue={onOpenQueue}
        onOpenSourcing={onOpenSourcing}
        onOpenAnalytics={onOpenAnalytics}
        onOpenMarketing={onOpenMarketing}
        onOpenAssistant={onOpenAssistant}
        onOpenSettings={onOpenSettings}
      />
      <BulkSelectionDialogs selection={selection} />
    </aside>
  );
}
