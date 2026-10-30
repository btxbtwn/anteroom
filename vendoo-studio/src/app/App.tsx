import { useWorkspaceRuntime } from "./runtime";
import { lazy, Suspense, useCallback, useEffect, useRef, useState, useReducer, type CSSProperties, type ReactNode } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { BUSY_POLL_MS, IDLE_POLL_MS, hasOpenJob, jobsPollMs, pollMs } from "../api/polling";
import type { BrowserField } from "../api/client";
import { useReloadStaleFrontend } from "../components/BuildVersion";
import { isChatToggleShortcut, ListingChatPopup, type ListingChatStatus } from "../components/ListingChatPopup";
import { ListingSession } from "../components/listingSession";
import { SetupChecklist } from "../components/SetupChecklist";
import { ListingSidebar } from "../components/ListingSidebar";
import { AssistantThreadPanel } from "../components/sidebar/AssistantThreadPanel";
import { SIDEBAR_FOOTER_WIDTH } from "../components/sidebar/SidebarFooter";
import { ArrowLeftIcon, MessagesSquareIcon, PlusIcon, SearchIcon, SquarePenIcon } from "lucide-react";
import {
  DEFAULT_SETTINGS_SECTION,
  SETTINGS_SECTION_LABELS,
  type SettingsSearchItem,
  type SettingsSectionId,
} from "../components/settingsNav";
import { ConfirmDialogHost } from "../components/ConfirmDialogHost";
import { PhotoDropOverlay } from "../components/PhotoDropOverlay";
import { BulkUploadDialog } from "../components/BulkUploadDialog";
import { useBulkRegenerate } from "../components/useBulkRegenerate";
import { bulkRegenerateToast } from "../bulkRegenerate";
import { PanelResizeHandle, usePanelCollapsed, usePanelWidth, type PanelWidthLimits } from "../components/PanelResizeHandle";
import { WorkspaceTopbar } from "../components/WorkspaceTopbar";
import { workspaceCrumbs, type WorkspaceView } from "../components/workspaceCrumbs";
import { SuggestionsPanel } from "../components/SuggestionsPanel";
import { isConfirmDialogOpen } from "../ui/confirmDialog";
import { dismissSetupGuide, isSetupGuideDismissed } from "../onboarding";
import { useVendooEnabled } from "../vendooMode";
import { toastManager } from "../components/ui/toast";
import { Button } from "../components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyTitle } from "../components/ui/empty";
import { Spinner } from "../components/ui/spinner";
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
import { MarketplaceSessionNotice } from "../components/settings/MarketplaceSessionNotice";

import { MobileNav, MobileNavButton, type MobileNavTarget } from "../components/MobileNav";
import { initialOpenListing, openListingReducer, type OpenListing } from "./openListing";
import { loadListingEditor, loadChatPanel, pageLoaders, preloadListingCode } from "./pageLoaders";
import { prefetchListingReads } from "../api/navigationQueries";

const ChatPanel = lazy(loadChatPanel);
const BrowserPreview = lazy(() =>
  import("../components/BrowserPreview").then((module) => ({ default: module.BrowserPreview })),
);
const ListingEditor = lazy(loadListingEditor);
const SettingsPage = lazy(pageLoaders.settings);
const AnalyticsPage = lazy(pageLoaders.analytics);
const AssistantPage = lazy(pageLoaders.assistant);
const MarketplacesPage = lazy(pageLoaders.marketplaces);
const UsagePage = lazy(pageLoaders.usage);
const AutomationsPage = lazy(pageLoaders.automations);
const SourcingPage = lazy(pageLoaders.sourcing);
// A page's code is fetched the first time it is opened. Fetching it once the
// app has settled means a tap does not wait on the network for it.
const PAGE_LOADERS = [...Object.values(pageLoaders), loadListingEditor, loadChatPanel];
const pageLoading = (
  <div className="flex flex-1 items-center justify-center" role="status" aria-label="Loading">
    <Spinner className="size-5 text-muted-foreground" />
  </div>
);
const FirstRunGuide = lazy(() =>
  import("../components/FirstRunGuide").then((module) => ({ default: module.FirstRunGuide })),
);

