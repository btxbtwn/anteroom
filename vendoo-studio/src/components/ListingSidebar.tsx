import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { listingCanRegenerate, mergeRegenerateIds } from "../bulkRegenerate";
import { jobsPollMs } from "../api/polling";
import type { SettingsSearchItem, SettingsSectionId } from "./settingsNav";
import { ListingFilters } from "./ListingFilters";
import { SuggestionsPanel } from "./SuggestionsPanel";
import type { BulkRegenerate } from "./useBulkRegenerate";
import { MobileNavButton } from "./MobileNav";
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
import { SearchIcon, XIcon } from "lucide-react";
import { cn } from "../lib/utils";
import { Kbd } from "./ui/kbd";
import { SIDEBAR_ROW } from "./sidebar/sidebarStyles";
import { ListingRow } from "./sidebar/ListingRow";
import { activeAnchorMs, settledAnchorMs, type Listing } from "./sidebar/listingShelves";
import { BulkActionBar, BulkSelectionDialogs } from "./sidebar/BulkSelection";
import { SettingsSidebarNav } from "./sidebar/SettingsSidebarNav";
import { SidebarFooter } from "./sidebar/SidebarFooter";
import { useListingSelection } from "./sidebar/useListingSelection";
import { useSettingsSearch } from "./sidebar/useSettingsSearch";

/* T3 Code's sidebar search row (MIT, (c) 2026 T3 Tools Inc.): a flush row that
   lights up on hover and focus, with an unstyled input inside. */
const SEARCH_CLASS =
  "flex h-8 min-w-0 flex-1 cursor-text items-center gap-2 rounded-[var(--control-radius)] px-2 text-sidebar-muted-foreground transition-colors hover:bg-sidebar-row-hover focus-within:bg-sidebar-row-hover";
const SEARCH_INPUT_CLASS =
  "min-w-0 flex-1 bg-transparent text-sm font-medium text-sidebar-foreground outline-none placeholder:text-sidebar-muted-foreground max-[900px]:text-base";

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
  onOpenInventory: () => void;
  onOpenSourcing: () => void;
  /** Phones: opens the page drawer from the sidebar header. */
  onOpenMobileNav?: () => void;
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
  onOpenInventory,
  onOpenSourcing,
  onOpenMobileNav,
  onOpenQueue,
  queueCount,
  onCloseSettings,
  onSettingsSectionChange,
  onSettingsSearchResult,
  bulk,
}: Props) {
  const [filters, setFilters] = useState<Filters>(DEFAULT_LISTING_FILTERS);
  const settingsMode = activeView === "settings";

  const { data: uiPrefs } = useQuery({
    queryKey: ["settings-ui"],
    queryFn: api.settings.ui,
  });
  const nameBySku = uiPrefs?.listing_names === "sku";

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

  // Finished listings stay out of the working list. Search and the Sold tab still find them.
  const shownListings = useMemo(() => {
    const includeFinished = Boolean(needle) || filters.status === "sold";
    const rows = includeFinished ? [...activeListings, ...settledListings] : [...activeListings];
    if (activeView === "listings" && selectedConvId && !rows.some((listing) => listing.id === selectedConvId)) {
      const open = settledListings.find((listing) => listing.id === selectedConvId);
      if (open) rows.unshift(open);
    }
    return rows;
  }, [activeListings, activeView, filters.status, needle, selectedConvId, settledListings]);

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
      shownListings
        .map((listing) => jobIdByConversation.get(listing.id))
        .filter((id): id is string => Boolean(id))
        .sort(),
    [shownListings, jobIdByConversation],
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
  };

  const handleFiltersChange = (next: Filters) => {
    setFilters(next);
  };

  return (
    <aside
      id="listings-sidebar"
      data-app-sidebar
      className={`panel sidebar${settingsMode ? " sidebar-settings" : ""}`}
      aria-label={settingsMode ? "Settings" : "Listings"}
      aria-hidden={mobileOpen === false ? true : undefined}
    >
      {/* Phones only: the desktop sidebar has no header of its own. */}
      <div className="sidebar-header pywebview-drag-region">
        {onOpenMobileNav ? <MobileNavButton onClick={onOpenMobileNav} /> : null}
        <h1 className="sidebar-mobile-title">{settingsMode ? "Settings" : "Inventory"}</h1>
        {!settingsMode ? (
          <button
            type="button"
            className="sidebar-select-btn"
            data-active={selecting}
            aria-pressed={selecting}
            aria-label={selecting ? "Stop selecting" : "Select listings to regenerate"}
            disabled={bulk.running}
            onClick={() => {
              if (selecting) exitSelecting();
              else setSelecting(true);
            }}
          >
            {selecting ? "Done" : "Select"}
          </button>
        ) : null}
      </div>

      {settingsMode ? (
        <>
        <div className="flex shrink-0 items-center gap-1 px-(--sidebar-content-inset) py-2">
          <label className={SEARCH_CLASS}>
            <SearchIcon className="size-4 shrink-0 text-(--sidebar-icon-color)" />
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
              className={SEARCH_INPUT_CLASS}
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
                className="inline-flex size-5 shrink-0 cursor-pointer items-center justify-center rounded-sm text-sidebar-muted-foreground hover:text-sidebar-foreground"
                aria-label="Clear search"
                onClick={() => {
                  clearSettingsSearch();
                  settingsSearchRef.current?.focus();
                }}
              >
                <XIcon className="size-3.5" />
              </button>
            ) : (
              <Kbd>/</Kbd>
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
          <div className="flex shrink-0 items-center gap-1 px-(--sidebar-content-inset) py-2 max-[900px]:hidden">
            <label className={SEARCH_CLASS}>
              <SearchIcon className="size-4 shrink-0 text-(--sidebar-icon-color)" />
              <input
                type="search"
                value={listingQuery}
                onChange={(e) => handleSearchChange(e.target.value)}
                className={SEARCH_INPUT_CLASS}
                placeholder="Search by title, SKU or label"
                aria-label="Search listings by title, SKU or label"
              />
            </label>
            <button
              type="button"
              data-active={selecting}
              className={cn(SIDEBAR_ROW, "w-auto shrink-0 px-2 text-xs font-semibold")}
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

          <div
            className={cn(
              "min-h-0 flex-1 overflow-y-auto overscroll-contain px-(--sidebar-content-inset) pb-2",
              "max-[900px]:flex-none max-[900px]:overflow-visible max-[900px]:pb-[calc(72px+env(safe-area-inset-bottom,0px))]",
            )}
          >
            {shownListings.map((listing) => (
              <ListingRow
                key={listing.id}
                listing={listing}
                selected={selectedConvId === listing.id && activeView === "listings"}
                settled={false}
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
              />
            ))}

            {(!conversations || conversations.length === 0) && (
              <p className="px-2.5 py-6 text-center text-xs text-sidebar-muted-foreground">No listings yet</p>
            )}
            {conversations && conversations.length > 0 && activeListings.length === 0 && settledListings.length === 0 && (
              <p className="px-2.5 py-6 text-center text-xs text-sidebar-muted-foreground">No matching listings</p>
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
        onOpenInventory={onOpenInventory}
        onOpenSettings={onOpenSettings}
      />
      <BulkSelectionDialogs selection={selection} />
    </aside>
  );
}
