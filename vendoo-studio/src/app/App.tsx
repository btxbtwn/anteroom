import { useWorkspaceRuntime } from "./runtime";
import { useMobileLayout } from "../lib/useMobileLayout";
import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useReducer, type CSSProperties } from "react";
import { discardBlankDraft, discardBlankDraftOnExit } from "./blankDraft";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { refreshInventory } from "../api/inventoryRefresh";
import { BUSY_POLL_MS, IDLE_POLL_MS, hasOpenJob, inventoryPollMs, pollMs } from "../api/polling";
import { useReloadStaleFrontend } from "../components/BuildVersion";
import { isChatToggleShortcut } from "../components/ListingChatPopup";
import { ListingSession } from "../components/listingSession";
import { SetupChecklist } from "../components/SetupChecklist";
import { ListingSidebar } from "../components/ListingSidebar";
import { AssistantThreadPanel } from "../components/sidebar/AssistantThreadPanel";
import { SIDEBAR_FOOTER_WIDTH } from "../components/sidebar/SidebarFooter";
import { ArrowLeftIcon, HistoryIcon, PlusIcon, SearchIcon, SquarePenIcon } from "lucide-react";
import {
  DEFAULT_SETTINGS_SECTION,
  SETTINGS_SECTION_LABELS,
  type SettingsSearchItem,
  type SettingsSectionId,
} from "../components/settingsNav";
import { ConfirmDialogHost } from "../components/ConfirmDialogHost";
import { PhotoDropOverlay } from "../components/PhotoDropOverlay";
import { canAddPhotos, DRAFT_ONLY_PHOTOS } from "../components/photoAdding";
import { BulkUploadDialog } from "../components/BulkUploadDialog";
import { useBulkRegenerate } from "../components/useBulkRegenerate";
import { bulkRegenerateToast } from "../bulkRegenerate";
import { PanelResizeHandle, usePanelCollapsed, usePanelWidth, type PanelWidthLimits } from "../components/PanelResizeHandle";
import { WorkspaceTopbar, stopTitlebarDrag } from "../components/WorkspaceTopbar";
import { MarketplaceActivity } from "../components/MarketplaceActivity";
import { workspaceCrumbs, type WorkspaceView } from "../components/workspaceCrumbs";
import { SuggestionsPage } from "../components/SuggestionsPage";
import { SuggestionsPanel } from "../components/SuggestionsPanel";
import { isConfirmDialogOpen } from "../ui/confirmDialog";
import { dismissSetupGuide, isSetupGuideDismissed } from "../onboarding";
import { toastManager } from "../components/ui/toast";
import { takeMarketplaceSignIn } from "../lib/marketplaceSignIn";
import { Button } from "../components/ui/button";
import { Empty, EmptyContent, EmptyHeader, EmptyTitle } from "../components/ui/empty";
import {
  groupImageFilesByFolder,
  listingTitleForFolder,
  type PhotoFolderGroup,
} from "../photoDrop";
import {
  createBulkPhotoListings,
  type BulkListingUploadResult,
  type BulkUploadDefaults,
  type BulkUploadItem,
} from "../bulkPhotoUpload";
import type { Marketplace } from "../components/MarketplacesPage";
import { marketplaceName } from "../components/marketplaceNames";
import { ThemeSync } from "../components/ThemeSync";
import { inventoryEmptyTitle } from "./inventoryEmpty";
import { MarketplaceSessionNotice } from "../components/settings/MarketplaceSessionNotice";

import { MobileNav, MobileNavButton, type MobileNavTarget } from "../components/MobileNav";
import type { ListingDocument } from "../api/types";
import { initialOpenListing, openListingReducer, phonePaneOnOpen } from "./openListing";
import {
  AnalyticsPage, AssistantPage, AutomationsPage, InventoryTablePage, MarketplacesPage, SettingsPage, SourcingPage,
  loadListingEditor, loadChatPanel, pageLoaders, preloadListingCode,
} from "./pageLoaders";
import { ListingChatPane, ListingEditorPane, pageLoading, type ListingPaneActions } from "./ListingPanes";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { setListingSearch, useListingSearch } from "../components/sidebar/listingSearch";
import { prefetchListingReads } from "../api/navigationQueries";
import { prefetchPageReads } from "../api/pageQueries";

// A page's code is fetched the first time it is opened. Fetching it once the
// app has settled means a tap does not wait on the network for it.
const PAGE_LOADERS = [...Object.values(pageLoaders), loadListingEditor, loadChatPanel];
const FirstRunGuide = lazy(() =>
  import("../components/FirstRunGuide").then((module) => ({ default: module.FirstRunGuide })),
);

const ACTIVE_JOB_STATUSES = new Set(["queued", "awaiting_extension", "dispatched"]);
/* Open wider for listing titles; resizing still stops at the footer's minimum width. */
const SIDEBAR_WIDTH: PanelWidthLimits = { min: SIDEBAR_FOOTER_WIDTH, max: 420, maxVw: 30 };
const THREADS_WIDTH: PanelWidthLimits = { min: 200, max: 420, maxVw: 30 };
const MAIN_PANEL_MIN_WIDTH = 360;
let setupGuideAutoOpen: boolean | null = null;