const ACTIVE_JOB_STATUSES = new Set(["queued", "awaiting_extension", "dispatched"]);
const MOBILE_LAYOUT_QUERY = "(max-width: 900px)";
/* Open wider for listing titles; resizing still stops at the footer's minimum width. */
const SIDEBAR_WIDTH: PanelWidthLimits = { min: SIDEBAR_FOOTER_WIDTH, max: 420, maxVw: 30 };
const THREADS_WIDTH: PanelWidthLimits = { min: 200, max: 420, maxVw: 30 };
const MAIN_PANEL_MIN_WIDTH = 360;
let setupGuideAutoOpen: boolean | null = null;

function useMobileLayout() {
  const [isMobile, setIsMobile] = useState(() => window.matchMedia(MOBILE_LAYOUT_QUERY).matches);
  useEffect(() => {
    const media = window.matchMedia(MOBILE_LAYOUT_QUERY);
    const onChange = () => setIsMobile(media.matches);
    onChange();
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);
  return isMobile;
}

export function App() {
  const runtime = useWorkspaceRuntime();
  const queryClient = useQueryClient();
  const vendooOn = useVendooEnabled();
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
  const [activeView, setActiveView] = useState<WorkspaceView>(listingFromUrl || runtime.hosted ? "listings" : "assistant");
  const [marketplace, setMarketplace] = useState<Marketplace>("ebay");
  /** Where Analytics, Settings and the rest return: the assistant is home, inventory is the other place. */
  const shell = useRef<"listings" | "assistant">(listingFromUrl || runtime.hosted ? "listings" : "assistant");
  const viewBeforeSettings = useRef<WorkspaceView>(listingFromUrl || runtime.hosted ? "listings" : "assistant");
  const [pickedThreadId, setPickedThreadId] = useState<string | null>(null);
  const [settingsSection, setSettingsSection] = useState<SettingsSectionId>(DEFAULT_SETTINGS_SECTION);
  const [settingsTargetId, setSettingsTargetId] = useState<string | null>(null);
  const [mobilePane, setMobilePane] = useState<"workspace" | "editor" | "browser">(runtime.hosted ? "editor" : "workspace");
  // On mobile the listings start open only when a listing link opened the app; home is the assistant.
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(() => listingFromUrl && window.matchMedia(MOBILE_LAYOUT_QUERY).matches);
  const [mobileThreadsOpen, setMobileThreadsOpen] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [listingQuery, setListingQuery] = useState("");
  const [setupGuideOpen, setSetupGuideOpen] = useState(setupGuideAutoOpen === true);
  const [browserJobId, setBrowserJobId] = useState<string | null>(null);
  const [browserFields, setBrowserFields] = useState<BrowserField[]>([]);
  const [browserExpanded, setBrowserExpanded] = useState(false);
  const [pendingBulkGroups, setPendingBulkGroups] = useState<PhotoFolderGroup[] | null>(null);
  const wasBrowserOpen = useRef(false);
  const mainPanelRef = useRef<HTMLElement>(null);
  const [sidebarWidth, setSidebarWidth] = usePanelWidth("sidebar");
  const [threadsWidth, setThreadsWidth] = usePanelWidth("threads");
  const [sidebarCollapsed, setSidebarCollapsed] = usePanelCollapsed("sidebar");
  // On desktop the listing's chat is a pop-up over the form; minimized, it is a pill.
  const [chatMinimized, setChatMinimized] = usePanelCollapsed("listing-chat");
  const [chatStatus, setChatStatus] = useState<ListingChatStatus | null>(null);
  const setChatOpen = useCallback((open: boolean) => setChatMinimized(!open), [setChatMinimized]);
  const [threadsCollapsed, setThreadsCollapsed] = usePanelCollapsed("threads");
  const panelWidthStyle = {
    // A width the seller dragged to wins, unless it would clip the footer buttons.
    "--sidebar-width": `${Math.max(sidebarWidth ?? 340, SIDEBAR_WIDTH.min)}px`,
    "--sidebar-min-width": `${SIDEBAR_WIDTH.min}px`,
    ...(threadsWidth ? { "--threads-width": `${threadsWidth}px` } : {}),
  } as CSSProperties;

  const { data: status } = useQuery({
    queryKey: ["status"],
    queryFn: api.status,
    refetchInterval: (query) => pollMs(query.state.data?.active_job_id ? BUSY_POLL_MS : IDLE_POLL_MS),
  });
  useReloadStaleFrontend(status?.version);
  // Re-reading the whole inventory is the heaviest request Studio makes, so it
  // only runs fast while a Vendoo send is moving rows. Studio's own edits
  // invalidate it; the slow pass picks up Vendoo's background syncs.
  const { data: conversations } = useQuery({
    queryKey: ["conversations"],
    queryFn: api.conversations.list,
    refetchInterval: (query) =>
      pollMs(
        status?.active_job_id || query.state.data?.some((c) => c.status === "listing")
          ? BUSY_POLL_MS
          : IDLE_POLL_MS,
      ),
  });
  const { data: jobs } = useQuery({
    queryKey: selectedConvId ? ["jobs", selectedConvId] : ["jobs"],
    queryFn: () => api.jobs.list(selectedConvId || undefined),
    refetchInterval: (query) => jobsPollMs(query.state.data),
  });
  const assistantThreads = useQuery({
    queryKey: ["assistant", "threads"],
    queryFn: api.assistant.threads,
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
    queryFn: api.jobs.queue,
    refetchInterval: (query) =>
      pollMs(hasOpenJob(query.state.data?.jobs) || query.state.data?.work.length ? BUSY_POLL_MS : IDLE_POLL_MS),
  });
  const inventoryLoaded = conversations !== undefined;
  useEffect(() => {
    if (!inventoryLoaded) return;
    let cancelled = false;
    let idleId: number | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const remaining = [...PAGE_LOADERS];
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
  }, [inventoryLoaded]);
  const previousQueueJobs = useRef<Map<string, string> | null>(null);
  useEffect(() => {
    if (!queue.data) return;
    const previous = previousQueueJobs.current;
    previousQueueJobs.current = new Map(queue.data.jobs.map((job) => [job.id, job.status]));
    if (!previous) return;
    for (const job of queue.data.jobs) {
      if (!ACTIVE_JOB_STATUSES.has(previous.get(job.id) || "") || ACTIVE_JOB_STATUSES.has(job.status)) continue;
      for (const key of ["jobs", "conversations", "conversation", "listing", "listing-fields", "fill-log", "vendoo-item"]) {
        void queryClient.invalidateQueries({ queryKey: [key] });
      }
      if (job.status === "completed" || job.status === "failed") {
        toastManager.add({
          type: job.status === "failed" ? "error" : "success",
          title: job.status === "failed"
            ? (vendooOn ? "Vendoo send failed" : "Send failed")
            : (vendooOn ? "Vendoo draft saved" : "Listing saved"),
          description: job.last_error || job.listing_title,
        });
      }
    }
  }, [queue.data, queryClient, vendooOn]);

  // Bound listings can change tab in Vendoo while the sidebar is open. A quiet
  // inventory pass refreshes draft/active/sold without importing photos — on
  // focus and whenever Chrome reconnects. Server debounce stops focus spam.
  const extensionConnected = Boolean(status?.extension_connected);
  useEffect(() => {
    if (!extensionConnected || !vendooOn) return undefined;
    const kick = () => {
      void api.imports.syncLabels().catch(() => {
        /* Chrome may still be pairing; next focus retries. */
      });
    };
    kick();
    const onFocus = () => kick();
    const onVisible = () => {
      if (document.visibilityState === "visible") kick();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [extensionConnected, vendooOn]);

  const providerConfigured = Boolean(status?.provider_configured);
  const needsSetup = !runtime.hosted && (!status || !status.provider_configured || !status.extension_connected);
  const createListingTitle = "New listing";
  const listingJob = jobs?.find((job) => job.conversation_id === selectedConvId && job.status !== "cancelled");
  const automationRunning = ACTIVE_JOB_STATUSES.has(String(listingJob?.status));
  // Sends run in the background; the seller opens the browser explicitly.
  const browserOpen = vendooOn && Boolean(browserJobId && listingJob?.id === browserJobId);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (!params.get("listing")) return;
    const url = new URL(window.location.href);
    url.searchParams.delete("listing");
    const next = url.pathname + url.search + url.hash;
    window.history.replaceState({}, "", next);
  }, []);

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
  const workspaceTitle = activeView === "settings"
    ? SETTINGS_SECTION_LABELS[settingsSection]
    : activeView === "analytics"
      ? "Analytics"
      : activeView === "marketplaces"
        ? marketplaceName(marketplace)
      : activeView === "usage"
        ? "Usage"
      : activeView === "automations"
        ? "Automations"
      : activeView === "assistant"
        ? (assistantThreads.data?.find((thread) => thread.id === assistantThreadId)?.title || "Assistant")
      : activeView === "sourcing"
        ? "Sourcing"
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
  const threadsHidden = !isMobile && threadsCollapsed;

  const closeMobileSidebar = useCallback(() => setMobileSidebarOpen(false), []);

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

  // Analytics, Marketplaces, Usage and Sourcing toggle back to whichever of home or inventory was open.
  const togglePage = (view: "analytics" | "marketplaces" | "usage" | "sourcing" | "automations") => {
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
  const openEbayInventoryLocation = () => {
    if (activeView !== "settings") viewBeforeSettings.current = activeView;
    setSettingsSection("connections");
    setSettingsTargetId("ebay-inventory-location");
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
  const openUsage = () => togglePage("usage");
  const openAutomations = () => togglePage("automations");
  const openSourcing = () => togglePage("sourcing");

  const openListing = useCallback((id: string) => {
    preloadListingCode();
    void prefetchListingReads(queryClient, id);
    setSelectedConvId(id);
    goListings();
    setMobilePane(runtime.hosted ? "editor" : "workspace");
    closeMobileSidebar();
  }, [setSelectedConvId, goListings, closeMobileSidebar, queryClient, runtime.hosted]);

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
    if (wasBrowserOpen.current && !browserOpen && mobilePane === "browser") {
      setMobilePane(runtime.hosted ? "editor" : "workspace");
    }
    wasBrowserOpen.current = browserOpen;
  }, [browserOpen, mobilePane, runtime.hosted]);

  const openBrowser = useMutation({
    mutationFn: (jobId: string) => api.jobs.browser.open(jobId),
    onMutate: (jobId: string) => {
      setBrowserJobId(jobId);
      setMobilePane("browser");
    },
    onSuccess: (result) => {
      if (result.warning) {
        toastManager.add({ type: "error", title: "Browser is view-only", description: result.warning });
      }
    },
    onError: (err: Error, jobId: string) => {
      setBrowserJobId((current) => (current === jobId ? null : current));
      toastManager.add({
        type: "error",
        title: vendooOn ? "Could not open the Vendoo draft" : "Could not open the draft",
        description: err.message,
      });
    },
  });

  const closeBrowser = () => {
    const jobId = browserJobId;
    setBrowserJobId(null);
    setBrowserFields([]);
    setBrowserExpanded(false);
    if (jobId) void api.jobs.browser.close(jobId).catch(() => {});
  };

  useEffect(() => {
    // A new Send replaces the listing job. Release the old draft tab.
    if (browserJobId && listingJob?.id && listingJob.id !== browserJobId) {
      setBrowserJobId(null);
      setBrowserFields([]);
      void api.jobs.browser.close(browserJobId).catch(() => {});
    }
  }, [browserJobId, listingJob?.id]);

  const browserJobRef = useRef<string | null>(null);
  browserJobRef.current = browserJobId;
  useEffect(() => () => {
    // Leaving a listing hands its Chrome tab back to the background window.
    const jobId = browserJobRef.current;
    if (jobId) {
      setBrowserJobId(null);
      setBrowserFields([]);
      void api.jobs.browser.close(jobId).catch(() => {});
    }
    setBrowserExpanded(false);
  }, [selectedConvId]);

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

  const showMailToolbar = isMobile && activeView === "listings" && mobileSidebarOpen;

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
      : null;

  const handleListingSearch = (value: string) => {
    setListingQuery(value);
    if (isMobile) setMobileSidebarOpen(true);
  };

  const createConv = useMutation({
    mutationFn: () => api.conversations.create({ title: "New Listing" }),
    onSuccess: (conv) => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
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
    if (bulk.running) {
      toastManager.add({
        type: "warning",
        title: "Anteroom is already working through a batch",
        description: "Generate these from the sidebar's Regenerate button when it finishes.",
      });
      return;
    }
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

  const cancelJob = useMutation({
    mutationFn: (jobId: string) => api.jobs.cancel(jobId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
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

  const renderChat = (listing: OpenListing) => !runtime.features.has("generation") ? null : (
    <Suspense fallback={pageLoading}>
      <ChatPanel
        convId={listing.id}
        queuedMessage={listing.queuedMessage}
        onQueuedMessageConsumed={() => dispatchOpenListing({ type: "message", id: listing.id, value: null })}
        browser={browserOpen && browserJobId ? { jobId: browserJobId, fields: browserFields } : null}
        onBrowserFieldsChange={setBrowserFields}
        onStatusChange={setChatStatus}
      />
    </Suspense>
  );
  const renderEditor = (listing: OpenListing, chat?: ReactNode) => (
    <Suspense fallback={null}>
      <ListingEditor
        convId={listing.id}
        reviewTab={listing.reviewTab}
        onReviewTabChange={(value) => dispatchOpenListing({ type: "review", id: listing.id, value })}
        onOpenBrowser={(jobId) => openBrowser.mutate(jobId)}
        browserOpen={browserOpen}
        onAskChat={(text) => {
          dispatchOpenListing({ type: "message", id: listing.id, value: text });
          setMobilePane(runtime.hosted ? "editor" : "workspace");
          setChatOpen(true);
        }}
        onCleared={() => {
          clearListingWorkspace(listing.id);
        }}
        onEditInventoryLocation={openEbayInventoryLocation}
        onBulkListingsCreated={(listings, generate) => {
          if (generate) void generateBatch(listings);
          const first = listings[0]?.convId;
          if (!first) return;
          setSelectedConvId(first);
          goListings();
          setMobilePane(runtime.hosted ? "editor" : "workspace");
          setMobileSidebarOpen(false);
        }}
        chat={runtime.features.has("generation") ? chat : undefined}
      />
    </Suspense>
  );

  return (
    <div className="app-shell" style={panelWidthStyle}>
      {runtime.features.has("marketplace_sessions") && <MarketplaceSessionNotice />}
      <WorkspaceTopbar
        accountControls={isMobile ? undefined : runtime.accountControls}
        crumbs={crumbs}
        sidebarOpen={!sidebarHidden}
        onToggleSidebar={() => setSidebarCollapsed(!sidebarCollapsed)}
        rightPanel={
          activeView === "assistant"
            ? {
                open: !threadsHidden,
                name: "chats",
                controls: "assistant-threads",
                onToggle: () => setThreadsCollapsed(!threadsCollapsed),
              }
            : null
        }
      />
      {runtime.hosted && isMobile && <div className="cloud-mobile-account">{runtime.accountControls}</div>}
      <div
        className={`app-content mobile-pane-${mobilePane}${mobileSidebarOpen ? " mobile-sidebar-open" : ""}${sidebarHidden ? " sidebar-collapsed" : ""}`}
      >
        {sidebarHidden ? null : (
          <ListingSidebar
            conversations={conversations}
            selectedConvId={selectedConvId}
            activeView={activeView}
            settingsSection={settingsSection}
            mobileOpen={!isMobile || mobileSidebarOpen}
            listingQuery={listingQuery}
            onSearchQueryChange={handleListingSearch}
            onSelect={openListing}
            onDelete={deleteListing}
            onCreate={createListing}
            creating={createConv.isPending}
            onOpenSettings={openSettings}
            onOpenUsage={openUsage}
            onOpenAnalytics={openAnalytics}
            onOpenMarketplaces={openMarketplaces}
            onOpenAutomations={openAutomations}
            onOpenAssistant={openAssistant}
            onOpenSourcing={openSourcing}
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
            {activeView === "assistant" ? (
              <button
                type="button"
                className="sidebar-icon-btn mobile-workspace-settings"
                title="Chats"
                aria-label={mobileThreadsOpen ? "Hide chats" : "Show chats"}
                aria-expanded={mobileThreadsOpen}
                aria-controls="assistant-threads"
                onClick={() => setMobileThreadsOpen((open) => !open)}
              >
                <MessagesSquareIcon className="size-4.5" />
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
                  onClick={() => setMobilePane("workspace")}
                >
                  Chat
                </button></>}
                <button
                  type="button"
                  role="tab"
                  aria-selected={mobilePane === "editor"}
                  className={mobilePane === "editor" ? "selected" : ""}
                  onClick={() => setMobilePane("editor")}
                >
                  Listing
                </button>
                {browserOpen ? (
                  <button
                    type="button"
                    role="tab"
                    aria-selected={mobilePane === "browser"}
                    className={mobilePane === "browser" ? "selected" : ""}
                    onClick={() => setMobilePane("browser")}
                  >
                    Browser
                  </button>
                ) : null}
              </div>
            ) : null}
          </header>
          <div className="workspace-body">
            <main className="panel main-panel" ref={mainPanelRef}>
              {activeView === "settings" ? (
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
              ) : activeView === "usage" ? (
                <Suspense fallback={pageLoading}>
                  <UsagePage />
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
                  />
                </Suspense>
              ) : activeView === "sourcing" ? (
                <Suspense fallback={pageLoading}>
                  <SourcingPage onOpenListing={openListing} />
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
                    <EmptyTitle>Turn product photos into marketplace-ready drafts</EmptyTitle>
                    <EmptyDescription>Pick up where Anteroom suggests, or start a new listing.</EmptyDescription>
                  </EmptyHeader>
                  <EmptyContent className="max-w-md">
                    {runtime.features.has("generation") && <SuggestionsPanel
                      variant="workspace"
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
              {selectedConvId && (
                <div hidden={activeView !== "listings"} className={`listing-workspace${browserOpen && browserExpanded ? " is-browser-expanded" : ""}`}>
                  {browserOpen && (
                    <Suspense fallback={pageLoading}>
                      <BrowserPreview
                        jobId={listingJob?.id ?? null}
                        step={listingJob?.current_step}
                        status={listingJob?.status}
                        vendooItemId={listingJob?.vendoo_item_id}
                        vendooUrl={listingJob?.vendoo_url}
                        cancelling={cancelJob.isPending}
                        onCancel={listingJob?.id ? () => cancelJob.mutate(listingJob.id) : undefined}
                        interactive={browserOpen}
                        mobile={isMobile}
                        automationRunning={automationRunning}
                        onClose={closeBrowser}
                        expanded={browserExpanded}
                        onToggleExpanded={isMobile ? undefined : () => setBrowserExpanded((value) => !value)}
                        selected={browserFields}
                        onSelectedChange={setBrowserFields}
                        onGoToChat={isMobile ? () => setMobilePane("workspace") : undefined}
                      />
                    </Suspense>
                  )}
                  {openListingState && (
                    <div className="listing-workspace-main listing-session" key={`${openListingState.id}:${openListingState.nonce}`}>
                      <ListingSession active={activeView === "listings"}>
                        {isMobile ? (
                          <div className="chat-column">{renderChat(openListingState)}</div>
                        ) : (
                          renderEditor(openListingState, (
                            <ListingChatPopup
                              convId={openListingState.id}
                              open={!chatMinimized}
                              onOpenChange={setChatOpen}
                              status={chatStatus}
                            >
                              {renderChat(openListingState)}
                            </ListingChatPopup>
                          ))
                        )}
                      </ListingSession>
                    </div>
                  )}
                </div>
              )}
            </main>

            {/* On a phone the form is its own pane; on desktop it is the main panel. */}
            {isMobile && (openListingState || activeView === "listings") && (
              <aside id="listing-inspector" className="panel detail-panel" hidden={activeView !== "listings"}>
                {openListingState ? (
                  <div className="listing-session" key={`${openListingState.id}:${openListingState.nonce}`}>
                    <ListingSession active={activeView === "listings"}>
                      {renderEditor(openListingState)}
                    </ListingSession>
                  </div>
                ) : (
                  <Empty className="h-full">
                    <EmptyDescription>Select a listing to inspect</EmptyDescription>
                  </Empty>
                )}
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
            <input
              type="search"
              value={listingQuery}
              onChange={(e) => handleListingSearch(e.target.value)}
              placeholder="Search"
              aria-label="Search listings"
            />
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