export function App() {
  const runtime = useWorkspaceRuntime();
  const queryClient = useQueryClient();
  const isMobile = useMobileLayout();
  const [openListingState, dispatchOpenListing] = useReducer(
    openListingReducer,
    new URLSearchParams(window.location.search).get("listing") || null,
    initialOpenListing,
  );
  const selectedConvId = openListingState?.id ?? null;
  const setSelectedConvId = useCallback((id: string) => dispatchOpenListing({ type: "open", id }), []);
  const clearListingWorkspace = (id: string) => dispatchOpenListing({ type: "clear", id });
  const listingFromUrl = Boolean(new URLSearchParams(window.location.search).get("listing"));
  const [suggestionsVisited, setSuggestionsVisited] = useState(false);
  const [returnToSuggestions, setReturnToSuggestions] = useState(false);
  // The assistant is home, on the Mac and signed in alike; a listing link opens that listing.
  const home = listingFromUrl || !runtime.views.has("assistant") ? "listings" : "assistant";
  const [activeView, setActiveView] = useState<WorkspaceView>(home);
  const [marketplace, setMarketplace] = useState<Marketplace>("ebay");
  /** Where Analytics, Settings and the rest return: the assistant is home, inventory is the other place. */
  const shell = useRef<"listings" | "assistant">(home);
  const viewBeforeSettings = useRef<WorkspaceView>(home);
  const [pickedThreadId, setPickedThreadId] = useState<string | null>(null);
  const [settingsSection, setSettingsSection] = useState<SettingsSectionId>(DEFAULT_SETTINGS_SECTION);
  const [settingsTargetId, setSettingsTargetId] = useState<string | null>(null);
  const [mobilePane, setMobilePane] = useState<"workspace" | "editor">(runtime.hosted ? "editor" : "workspace");
  /** A late listing read must not override a newer pane choice. */
  const paneChoice = useRef(0);
  // A listing link opens the listing itself on phones, with inventory closed.
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const phoneInventory = isMobile && activeView === "listings" && mobileSidebarOpen;
  const inventoryScroll = useRef(0);
  useLayoutEffect(() => {
    if (!phoneInventory) return;
    window.scrollTo(0, inventoryScroll.current);
    const remember = () => { inventoryScroll.current = window.scrollY; };
    window.addEventListener("scroll", remember, { passive: true });
    return () => window.removeEventListener("scroll", remember);
  }, [phoneInventory]);
  const [mobileThreadsOpen, setMobileThreadsOpen] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [setupGuideOpen, setSetupGuideOpen] = useState(setupGuideAutoOpen === true);
  const [pendingBulkGroups, setPendingBulkGroups] = useState<PhotoFolderGroup[] | null>(null);
  const mainPanelRef = useRef<HTMLElement>(null);
  const [sidebarWidth, setSidebarWidth] = usePanelWidth("sidebar");
  const [threadsWidth, setThreadsWidth] = usePanelWidth("threads");
  const [sidebarCollapsed, setSidebarCollapsed] = usePanelCollapsed("sidebar");
  // On desktop the listing's chat is a column beside the form, or a pop-up over it when narrow; minimized, it is a pill.
  const [chatMinimized, setChatMinimized] = usePanelCollapsed("listing-chat");
  const setChatOpen = useCallback((open: boolean) => setChatMinimized(!open), [setChatMinimized]);
  // Chat history opens from the History button and starts closed, as a side sheet does.
  const [threadsCollapsed, setThreadsCollapsed] = usePanelCollapsed("assistant-history", true);
  const panelWidthStyle = {
    // A width the seller dragged to wins, unless it would clip the footer buttons.
    "--sidebar-width": `${Math.max(sidebarWidth ?? 340, SIDEBAR_WIDTH.min)}px`,
    "--sidebar-min-width": `${SIDEBAR_WIDTH.min}px`,
    ...(threadsWidth ? { "--threads-width": `${threadsWidth}px` } : {}),
  } as CSSProperties;

  const { data: status } = useQuery({
    queryKey: ["status"],
    queryFn: ({ signal }) => api.status({ signal }),
    refetchInterval: (query) => pollMs(query.state.data?.active_job_id ? BUSY_POLL_MS : IDLE_POLL_MS),
  });
  useReloadStaleFrontend(status?.version);
  // Re-reading the whole inventory is the heaviest request Studio makes.
  // Studio's own edits and finished sends (the queue watcher below) refresh it;
  // the poll only backs those up. See inventoryPollMs.
  const inventory = useQuery({
    queryKey: ["conversations"],
    queryFn: ({ signal }) => api.conversations.list({ signal }),
    refetchInterval: (query) => inventoryPollMs(query.state.data),
  });
  const conversations = inventory.data;
  const inventoryFailed = inventory.isError && conversations === undefined;
  const assistantThreads = useQuery({
    queryKey: ["assistant", "threads"],
    queryFn: ({ signal }) => api.assistant.threads({ signal }),
    enabled: runtime.features.has("assistant"),
  });
  // A blank chat is the home screen. A thread opens only when the seller picks one or sends the first message.
  const assistantThreadId = pickedThreadId && (
    assistantThreads.data?.some((thread) => thread.id === pickedThreadId) ?? !assistantThreads.isFetched
  )
    ? pickedThreadId
    : null;
  const queue = useQuery({
    queryKey: ["queue"],
    queryFn: ({ signal }) => api.jobs.queue({ signal }),
    refetchInterval: (query) =>
      pollMs(hasOpenJob(query.state.data?.jobs) || query.state.data?.work.length ? BUSY_POLL_MS : IDLE_POLL_MS),
  });
  const inventoryLoaded = conversations !== undefined;
  // The runtime object is rebuilt on every render; its names are what matter here.
  const pageViews = [...runtime.views].join(" ");
  const pageFeatures = [...runtime.features].join(" ");
  useEffect(() => {
    if (!inventoryLoaded) return;
    let cancelled = false;
    let idleId: number | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    // The pages' first reads go first, so their data is in before a tap
    // opens them; the page code then loads behind them.
    const readPages = () => { void prefetchPageReads(queryClient, { views: new Set(pageViews.split(" ")), features: new Set(pageFeatures.split(" ")) }).catch(() => {}); return Promise.resolve(); };
    const remaining = [readPages, ...PAGE_LOADERS];
    const schedule = () => {
      if (cancelled || !remaining.length) return;
      const next = () => {
        if (cancelled) return;
        const load = remaining.shift()!;
        void load().catch(() => {}).then(schedule);
      };
      // Warm one page per idle turn so parsing every page cannot crowd out
      // the first interaction or compete with the page the seller opens.
      if (typeof window.requestIdleCallback === "function") {
        idleId = window.requestIdleCallback(next);
      } else {
        timer = setTimeout(next, 200);
      }
    };
    schedule();
    return () => {
      cancelled = true;
      if (idleId !== undefined) window.cancelIdleCallback(idleId);
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [inventoryLoaded, queryClient, pageViews, pageFeatures]);
  const previousQueueJobs = useRef<Map<string, string> | null>(null);
  useEffect(() => {
    if (!queue.data) return;
    const previous = previousQueueJobs.current;
    previousQueueJobs.current = new Map(queue.data.jobs.map((job) => [job.id, job.status]));
    if (!previous) return;
    for (const job of queue.data.jobs) {
      if (!ACTIVE_JOB_STATUSES.has(previous.get(job.id) || "") || ACTIVE_JOB_STATUSES.has(job.status)) continue;
      for (const key of ["jobs", "conversation", "listing", "listing-fields", "fill-log"]) {
        void queryClient.invalidateQueries({ queryKey: [key] });
      }
      refreshInventory(queryClient);
      if (job.status === "completed" || job.status === "failed") {
        toastManager.add({
          type: job.status === "failed" ? "error" : "success",
          title: job.status === "failed"
            ? "Send failed"
            : "Listing saved",
          description: job.last_error || job.listing_title,
        });
      }
    }
  }, [queue.data, queryClient]);

  const providerConfigured = Boolean(status?.provider_configured);
  const needsSetup = !runtime.hosted && (!status || !status.provider_configured || !status.extension_connected);
  const createListingTitle = "New listing";

  useEffect(() => {
    const signIn = takeMarketplaceSignIn();
    if (!signIn) return;
    const name = signIn.marketplace === "ebay" ? "eBay" : "Etsy";
    void api.directConnections.finish(signIn.marketplace, signIn.state, signIn.claim)
      .then((done) => toastManager.add({ type: "success", title: `${name} is connected`, description: done.label ?? undefined }))
      .catch((error: unknown) => toastManager.add({
        type: "error", title: `${name} was not connected`, description: error instanceof Error ? error.message : undefined,
      }))
      .finally(() => void queryClient.invalidateQueries({ queryKey: ["direct-connections"] }));
  }, [queryClient]);

  useEffect(() => {
    const url = new URL(window.location.href);
    if (activeView === "listings" && selectedConvId) {
      url.searchParams.set("listing", selectedConvId);
    } else {
      url.searchParams.delete("listing");
    }
    const next = url.pathname + url.search + url.hash;
    window.history.replaceState(window.history.state, "", next);
  }, [activeView, selectedConvId]);

  useEffect(() => {
    if (setupGuideAutoOpen !== null) return;
    if (isSetupGuideDismissed()) {
      setupGuideAutoOpen = false;
      dismissSetupGuide();
      return;
    }
    if (!status) return;
    if (status.setup_guide_dismissed || status.provider_configured || (status.conversations ?? 0) > 0) {
      setupGuideAutoOpen = false;
      dismissSetupGuide();
      return;
    }
    setupGuideAutoOpen = true;
    setSetupGuideOpen(true);
    dismissSetupGuide();
  }, [status]);

  const selectedListing = conversations?.find((listing: { id: string }) => listing.id === selectedConvId);
  const workspaceTitle = activeView === "suggestions" ? "Suggestions" : activeView === "settings"
    ? SETTINGS_SECTION_LABELS[settingsSection]
    : activeView === "analytics"
      ? "Analytics"
      : activeView === "marketplaces"
        ? marketplaceName(marketplace)
      : activeView === "automations"
        ? "Automations"
      : activeView === "assistant"
        ? (assistantThreads.data?.find((thread) => thread.id === assistantThreadId)?.title || "Assistant")
      : activeView === "sourcing"
        ? "Sourcing"
      : activeView === "inventory"
        ? "Inventory"
        : String(selectedListing?.title || "Anteroom");
  const assistantThreadTitle = assistantThreads.data?.find((thread) => thread.id === assistantThreadId)?.title ?? null;
  const crumbs = workspaceCrumbs(
    activeView,
    SETTINGS_SECTION_LABELS[settingsSection],
    activeView === "listings" ? selectedListing : null,
    activeView === "assistant" ? assistantThreadTitle : activeView === "marketplaces" ? marketplaceName(marketplace) : null,
  );
  // The sidebar overlays the workspace on mobile and the editor is its own pane
  // there, so the desktop collapse states only apply to the desktop layout.
  const sidebarHidden = !isMobile && sidebarCollapsed;
  // Nothing to show until the first chat: no button, no empty column.
  const hasChats = Boolean(assistantThreads.data?.length);
  const threadsHidden = !isMobile && (threadsCollapsed || !hasChats);
  const showHistory = () => (isMobile ? setMobileThreadsOpen(true) : setThreadsCollapsed(false));

  const closeMobileSidebar = useCallback(() => setMobileSidebarOpen(false), []);
  // On a phone, Inventory with no listing open is the list itself, never an empty inspector.
  const mobileListOpen = isMobile && (mobileSidebarOpen || (activeView === "listings" && !selectedConvId));

  const goListings = useCallback(() => {
    shell.current = "listings";
    setActiveView("listings");
  }, []);
  const goAssistant = () => {
    if (!runtime.views.has("assistant")) return;
    shell.current = "assistant";
    setActiveView("assistant");
    setMobilePane(runtime.hosted ? "editor" : "workspace");
    setMobileThreadsOpen(false);
    closeMobileSidebar();
  };

  const openSettings = () => {
    if (!runtime.views.has("settings")) return;
    if (activeView !== "settings") viewBeforeSettings.current = activeView;
    setSettingsSection(DEFAULT_SETTINGS_SECTION);
    setSettingsTargetId(null);
    setActiveView("settings");
    setMobilePane(runtime.hosted ? "editor" : "workspace");
    // On mobile, land on the settings section list first so Providers etc. are reachable.
    setMobileSidebarOpen(isMobile);
  };

  const closeSettings = () => {
    const back = viewBeforeSettings.current;
    setActiveView(back === "settings" ? shell.current : back);
    setSettingsTargetId(null);
    closeMobileSidebar();
  };

  // Analytics, Marketplaces, Automations and Sourcing toggle back to whichever of home or inventory was open.
  const togglePage = (view: "analytics" | "marketplaces" | "sourcing" | "automations") => {
    if (!runtime.views.has(view)) return;
    setMobilePane(runtime.hosted ? "editor" : "workspace");
    if (activeView === view) {
      setActiveView(shell.current);
      return;
    }
    setActiveView(view);
    closeMobileSidebar();
  };
  const openAnalytics = () => togglePage("analytics");
  const openMarketplaces = () => togglePage("marketplaces");
  const openAssistant = () => goAssistant();
  const openShipsFromZip = () => {
    if (activeView !== "settings") viewBeforeSettings.current = activeView;
    setSettingsSection("listings");
    setSettingsTargetId("ships-from-zip");
    setActiveView("settings");
    setMobilePane(runtime.hosted ? "editor" : "workspace");
    closeMobileSidebar();
  };
  const openProviders = () => {
    if (!runtime.views.has("settings")) return;
    if (activeView !== "settings") viewBeforeSettings.current = activeView;
    setSettingsSection("providers");
    setSettingsTargetId(null);
    setActiveView("settings");
    setMobilePane(runtime.hosted ? "editor" : "workspace");
    closeMobileSidebar();
  };
  const openAutomations = () => togglePage("automations");
  // The table is the inventory's other view; it returns to the list it came from.
  const toggleInventoryTable = () => {
    if (!runtime.views.has("listings")) return;
    setMobilePane(runtime.hosted ? "editor" : "workspace");
    closeMobileSidebar();
    if (activeView === "inventory") goListings();
    else setActiveView("inventory");
  };
  const openSourcing = () => togglePage("sourcing");

  const openListing = useCallback((id: string, tab?: string) => {
    setReturnToSuggestions(false);
    preloadListingCode();
    void prefetchListingReads(queryClient, id);
    dispatchOpenListing({ type: "open", id, tab });
    goListings();
    const row = conversations?.find((item) => item.id === id);
    const cached = queryClient.getQueryData<ListingDocument>(["listing", id]);
    const pane = runtime.hosted ? "editor" : phonePaneOnOpen(row, cached);
    const choice = ++paneChoice.current;
    setMobilePane(pane);
    // A draft not read yet may already have copy: once its listing loads, move
    // to the form, unless the seller picked a tab or another listing first.
    if (pane === "workspace" && !cached) {
      void queryClient.fetchQuery({ queryKey: ["listing", id], queryFn: ({ signal }) => api.listings.get(id, { signal }), retry: false })
        .then((listing) => {
          if (paneChoice.current === choice && phonePaneOnOpen(row, listing) === "editor") setMobilePane("editor");
        }, () => {});
    }
    closeMobileSidebar();
  }, [goListings, closeMobileSidebar, queryClient, runtime.hosted, conversations]);

  const openSuggestions = () => {
    if (!runtime.features.has("suggestions")) return;
    setSuggestionsVisited(true);
    setActiveView("suggestions");
    setMobilePane("workspace");
    closeMobileSidebar();
  };
  const openSuggestedListing = (id: string) => {
    openListing(id);
    setReturnToSuggestions(true);
  };

  const handleSettingsSectionChange = (section: SettingsSectionId) => {
    setSettingsSection(section);
    setSettingsTargetId(null);
    closeMobileSidebar();
  };

  const handleSettingsSearchResult = (item: SettingsSearchItem) => {
    setSettingsSection(item.section);
    setSettingsTargetId(item.targetId || item.id);
    closeMobileSidebar();
  };

  useEffect(() => {
    if (!isMobile) setMobileSidebarOpen(false);
  }, [isMobile]);

  useEffect(() => {
    if (!isMobile || !mobileSidebarOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMobileSidebarOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isMobile, mobileSidebarOpen]);

  useEffect(() => {
    if (activeView === "listings" || activeView === "assistant") return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented || isConfirmDialogOpen()) return;
      if (isMobile && mobileSidebarOpen) return;
      const target = event.target;
      if (
        target instanceof HTMLElement &&
        (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)
      ) {
        return;
      }
      if (activeView === "settings") closeSettings();
      else setActiveView(shell.current);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [activeView, isMobile, mobileSidebarOpen]);

  useEffect(() => {
    if (isMobile || activeView !== "listings" || !selectedConvId) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (!isChatToggleShortcut(event) || isConfirmDialogOpen()) return;
      event.preventDefault();
      setChatMinimized(!chatMinimized);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isMobile, activeView, selectedConvId, chatMinimized, setChatMinimized]);

  const showMailToolbar = activeView === "listings" && mobileListOpen;

  /** Phone pages open from the navigation drawer; a listing and a settings section step back to their list. */
  const navigateMobile = (target: MobileNavTarget) => {
    if (!runtime.views.has(target)) return;
    setMobilePane(runtime.hosted ? "editor" : "workspace");
    setMobileThreadsOpen(false);
    if (target === "assistant") goAssistant();
    else if (target === "settings") openSettings();
    else if (target === "listings") {
      goListings();
      setMobileSidebarOpen(true);
    } else {
      setActiveView(target);
      closeMobileSidebar();
    }
  };
  const mobileBack = activeView === "listings"
    ? { label: "Inventory", onClick: () => setMobileSidebarOpen(true) }
    : activeView === "settings"
      ? { label: "Settings", onClick: () => setMobileSidebarOpen(true) }
    : activeView === "inventory"
      ? { label: "Inventory", onClick: () => { goListings(); setMobileSidebarOpen(true); } }
      : null;

  const openSidebarForSearch = useCallback(() => {
    if (isMobile) setMobileSidebarOpen(true);
  }, [isMobile]);

  // The draft New listing just made, until the seller moves on from it. Left
  // without a photo or a typed word, it is discarded (see blankDraft.ts).
  const freshDraftId = useRef<string | null>(null);
  const discardFreshDraft = useCallback((id: string) => {
    void discardBlankDraft(id, api.conversations.discardBlank).then((discarded) => {
      if (discarded) void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    });
  }, [queryClient]);
  useEffect(() => {
    const fresh = freshDraftId.current;
    if (!fresh || fresh === selectedConvId) return;
    freshDraftId.current = null;
    discardFreshDraft(fresh);
  }, [selectedConvId, discardFreshDraft]);
  useEffect(() => {
    // Registered after the unsaved-work guard, so a typed field has already started saving.
    const onPageHide = (event: PageTransitionEvent) => {
      const fresh = freshDraftId.current;
      if (!fresh || event.persisted) return;
      if (discardBlankDraftOnExit(fresh, (id) => api.conversations.discardBlank(id, { keepalive: true }))) {
        freshDraftId.current = null;
      }
    };
    window.addEventListener("pagehide", onPageHide);
    return () => window.removeEventListener("pagehide", onPageHide);
  }, []);

  const createConv = useMutation({
    mutationFn: () => api.conversations.create({ title: "New Listing" }),
    onSuccess: (conv) => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      const previous = freshDraftId.current;
      freshDraftId.current = conv.id;
      if (previous && previous !== conv.id) discardFreshDraft(previous);
      setSelectedConvId(conv.id);
      goListings();
      setMobilePane(runtime.hosted ? "editor" : "workspace");
      setMobileSidebarOpen(false);
    },
  });
  const createListing = () => {
    if (createConv.isPending) return false;
    createConv.mutate();
    return true;
  };

  const bulk = useBulkRegenerate((id) => activeView === "listings" && selectedConvId === id);
  // Bulk-uploaded drafts already carry their SKUs and measurements, so they generate straight away.
  const generateBatch = async (uploaded: BulkListingUploadResult[]) => {
    if (!runtime.features.has("generation")) return;
    const listings = uploaded
      .filter((listing) => listing.count > 0)
      .map((listing) => ({ convId: listing.convId, title: listingTitleForFolder(listing.folder) }));
    if (!listings.length) return;
    const ids = listings.map((listing) => listing.convId);
    const titles = new Map(listings.map((listing) => [listing.convId, listing.title]));
    const result = await bulk.start(ids, titles, "generate");
    if (result) toastManager.add(bulkRegenerateToast(result, "generate"));
  };

  // Photos dropped anywhere in the window land on the open listing; with no
  // listing open the drop starts one, the same as "New listing" then Add Photos.
  // Multiple folders (Finder multi-select or a parent of item folders) each
  // become their own draft — a bulk upload — instead of one mixed listing.
  const dropPhotos = useMutation({
    mutationFn: async (
      upload:
        | { files: File[] }
        | { items: BulkUploadItem[]; bulkDefaults: BulkUploadDefaults; generate: boolean },
    ) => {
      if ("items" in upload) {
        const listings = await createBulkPhotoListings(upload.items, upload.bulkDefaults, api.conversations);
        return { mode: "bulk" as const, listings, generate: upload.generate };
      }

      const { files } = upload;
      const groups = groupImageFilesByFolder(files);
      const only = groups[0]?.files || files;
      const openConvId = selectedConvId;
      const title =
        !openConvId && groups[0]?.folder
          ? listingTitleForFolder(groups[0].folder)
          : "New Listing";
      const convId = openConvId || (await api.conversations.create({ title })).id;
      const result = await api.conversations.uploadPhotos(convId, only);
      return {
        mode: "single" as const,
        convId,
        created: !openConvId,
        count: result.count,
        errors: result.errors || [],
      };
    },
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      if (result.mode === "bulk") {
        for (const listing of result.listings) {
          queryClient.invalidateQueries({ queryKey: ["photos", listing.convId] });
        }
        const first = result.listings[0];
        if (first) {
          setSelectedConvId(first.convId);
          setMobileSidebarOpen(false);
          goListings();
          setMobilePane(runtime.hosted ? "editor" : "workspace");
        }
        const totalPhotos = result.listings.reduce((sum, row) => sum + row.count, 0);
        const errors = result.listings.flatMap((row) => row.errors);
        const title = `Started ${result.listings.length} listings`;
        const description = `Added ${totalPhotos} photo${totalPhotos === 1 ? "" : "s"} from separate folders.`;
        if (result.generate) void generateBatch(result.listings);
        if (errors.length) {
          toastManager.add({ type: "error", title, description: errors.join("; ") });
          return;
        }
        toastManager.add({ type: "success", title, description });
        return;
      }

      const { convId, created, count, errors } = result;
      queryClient.invalidateQueries({ queryKey: ["photos", convId] });
      if (created) {
        setSelectedConvId(convId);
        setMobileSidebarOpen(false);
      }
      if (created || activeView !== "listings") {
        goListings();
        setMobilePane(runtime.hosted ? "editor" : "workspace");
      }
      const title = `Added ${count} photo${count === 1 ? "" : "s"}`;
      if (errors.length) {
        toastManager.add({ type: "error", title, description: errors.join("; ") });
        return;
      }
      toastManager.add({ type: "success", title, description: created ? "Started a new listing for them." : undefined });
    },
    onError: (err: Error) => {
      toastManager.add({ type: "error", title: "Could not add photos", description: err.message });
    },
  });

  const { mutate: deleteConversation } = useMutation({
    mutationFn: (convId: string) => api.conversations.delete(convId),
    onSuccess: (_data, convId) => {
      dispatchOpenListing({ type: "close", id: convId });
      if (selectedConvId === convId) {
        setMobilePane(runtime.hosted ? "editor" : "workspace");
        if (isMobile) setMobileSidebarOpen(true);
      }
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });

  const deleteListing = useCallback((id: string) => deleteConversation(id), [deleteConversation]);

  // The open listing's panes get one stable set of actions; each call reads
  // the workspace as it is now, so the panes need not re-render when it changes.
  const paneHandlers = useRef<Omit<ListingPaneActions, "dispatch"> | null>(null);
  useLayoutEffect(() => {
    paneHandlers.current = {
      askChat: (id, text) => {
        dispatchOpenListing({ type: "message", id, value: text });
        setMobilePane(runtime.hosted ? "editor" : "workspace");
        setChatOpen(true);
      },
      cleared: clearListingWorkspace,
      editInventoryLocation: openShipsFromZip,
      bulkCreated: (listings, generate) => {
        if (generate) void generateBatch(listings);
        const first = listings[0]?.convId;
        if (!first) return;
        setSelectedConvId(first);
        goListings();
        setMobilePane(runtime.hosted ? "editor" : "workspace");
        setMobileSidebarOpen(false);
      },
    };
  });
  const paneActions = useMemo<ListingPaneActions>(() => ({
    dispatch: dispatchOpenListing,
    askChat: (id, text) => paneHandlers.current?.askChat(id, text),
    cleared: (id) => paneHandlers.current?.cleared(id),
    editInventoryLocation: () => paneHandlers.current?.editInventoryLocation(),
    bulkCreated: (listings, generate) => paneHandlers.current?.bulkCreated(listings, generate),
  }), []);
  const generation = runtime.features.has("generation");
  const activity = runtime.actionMarketplaces.size > 0
    ? <MarketplaceActivity jobs={queue.data?.jobs} checkedAt={queue.dataUpdatedAt} onOpen={openListing} />
    : null;

  return (
    <div className={`app-shell${phoneInventory ? " phone-inventory" : ""}`} style={panelWidthStyle}>
      {runtime.features.has("marketplace_sessions") && <MarketplaceSessionNotice />}
      <WorkspaceTopbar
        accountControls={isMobile ? undefined : runtime.accountControls}
        activity={isMobile ? undefined : activity}
        crumbs={crumbs}
        sidebarOpen={!sidebarHidden}
        onToggleSidebar={() => setSidebarCollapsed(!sidebarCollapsed)}
        rightPanel={
          activeView === "assistant" && hasChats
            ? {
                open: !threadsHidden,
                name: "chat history",
                controls: "assistant-threads",
                onToggle: () => setThreadsCollapsed(!threadsCollapsed),
                icon: <HistoryIcon className="size-4" />,
                actions: (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="[-webkit-app-region:no-drag]"
                    disabled={!assistantThreadId}
                    onMouseDown={stopTitlebarDrag}
                    onClick={() => setPickedThreadId(null)}
                  >
                    <SquarePenIcon />
                    New chat
                  </Button>
                ),
              }
            : null
        }
      />
      {/* One account menu, always mounted: it holds the Chrome relay. On phones CSS
          places it at the end of the page header row, which leaves room for it. */}
      {runtime.hosted && isMobile && <div className="cloud-mobile-account">{runtime.accountControls}</div>}
      <div
        className={`app-content mobile-pane-${activeView === "listings" ? mobilePane : "workspace"}${mobileListOpen ? " mobile-sidebar-open" : ""}${sidebarHidden ? " sidebar-collapsed" : ""}`}
      >
        {sidebarHidden ? null : (
          <ListingSidebar
            conversations={conversations}
            loadFailed={inventoryFailed}
            selectedConvId={selectedConvId}
            activeView={activeView}
            settingsSection={settingsSection}
            mobileOpen={!isMobile || mobileListOpen}
            onSearch={openSidebarForSearch}
            onSelect={openListing}
            onDelete={deleteListing}
            onCreate={createListing}
            creating={createConv.isPending}
            onOpenSettings={openSettings}
            onOpenAnalytics={openAnalytics}
            onOpenMarketplaces={openMarketplaces}
            onOpenAutomations={openAutomations}
            onOpenAssistant={openAssistant}
            onOpenSourcing={openSourcing}
            onOpenSuggestions={openSuggestions}
            onToggleInventoryTable={toggleInventoryTable}
            onOpenMobileNav={() => setMobileNavOpen(true)}
            onCloseSettings={closeSettings}
            onSettingsSectionChange={handleSettingsSectionChange}
            onSettingsSearchResult={handleSettingsSearchResult}
            bulk={bulk}
          />
        )}
        {sidebarHidden ? null : (
          <PanelResizeHandle
            label="Resize listings sidebar"
            panel="before"
            width={sidebarWidth}
            onResize={setSidebarWidth}
            absorberRef={mainPanelRef}
            absorberMin={MAIN_PANEL_MIN_WIDTH}
            {...SIDEBAR_WIDTH}
          />
        )}

        <div className="workspace-frame">
          <header className="mobile-workspace-bar">
            {mobileBack ? (
              <button
                type="button"
                className="sidebar-icon-btn sidebar-toggle"
                aria-label={mobileBack.label}
                title={mobileBack.label}
                onClick={mobileBack.onClick}
              >
                <ArrowLeftIcon className="size-4.5" />
              </button>
            ) : (
              <MobileNavButton onClick={() => setMobileNavOpen(true)} />
            )}
            <div className="mobile-workspace-title" data-mobile-title>{workspaceTitle}</div>
            {isMobile && activity}
            {activeView === "assistant" && hasChats ? (
              <button
                type="button"
                className="sidebar-icon-btn mobile-workspace-settings"
                title="Chat history"
                aria-label={mobileThreadsOpen ? "Hide chat history" : "Show chat history"}
                aria-expanded={mobileThreadsOpen}
                aria-controls="assistant-threads"
                onClick={() => setMobileThreadsOpen((open) => !open)}
              >
                <HistoryIcon className="size-4.5" />
              </button>
            ) : null}
            {activeView === "listings" && selectedConvId ? (
              <div className="mobile-workspace-panes" role="tablist" aria-label="Listing views">
                {runtime.features.has("generation") && <>
                <button
                  type="button"
                  role="tab"
                  aria-selected={mobilePane === "workspace"}
                  className={mobilePane === "workspace" ? "selected" : ""}
                  onClick={() => { paneChoice.current++; setMobilePane("workspace"); }}
                >
                  Chat
                </button></>}
                <button
                  type="button"
                  role="tab"
                  aria-selected={mobilePane === "editor"}
                  className={mobilePane === "editor" ? "selected" : ""}
                  onClick={() => { paneChoice.current++; setMobilePane("editor"); }}
                >
                  Listing
                </button>
              </div>
            ) : null}
          </header>
          {isMobile && activeView === "listings" && returnToSuggestions && (
            <div className="shrink-0 border-b border-border px-3 py-2">
              <Button variant="ghost" size="sm" className="min-h-11" onClick={openSuggestions}><ArrowLeftIcon /> Back to suggestions</Button>
            </div>
          )}
          <div className="workspace-body">
            <main className="panel main-panel" ref={mainPanelRef}>
              {!isMobile && activeView === "listings" && returnToSuggestions && (
                <div className="shrink-0 border-b border-border px-3 py-2">
                  <Button variant="ghost" size="sm" onClick={openSuggestions}><ArrowLeftIcon /> Back to suggestions</Button>
                </div>
              )}
              <ErrorBoundary label="This page" resetKey={activeView === "settings" ? `settings:${settingsSection}` : activeView}>
                {activeView === "suggestions" ? null : activeView === "settings" ? (
                  <Suspense fallback={pageLoading}>
                    <SettingsPage
                      onOpenListing={openListing}
                      section={settingsSection}
                      targetId={settingsTargetId}
                      onTargetHandled={() => setSettingsTargetId(null)}
                      onOpenSetupGuide={() => setSetupGuideOpen(true)}
                    />
                  </Suspense>
                ) : activeView === "analytics" ? (
                  <Suspense fallback={pageLoading}>
                    <AnalyticsPage onOpenListing={openListing} />
                  </Suspense>
                ) : activeView === "marketplaces" ? (
                  <Suspense fallback={pageLoading}>
                    <MarketplacesPage marketplace={marketplace} onMarketplaceChange={setMarketplace} onOpenListing={openListing} />
                  </Suspense>
                ) : activeView === "automations" ? (
                  <Suspense fallback={pageLoading}>
                    <AutomationsPage onOpenListing={openListing} />
                  </Suspense>
                ) : activeView === "assistant" ? (
                  <Suspense fallback={pageLoading}>
                    <AssistantPage
                      onOpenProviders={openProviders}
                      onOpenListing={openListing}
                      threadId={assistantThreadId}
                      onThreadReady={setPickedThreadId}
                      onShowHistory={showHistory}
                    />
                  </Suspense>
                ) : activeView === "sourcing" ? (
                  <Suspense fallback={pageLoading}>
                    <SourcingPage onOpenListing={openListing} />
                  </Suspense>
                ) : activeView === "inventory" ? (
                  <Suspense fallback={pageLoading}>
                    <InventoryTablePage
                      conversations={conversations}
                      loadFailed={inventoryFailed}
                      onOpenListing={openListing}
                      sidebarSearch={!isMobile && !sidebarHidden}
                    />
                  </Suspense>
                ) : selectedConvId ? null : needsSetup ? (
                  <SetupChecklist
                    providerConfigured={providerConfigured}
                    chromeAvailable={status?.chrome_available !== false}
                    extensionConnected={Boolean(status?.extension_connected)}
                    creating={createConv.isPending}
                    onOpenSettings={openSettings}
                    onStartGuide={() => setSetupGuideOpen(true)}
                    onCreate={createListing}
                  />
                ) : (
                  <Empty className="flex-1 overflow-y-auto">
                    <EmptyHeader>
                      <EmptyTitle>{inventoryEmptyTitle(conversations?.length)}</EmptyTitle>
                    </EmptyHeader>
                    <EmptyContent className="max-w-md">
                      {runtime.features.has("suggestions") && <SuggestionsPanel
                        selectedConvId={selectedConvId}
                        onSelect={openListing}
                      />}
                      <Button
                        type="button"
                        size="sm"
                        disabled={createConv.isPending}
                        title={createListingTitle}
                        onClick={createListing}
                      >
                        <PlusIcon />
                        Create a listing
                      </Button>
                    </EmptyContent>
                  </Empty>
                )}
              </ErrorBoundary>
              {suggestionsVisited && <SuggestionsPage active={activeView === "suggestions"} onOpenListing={openSuggestedListing} />}
              {selectedConvId && (
                <div hidden={activeView !== "listings"} className="listing-workspace">
                  {openListingState && (
                    <div className="listing-workspace-main listing-session" key={`${openListingState.id}:${openListingState.nonce}`}>
                      <ListingSession active={activeView === "listings"}>
                        {isMobile ? (
                          <div className="chat-column">
                            {generation && <ListingChatPane listing={openListingState} actions={paneActions} />}
                          </div>
                        ) : (
                          <ListingEditorPane
                            listing={openListingState}
                            actions={paneActions}
                            chatOpen={generation ? !chatMinimized : undefined}
                            onChatOpenChange={generation ? setChatOpen : undefined}
                          />
                        )}
                      </ListingSession>
                    </div>
                  )}
                </div>
              )}
            </main>

            {/* On a phone the form is its own pane; on desktop it is the main panel. */}
            {/* With no listing open, a phone shows the inventory list instead (mobileListOpen). */}
            {isMobile && openListingState && (
              <aside id="listing-inspector" className="panel detail-panel" hidden={activeView !== "listings"}>
                <div className="listing-session" key={`${openListingState.id}:${openListingState.nonce}`}>
                  <ListingSession active={activeView === "listings"}>
                    <ListingEditorPane listing={openListingState} actions={paneActions} />
                  </ListingSession>
                </div>
              </aside>
            )}

            {activeView === "assistant" && !threadsHidden && !isMobile && (
              <PanelResizeHandle
                label="Resize chats"
                panel="after"
                width={threadsWidth}
                onResize={setThreadsWidth}
                absorberRef={mainPanelRef}
                absorberMin={MAIN_PANEL_MIN_WIDTH}
                {...THREADS_WIDTH}
              />
            )}
            {activeView === "assistant" && !threadsHidden && (
              <AssistantThreadPanel
                selectedId={assistantThreadId}
                onSelect={setPickedThreadId}
                mobileOpen={isMobile ? mobileThreadsOpen : undefined}
                onCloseMobile={() => setMobileThreadsOpen(false)}
              />
            )}
          </div>
        </div>
      </div>

      {showMailToolbar && (
        <nav className="mobile-mail-toolbar" aria-label="Search and create listings">
          <label className="mobile-mail-search">
            <SearchIcon className="size-4.5 shrink-0" />
            <MobileListingSearch />
          </label>
          <button
            type="button"
            className="mobile-mail-btn"
            title={createListingTitle}
            aria-label={createListingTitle}
            disabled={createConv.isPending}
            onClick={createListing}
          >
            <SquarePenIcon className="size-5" />
          </button>
        </nav>
      )}
      <PhotoDropOverlay
        title={
          selectedConvId
            ? `Drop photos into ${String(selectedListing?.title || "this listing")}`
            : "Drop photos to start a new listing"
        }
        hint="Drop several folders for one draft each · JPG, PNG, WEBP or HEIC · up to 20 per listing"
        busy={dropPhotos.isPending}
        busyLabel="Adding photos…"
        onFiles={(files) => {
          const groups = groupImageFilesByFolder(files);
          if (groups.length > 1) {
            setPendingBulkGroups(groups);
            return;
          }
          if (selectedConvId && !canAddPhotos(runtime.hosted, selectedListing?.status)) {
            toastManager.add({ type: "info", title: "Photos not added", description: DRAFT_ONLY_PHOTOS });
            return;
          }
          dropPhotos.mutate({ files });
        }}
      />
      {pendingBulkGroups ? (
        <BulkUploadDialog
          groups={pendingBulkGroups}
          onCancel={() => setPendingBulkGroups(null)}
          onConfirm={(bulkDefaults, items, generate) => {
            setPendingBulkGroups(null);
            dropPhotos.mutate({ items, bulkDefaults, generate });
          }}
        />
      ) : null}
      {isMobile ? (
        <MobileNav
          open={mobileNavOpen}
          onOpenChange={setMobileNavOpen}
          activeView={activeView}
          onNavigate={navigateMobile}
        />
      ) : null}
      <ThemeSync />
      <ConfirmDialogHost />
      {setupGuideOpen ? (
        <Suspense fallback={null}>
          <FirstRunGuide
            providerConfigured={providerConfigured}
            chromeAvailable={status?.chrome_available !== false}
            extensionConnected={Boolean(status?.extension_connected)}
            creating={createConv.isPending}
            onClose={() => setSetupGuideOpen(false)}
            onCreateListing={createListing}
          />
        </Suspense>
      ) : null}
    </div>
  );
}

/** The phone toolbar's search box; it shares the sidebar's search text. */
function MobileListingSearch() {
  const query = useListingSearch();
  return (
    <input
      type="search"
      value={query}
      onChange={(e) => setListingSearch(e.target.value)}
      placeholder="Search"
      aria-label="Search listings"
    />
  );
}
