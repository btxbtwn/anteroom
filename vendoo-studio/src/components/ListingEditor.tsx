import React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Job, ListingData, MarketplaceForm } from "../api/types";
import { cloneListing, expandJoinedChoices, formFieldText, formFieldValues, getListingEditorValue, nextFieldDraft, setListingEditorValue, squashFieldKey } from "../listingPaths";
import { PhotoTray } from "./PhotoTray";
import { ItemDetails } from "./ItemDetails";
import { ConnectChromeButton } from "./ConnectChromeButton";
import { SendProgress, sendProgressLabel } from "./SendProgress";
import { ListingBlockers } from "./ListingBlockers";
import { ListingHistory } from "./ListingHistory";
import { blockerTarget, isEbayInventoryLocationEdit, matchingEditorField } from "./listingChanges";
import { ListingSyncStatus } from "./ListingSyncStatus";
import { OpenListingButton } from "./OpenListingButton";
import { SoldElsewhereNotice } from "./SoldElsewhereNotice";
import { MarketplaceLogo } from "./MarketplaceLogo";
import { marketplaceName } from "./marketplaceNames";
import {
  joinMarketplaces,
  marketplacesNeedingRelist,
  relistCallout,
  relistStage,
} from "./relistStatus";
import { ETSY_CATEGORY_OPTIONALS } from "../marketplaceFields";
import { depopSizeGroupingApplies } from "../marketplaceFields";
import { withDropdownOptions, withSchemaOptions } from "../dropdownOptions";
import { applyBottomsFields, isBottomsListing } from "../bottomsSizes";
import { activityPollMs, EXTENSION_STATUS_POLL_MS, fillLogPollMs, jobsPollMs, pollMs } from "../api/polling";
import { toastManager } from "./ui/toast";
import { MarketplacesLayout } from "./MarketplacesSection";
import { confirmDialog } from "../ui/confirmDialog";
import { useChatBusy } from "./chat/liveChatStore";
import { fetchVendooItemLive, VENDOO_ITEM_STALE_MS, vendooItemQueryKey } from "../api/vendooItemQuery";
import {
  CopyableLlmError,
  jobErrorPrompt,
  validationErrorsPrompt,
} from "./CopyableLlmError";
import { askChatGapsPrompt } from "./fillLog/chatPrompts";
import { mergeDraftItem } from "./fillLog/draftForms";
import { normalizeFieldName } from "./fillLog/fieldKeys";
import { askChatTargetCount } from "./fillLog/fieldSync";
import { fillFailureEntries } from "./fillLog/fillStatus";
import {
  emptyHiddenFields,
  hiddenFieldKey,
  hiddenKeySet,
  withoutHiddenFields,
} from "./fillLog/formFilters";
import { sourceFormsForJob } from "./fillLog/sourceForms";
import { ListingBrowserButton, ListingReviewActions } from "./ListingReviewActions";
import { type ListingReviewTab } from "./ListingReviewTabs";
import type { BulkListingUploadResult } from "../bulkPhotoUpload";
import { HistoryIcon, Link2Icon, RotateCcwIcon } from "lucide-react";
import { cn } from "../lib/utils";
import { EbayCategorySelect, FieldLabel, InspectorSection, SearchableSelect, SearchableMultiSelect } from "./ListingFormControls";
import { groupFieldsIntoSections } from "./listingFieldSections";
import { Alert, AlertDescription, AlertTitle } from "./ui/alert";
import { Badge } from "./ui/badge";
import { Button, InlineButton } from "./ui/button";
import { Input } from "./ui/input";
import { Spinner } from "./ui/spinner";
import { Switch } from "./ui/switch";
import { Textarea } from "./ui/textarea";

interface Props {
  convId: string;
  onAskChat?: (text: string) => void;
  onCleared?: () => void;
  onOpenBrowser?: (jobId: string) => void;
  browserOpen?: boolean;
  reviewTab: ListingReviewTab;
  onReviewTabChange: (tab: ListingReviewTab) => void;
  onBulkListingsCreated?: (listings: BulkListingUploadResult[], generate: boolean) => void;
  onEditInventoryLocation?: () => void;
}

const DIRECT_FORM_MARKETS = new Set(["ebay", "poshmark", "mercari", "depop", "sellwild"]);

interface EditorField {
  key: string;
  label: string;
  type?: string;
  defaultValue?: string | string[] | number | boolean;
  native?: boolean;
  optionValues?: Record<string, string>;
  clears?: string[];
  help?: string;
  valueConstraints?: Record<string, { parentName: string; parentValues: string[] }[]>;
  required?: boolean;
  options?: string[];
  locked?: boolean;
  multi?: boolean;
  selectionOnly?: boolean;
  maxValues?: number | null;
  maxLength?: number | null;
  optionsSource?: "marketplace" | "vendoo" | "reference" | "saved";
}

export function ListingEditor({
  convId,
  onAskChat,
  onCleared,
  onOpenBrowser,
  browserOpen,
  reviewTab,
  onReviewTabChange,
  onBulkListingsCreated,
  onEditInventoryLocation,
}: Props) {
  const queryClient = useQueryClient();
  const [editTab, setEditTab] = React.useState("general");
  const [historyOpen, setHistoryOpen] = React.useState(false);
  const [fieldTarget, setFieldTarget] = React.useState<{ field: string; sequence: number } | null>(null);
  const editorRef = React.useRef<HTMLDivElement>(null);
  const { data: jobs, isLoading: jobsLoading } = useQuery({
    queryKey: ["jobs", convId],
    queryFn: () => api.jobs.list(convId),
    refetchInterval: (query) => jobsPollMs(query.state.data),
  });
  const { data: photos, isLoading: photosLoading } = useQuery({
    queryKey: ["photos", convId],
    queryFn: () => api.conversations.photos(convId),
  });
  const listingJob = jobs?.find((j) => j.conversation_id === convId && j.status !== "cancelled" && j.mode !== "native_marketplace");
  const schemaProbeActive = Boolean(
    listingJob?.mode === "schema_probe"
    && ["queued", "awaiting_extension", "dispatched"].includes(String(listingJob?.status || "")),
  );
  const discardUnsent = useMutation({
    mutationFn: () => api.vendooApi.pull(convId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["listing", convId] });
      queryClient.invalidateQueries({ queryKey: ["listing-fields", convId] });
      queryClient.invalidateQueries({ queryKey: ["conversation", convId] });
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      queryClient.invalidateQueries({ queryKey: ["vendoo-item"] });
      queryClient.invalidateQueries({ queryKey: ["vendoo-item-peek"] });
      queryClient.invalidateQueries({ queryKey: ["vendoo-sync", convId] });
      toastManager.add({ type: "success", title: "Back to the Vendoo listing" });
    },
    onError: (err: Error) => {
      toastManager.add({
        type: "error",
        title: "Could not pull from Vendoo",
        description: err.message || "Is Chrome connected?",
      });
    },
  });
  async function confirmDiscardUnsent() {
    const drafts = await queryClient
      .fetchQuery({
        queryKey: ["native-marketplaces", convId],
        queryFn: () => api.nativeMarketplaces.status(convId),
      })
      .catch(() => undefined);
    const live = Object.entries(drafts ?? {})
      .filter(([, draft]) => draft?.state === "published")
      .map(([marketplace]) => marketplaceName(marketplace));
    const liveNote = live.length
      ? ` This listing is live on ${live.join(", ")} from Anteroom. Discarding won't change those listings, so Anteroom will no longer match them.`
      : "";
    const confirmed = await confirmDialog(
      `Discard unsent edits?\nThis listing goes back to what Vendoo has. Edits and anything regenerated since the last send are replaced.${liveNote}`,
      { variant: "destructive", confirmLabel: "Discard edits" },
    );
    if (confirmed) discardUnsent.mutate();
  }
  const ensureDraftMutation = useMutation({
    mutationFn: async (opts?: { importDraft?: boolean }) => {
      const job = await api.jobs.ensureDraft(convId);
      // Live API read stays inside the mutation so attach waits until the draft is loaded.
      // Importing needs blob: preview photos resolved and uploaded by the extension first,
      // since only the Vendoo tab itself can read a blob: URL.
      const fresh = await fetchVendooItemLive(queryClient, job.id, {
        force: true,
        resolvePhotos: Boolean(opts?.importDraft),
      });
      // Linking adopts the draft; plain attach must not overwrite Studio edits.
      const imported = opts?.importDraft ? await api.jobs.importDraft(job.id) : null;
      return { job, fresh, imported };
    },
    onSuccess: ({ job, fresh, imported }) => {
      queryClient.setQueryData(["jobs", convId], (old: Job[] | undefined) => {
        const rest = (old || []).filter((item) => item.id !== job.id);
        return [job, ...rest];
      });
      queryClient.invalidateQueries({ queryKey: ["jobs", convId] });
      queryClient.invalidateQueries({ queryKey: ["conversation", convId] });
      queryClient.invalidateQueries({ queryKey: ["listing", convId] });
      queryClient.invalidateQueries({ queryKey: ["fill-log"] });
      if (imported) {
        queryClient.invalidateQueries({ queryKey: ["photos", convId] });
        toastManager.add({
          type: imported.photo_warnings.length ? "error" : "success",
          title: `Imported Vendoo draft with ${imported.photo_count} photo${imported.photo_count === 1 ? "" : "s"}`,
          description: imported.photo_warnings.join(" ") || imported.listing_title,
        });
      }
      if (fresh?.error || fresh?.api_error) {
        toastManager.add({
          type: "error",
          title: "Could not fully refresh Vendoo draft",
          description: String(fresh.error || fresh.api_error),
        });
      }
    },
    onError: (err: Error) => {
      // Remount on Regenerate/Clear aborts an in-flight refresh; that is not a failure.
      const name = err?.name || "";
      if (name === "AbortError" || name === "CancelledError") return;
      toastManager.add({
        type: "error",
        title: "Could not refresh fields",
        description: err.message || "Could not attach this listing to its Vendoo draft.",
      });
    },
  });

  const { data } = useQuery({
    queryKey: ["listing", convId],
    queryFn: () => api.listings.get(convId),
    refetchInterval: schemaProbeActive ? 3000 : false,
  });

  const { data: uiPrefs } = useQuery({
    queryKey: ["settings-ui"],
    queryFn: api.settings.ui,
  });
  const vendooEnabled = uiPrefs?.vendoo_enabled ?? true;
  const { data: marketplaceSettings } = useQuery({
    queryKey: ["settings-marketplaces"],
    queryFn: api.settings.marketplaces,
  });
  const { data: conversation } = useQuery({
    queryKey: ["conversation", convId],
    queryFn: () => api.conversations.get(convId),
  });

  const updateMutation = useMutation({
    mutationFn: (listing: ListingData) => api.listings.update(convId, listing),
    onSuccess: (_result, listing) => {
      queryClient.invalidateQueries({ queryKey: ["listing", convId] });
      // A SKU edit is copied into Item Details.
      if ((listing.sku || "") !== (data?.listing?.sku || "")) {
        queryClient.invalidateQueries({ queryKey: ["conversation", convId] });
      }
    },
    onError: (err: Error) => {
      toastManager.add({ type: "error", title: "Could not save", description: err.message || "Try that field again." });
    },
  });

  React.useEffect(() => {
    if (listingJob?.mode === "schema_probe" && listingJob.status === "completed") {
      queryClient.invalidateQueries({ queryKey: ["listing", convId] });
      queryClient.invalidateQueries({ queryKey: ["fill-log"] });
      queryClient.invalidateQueries({ queryKey: ["conversation", convId] });
    }
  }, [listingJob?.mode, listingJob?.status, listingJob?.id, convId, queryClient]);

  const tabs = React.useMemo(() => {
    const selected = new Set(marketplaceSettings?.selected ?? ["ebay", "etsy", "poshmark", "mercari", "depop"]);
    return [
      "general",
      ...["ebay", "poshmark", "mercari", "depop", "etsy", "grailed", "vinted", "facebook", "sellwild"].filter((id) => id === "vinted" || selected.has(id)),
    ];
  }, [marketplaceSettings?.selected]);

  React.useEffect(() => {
    if (!tabs.includes(editTab)) setEditTab("general");
  }, [editTab, tabs]);

  const openBlocker = (field: string) => {
    if (isEbayInventoryLocationEdit(field)) {
      onEditInventoryLocation?.();
      return;
    }
    const target = blockerTarget(field);
    onReviewTabChange(target.reviewTab);
    setEditTab(tabs.includes(target.tab) ? target.tab : "general");
    setFieldTarget({ field, sequence: Date.now() });
  };
  React.useEffect(() => {
    if (fieldTarget?.field === "photos" && reviewTab === "input") {
      const target = editorRef.current?.querySelector<HTMLElement>("[data-photo-tray] button");
      target?.scrollIntoView({ block: "center" });
      target?.focus();
    }
  }, [fieldTarget, reviewTab]);

  // Error cards offer "Fix in chat" / "Fill empty fields"; mid-generation the
  // listing is still being written, so those prompts would chase half-done values.
  const chatBusy = useChatBusy(convId);
  // Same query ChatPanel polls: background field fills keep writing after the
  // stream ends, and "Ask chat" beside "Filling discovered fields…" contradicts it.
  const { data: activity } = useQuery({
    queryKey: ["activity", convId],
    queryFn: () => api.conversations.activity(convId),
    refetchInterval: (query) => activityPollMs(Boolean(query.state.data?.busy)),
  });
  const generating = chatBusy || Boolean(activity?.busy) || schemaProbeActive;
  const askChat = generating ? undefined : onAskChat;

  const listing = data?.listing || {};
  const listingTitle = String(listing.title || "Listing");
  const importedItemId = listingJob?.vendoo_item_id || notesVendooItemId(conversation?.notes);
  const importedUrl = listingJob?.vendoo_url || notesVendooUrl(conversation?.notes);

  // Studio writes the Vendoo *form*. Vendoo carries a form change onto a live
  // marketplace listing only when the seller delists and relists it there, so
  // the app says which listings are still on the old copy instead of leaving a
  // regenerated item reading as though it were published.
  // The listing's own answer is fresher than the sidebar row's after an edit.
  const etsyEligible = data?.etsy_eligible ?? conversation?.etsy_eligible ?? true;
  const relistSource = React.useMemo(
    () => (conversation ? { ...conversation, etsy_eligible: etsyEligible } : conversation),
    [conversation, etsyEligible],
  );
  const relistMarketplaces = React.useMemo(
    () => marketplacesNeedingRelist(relistSource).map(marketplaceName),
    [relistSource],
  );
  // Which half is left. After Delist Item the item is live nowhere, which the
  // banner has to say plainly — that is the state most easily walked away from.
  const stage = React.useMemo(() => relistStage(relistSource), [relistSource]);
  const relistDone = useMutation({
    mutationFn: () => api.vendooApi.relistDone(convId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conversation", convId] });
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
  const liveMarketplaces = React.useMemo(() => {
    const sold = conversation?.vendoo_sold_dates || {};
    return (conversation?.vendoo_marketplaces || [])
      .filter((id) => !sold[id])
      .map(marketplaceName);
  }, [conversation]);

  const ensureAttemptKey = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (jobsLoading || photosLoading || listingJob || !importedItemId) return;
    const key = `${convId}:${importedItemId}`;
    if (ensureAttemptKey.current === key) return;
    ensureAttemptKey.current = key;
    // Blank linked listing (Clear, or first open with no local photos): import the
    // Vendoo draft. Photos still present means Regenerate/remount — only reattach
    // Fields. importDraft opens Chrome and walks every marketplace tab.
    const hasLocalPhotos = Boolean(photos?.length);
    ensureDraftMutation.mutate({ importDraft: !hasLocalPhotos });
    // Refresh fields clears ensureAttemptKey before calling mutate again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobsLoading, photosLoading, listingJob?.id, importedItemId, convId, photos?.length]);

  return (
    <div className="listing-editor" ref={editorRef}>
      <div className="flex shrink-0 flex-col gap-2 px-3 pt-2.5">
        {/* The tabs, actions and browser toggle belong to this listing, so they
            sit together in its own header. Desktop: the tab and the titlebar
            breadcrumb already name the listing, so only mobile, which has no
            titlebar, shows the title here. */}
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <h2
            className="pywebview-drag-region hidden min-w-0 flex-1 basis-full truncate font-semibold text-[15px] text-foreground max-[900px]:block"
            title={listingTitle}
          >
            {listingTitle}
          </h2>
          <ListingReviewActions
            convId={convId}
            onCleared={onCleared}
          >
            <ListingBrowserButton
              convId={convId}
              browserOpen={browserOpen}
              onOpenBrowser={onOpenBrowser}
            />
          </ListingReviewActions>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-muted-foreground text-xs">
          <Button variant="ghost-muted" size="xs" disabled={!data?.current_revision_id} onClick={() => { setFieldTarget(null); setHistoryOpen(true); }}>
            <HistoryIcon />
            History
          </Button>
          <VendooLinkControl
            convId={convId}
            itemId={importedItemId}
            url={importedUrl}
            onLinked={(linkedItemId) => {
              // Claim the auto-attach key so the effect doesn't race this import.
              ensureAttemptKey.current = `${convId}:${linkedItemId}`;
              queryClient.invalidateQueries({ queryKey: ["conversation", convId] });
              queryClient.invalidateQueries({ queryKey: ["conversations"] });
              queryClient.invalidateQueries({ queryKey: ["jobs", convId] });
              ensureDraftMutation.mutate({ importDraft: true });
            }}
          />
          <MarketplaceLinks urls={conversation?.vendoo_listing_urls} />
          <ListingSyncStatus
            convId={convId}
            bound={Boolean(listingJob?.vendoo_item_id || importedItemId)}
          />
          {conversation?.unsent_edits ? (
            <>
              <Badge
                variant="info"
                title="This listing has changed since Vendoo last had it. Update Vendoo writes it onto the Vendoo form."
              >
                Unsent edits
              </Badge>
              {listingJob?.vendoo_item_id || importedItemId ? (
                <InlineButton
                  tone="muted"
                  className="text-xs"
                  disabled={discardUnsent.isPending || generating}
                  title="Replace this listing with the copy Vendoo has"
                  onClick={() => void confirmDiscardUnsent()}
                >
                  {discardUnsent.isPending ? "Pulling…" : "Discard"}
                </InlineButton>
              ) : null}
            </>
          ) : null}
          {data?.can_send && !generating && <Badge variant="success">Ready</Badge>}
        </div>
      </div>

      <SoldElsewhereNotice
        convId={convId}
        openInVendoo={
          listingJob ? (
            <OpenListingButton jobId={listingJob.id} vendooItemId={importedItemId} vendooUrl={importedUrl} />
          ) : null
        }
      />


      {relistMarketplaces.length > 0 && (
        <Alert
          variant="warning"
          role="status"
          controlAlignment="first-line"
          className="mx-3 mt-2.5 shrink-0"
          title={`Waiting on a relist: ${relistMarketplaces.join(", ")}`}
        >
          <RotateCcwIcon />
          <AlertTitle>
            {stage === "list" ? "Finish relisting in Vendoo" : "Relist to show your updates"}
          </AlertTitle>
          <AlertDescription className="gap-2">
            <p>{relistCallout(relistMarketplaces, stage)}</p>
            <ol className="list-decimal space-y-1 ps-4">
              <li>Open this listing in Vendoo.</li>
              {stage !== "list" && (
                <li>
                  Open the <strong>⋮ menu</strong> beside <strong>Vendoo Form</strong> and choose{" "}
                  <strong>Delist Item</strong>. This removes the item from every marketplace at once.
                </li>
              )}
              <li>List the item again on: <strong>{relistMarketplaces.join(", ")}</strong>.</li>
            </ol>
            <p className="text-xs">
              Already relisted? Choose <strong>I've relisted</strong> to clear this reminder.
              {" "}This button only clears the reminder.
            </p>
            <div className="flex flex-wrap gap-2">
              {listingJob ? (
                <OpenListingButton
                  jobId={listingJob.id}
                  vendooItemId={importedItemId}
                  vendooUrl={importedUrl}
                />
              ) : null}
              <Button
                variant="ghost"
                size="xs"
                disabled={relistDone.isPending}
                title="Clear this reminder after you have relisted in Vendoo"
                onClick={() => relistDone.mutate()}
              >
                {relistDone.isPending ? "Clearing…" : "I've relisted"}
              </Button>
            </div>
          </AlertDescription>
        </Alert>
      )}

      {schemaProbeActive && (
        <Alert variant="info" role="status" className="mx-3 mt-2.5 shrink-0">
          <Spinner />
          <AlertDescription>
            Discovering Vendoo marketplace fields for this category… Empty keys will appear on each marketplace as they arrive.
          </AlertDescription>
        </Alert>
      )}

      <MarketplacesLayout key={convId} convId={convId} revisionId={data?.current_revision_id || null} title={listingTitle}
        disabled={generating} etsyEligible={etsyEligible} onAskChat={askChat} onEditField={openBlocker} ids={tabs}
        selected={reviewTab === "forms" && editTab !== "general" ? editTab : "general"}
        onSelect={(id) => {
          setFieldTarget(null);
          if (id === "general") onReviewTabChange("input");
          else onReviewTabChange("forms");
          setEditTab(id);
        }}>
        <>
          {(reviewTab !== "forms" || editTab === "general") && (
            <>
              <PhotoTray convId={convId} onBulkListingsCreated={onBulkListingsCreated} />
              <ItemDetails convId={convId} />
            </>
          )}
          <StructuredEditor
            convId={convId}
            listing={listing}
            revisionId={data?.current_revision_id}
            tab={reviewTab === "forms" && editTab !== "general" ? editTab : "general"}
            target={fieldTarget}
            onChange={(updated) => updateMutation.mutate(updated)}
          />
        </>
      </MarketplacesLayout>

      {vendooEnabled && <div className="listing-send-footer flex max-h-[min(42vh,320px)] min-w-0 shrink-0 flex-col overflow-y-auto overflow-x-hidden border-t px-3 py-2.5 max-[900px]:max-h-[min(28vh,220px)] max-[900px]:pb-[max(10px,env(safe-area-inset-bottom,0px))]">
        <SendToVendooButton
          convId={convId}
          canSend={data?.can_send ?? false}
          generating={generating}
          sendBlockers={data?.errors || []}
          listing={listing}
          listingTitle={listingTitle}
          selectedMarketplaces={marketplaceSettings?.selected}
          liveMarketplaces={liveMarketplaces}
          vendooItemId={importedItemId}
          onSelectBlocker={openBlocker}
          onAskChat={askChat}
        />
      </div>}
      {historyOpen && data?.current_revision_id && <ListingHistory convId={convId} listing={listing} revisionId={data.current_revision_id}
        busy={generating || Boolean(jobs?.some((job) => ACTIVE_SEND_STATUSES.has(job.status)))} onClose={() => setHistoryOpen(false)} />}
    </div>
  );
}

export function StructuredEditor({
  convId,
  listing,
  revisionId,
  tab,
  onChange,
  target,
}: {
  convId: string;
  listing: ListingData;
  revisionId?: string | null;
  tab: string;
  onChange: (v: ListingData) => void;
  target: { field: string; sequence: number } | null;
}) {
  const formRef = React.useRef<HTMLDivElement>(null);
  const nativeTab = DIRECT_FORM_MARKETS.has(tab);
  const nativeQuery = useQuery({
    queryKey: ["native-editor-form", convId, tab, revisionId],
    queryFn: () => api.nativeMarketplaces.form(convId, tab, revisionId),
    enabled: nativeTab,
    staleTime: 60_000,
    retry: false,
  });
  // What Vendoo says this listing's category actually renders, rather than a
  // list kept by hand here. Falls back to the static one while it loads, or
  // for a category no schema has been fetched for.
  const formsQuery = useQuery({
    queryKey: ["listing-fields", convId],
    queryFn: () => api.vendooApi.listingFields(convId),
    enabled: !nativeTab,
    staleTime: 60_000,
  });
  const { data: forms } = formsQuery;
  const dropdownQuery = useQuery({
    queryKey: ["catalog-dropdown-options"],
    queryFn: api.catalog.dropdownOptions,
    enabled: !nativeTab,
    staleTime: Infinity,
  });
  const { data: dropdownOptions } = dropdownQuery;
  const { data: inventoryLocation } = useQuery({
    queryKey: ["settings-ebay-inventory-location"],
    queryFn: api.settings.ebayInventoryLocation,
    staleTime: 30_000,
  });
  // Show all is per tab and starts collapsed, so a marketplace opens on its category form.
  const [showAllTab, setShowAllTab] = React.useState<string | null>(null);
  const showAll = showAllTab === tab;
  const categoryForm = nativeTab ? nativeQuery.data : forms?.forms.find((form) => form.marketplace === tab);
  const fields = React.useMemo(
    () => {
      const source = nativeTab ? (nativeQuery.data ? [nativeQuery.data] : []) : forms?.forms;
      const built = fieldsForMarketplaceForm(listing, tab, source, showAll);
      const rows = nativeTab
        ? nativeEditorDependencies(built, listing)
        : withDropdownOptions(built, tab, dropdownOptions?.forms);
      const fallback = inventoryLocation?.location || "";
      if (!fallback) return rows;
      return rows.map((field) => (
        field.key === "zipCode" && !formFieldText(getListingEditorValue(listing, field.key))
          ? { ...field, defaultValue: fallback }
          : field
      ));
    },
    [nativeTab, nativeQuery.data, forms, listing, tab, showAll, dropdownOptions?.forms, inventoryLocation?.location],
  );
  const [local, setLocal] = React.useState<Record<string, string | string[]>>({});
  const dirty = React.useRef<Record<string, string | string[]>>({});
  React.useEffect(() => {
    if (!target) return;
    const key = matchingEditorField(fields, target.field);
    if (!key) return;
    const input = [...(formRef.current?.querySelectorAll<HTMLElement>("[data-listing-field]") || [])].find((element) => element.dataset.listingField === key);
    input?.scrollIntoView({ block: "center" });
    input?.focus();
  }, [target, fields]);

  React.useEffect(() => {
    const init: Record<string, string | string[]> = {};
    fields.forEach((f) => {
      const val = getListingEditorValue(listing, f.key);
      // Schema labels ("Sleeve Length") don't match JSON keys ("sleeveLength");
      // the fields API already resolved the value — use it when the path miss.
      const value = (f.native || f.locked) && f.defaultValue !== undefined ? f.defaultValue : (val ?? f.defaultValue);
      const label = (v: string) => Object.entries(f.optionValues || {}).find(([, id]) => id === v)?.[0] ?? v;
      const followed = f.label.endsWith(" price") && f.key.includes("_specifics.")
        && !(Array.isArray(listing?.seller_fields) && listing.seller_fields.includes(f.key));
      const shown = followed && listing?.price != null
        ? formFieldText(listing.price)
        : f.multi ? formFieldValues(value, f.options).map(label) : label(formFieldText(value));
      init[f.key] = f.key === "ebay_specifics.bestOffer.enabled"
        ? (/^(no|false|0|off)$/i.test(formFieldText(shown)) ? "No" : "Yes")
        : shown;
    });
    const next = nextFieldDraft(init, dirty.current);
    dirty.current = next.dirty;
    setLocal(next.local);
  }, [listing, revisionId, tab, fields]);

  const handleBlur = (key: string) => {
    if (local[key] == null) return;
    const field = fields.find((entry) => entry.key === key);
    if (field?.locked) return;
    const before = getListingEditorValue(listing, key);
    const text = formFieldText(local[key]);
    if (text.trim() === "[object Object]") return;
    if (text === formFieldText(before) || text === formFieldText(field?.defaultValue)) return;
    const updated = cloneListing(listing);
    setListingEditorValue(updated, key, field?.type === "number" ? coerce(text) : text);
    onChange(updated);
  };

  const commitField = (key: string, value: string | string[] | boolean) => {
    if (typeof value === "string" && value.trim() === "[object Object]") return;
    const shown = typeof value === "boolean" ? String(value) : value;
    const nextLocal = { ...local, [key]: shown };
    dirty.current = { ...dirty.current, [key]: shown };
    setLocal(nextLocal);
    const updated = cloneListing(listing);
    const field = fields.find((entry) => entry.key === key);
    const stored = typeof value === "string" ? field?.optionValues?.[value] ?? (field?.type === "number" ? coerce(value) : value)
      : Array.isArray(value) ? value.map(label => field?.optionValues?.[label] ?? label) : value;
    setListingEditorValue(updated, key, stored);
    if (key === "poshmark_specifics.nativeSizeId") {
      setListingEditorValue(updated, "poshmark_specifics.sizeSetId", fields.find((row) => row.key === "poshmark_specifics.sizeSetId")?.defaultValue || "");
    }
    for (const dependent of field?.clears || []) setListingEditorValue(updated, dependent, dependent.endsWith(".attributes") ? {} : "");
    onChange(updated);
  };

  const sections = groupFieldsIntoSections(fields);
  const control = (f: EditorField) => {
    const id = `listing-field-${f.key}`;
    const text = formFieldText(local[f.key]);
    if (f.native && f.key === "direct_category_ids.ebay") return <EbayCategorySelect id={id} value={text} onChange={(categoryId, path) => {
      const updated = cloneListing(listing);
      setListingEditorValue(updated, f.key, categoryId);
      setListingEditorValue(updated, "direct_category_ids.ebay_path", path);
      onChange(updated);
    }} />;
    if (f.type === "boolean") return <Switch id={id} data-listing-field={f.key} checked={text === "true"}
      disabled={f.locked} onCheckedChange={(checked) => commitField(f.key, checked)} />;
    if (f.key === "description") {
      return <Textarea id={id} data-listing-field={f.key} className="[&_textarea]:min-h-25" value={text} maxLength={f.maxLength ?? undefined} onChange={(e) => setLocal({ ...local, [f.key]: e.target.value })} onBlur={() => handleBlur(f.key)} />;
    }
    if (f.multi) return <SearchableMultiSelect id={id} fieldKey={f.key}
      value={expandJoinedChoices(local[f.key], f.options)} options={f.options ?? []}
      selectionOnly={f.selectionOnly ?? Boolean(f.options?.length)} disabled={f.locked}
      choicesVerified={f.optionsSource === "marketplace"}
      maxValues={f.maxValues ?? undefined} maxLength={f.maxLength ?? undefined}
      onChange={(value) => commitField(f.key, value)} />;
    if (f.options?.length) {
      return (
        <SearchableSelect
          id={id}
          fieldKey={f.key}
          value={text}
          options={f.options}
          disabled={f.locked}
          selectionOnly={f.selectionOnly}
          choicesVerified={f.optionsSource === "marketplace"}
          maxLength={f.maxLength ?? undefined}
          onChange={(value) => commitField(f.key, value)}
        />
      );
    }
    return (
      <Input
        nativeInput
        id={id}
        data-listing-field={f.key}
        type={f.type || "text"}
        readOnly={f.locked}
        disabled={f.selectionOnly && !f.options?.length}
        maxLength={f.maxLength ?? undefined}
        value={text}
        onChange={(e) => { if (!f.locked) setLocal({ ...local, [f.key]: e.target.value }); }}
        onBlur={() => handleBlur(f.key)}
      />
    );
  };
  // Long text and the category path need the full row.
  const fullWidth = (f: EditorField) => ["title", "description", "category_path"].includes(f.key) || f.label === "Category";

  return (
    // Keyed by tab so switching marketplaces rebuilds the form instead of reusing rows.
    <div ref={formRef} key={tab} className="flex flex-col">
      {tab !== "general" && <div className="flex items-start justify-between gap-3 border-b py-3 text-xs text-muted-foreground">
        <p role="status">{nativeTab && nativeQuery.isFetching
          ? "Loading the marketplace’s category fields and choices…"
          : nativeTab && nativeQuery.isError
            ? (nativeQuery.error instanceof Error ? nativeQuery.error.message : "The marketplace form could not be loaded. Refresh choices.")
            : !nativeTab && formsQuery.isFetching
              ? "Refreshing category fields and choices…"
              : !nativeTab && (formsQuery.isError || dropdownQuery.isError)
                ? "Some choices could not be refreshed. Check the source below each field before listing."
                : categoryForm?.known === false
                  ? "The full category form is unavailable. Reference choices are shown where available."
                  : categoryForm?.known && showAll
                    ? "Every saved field is showing. Values you do not see on the category form stay saved."
                    : categoryForm?.known
                      ? "This category's fields are showing."
                      : nativeTab
                        ? "Fields and choices come from the catalog direct List uses."
                        : "Choices depend on this category. Reference choices may differ from the marketplace."}</p>
        <div className="flex shrink-0 gap-1">
          {categoryForm?.known && <Button type="button" variant="ghost" size="sm" aria-pressed={showAll}
            onClick={() => setShowAllTab(showAll ? null : tab)}>{showAll ? "Show category fields" : "Show all fields"}</Button>}
          <Button type="button" variant="ghost" size="sm" onClick={() => {
            if (nativeTab) { void nativeQuery.refetch(); return; }
            void formsQuery.refetch(); void dropdownQuery.refetch();
          }}>Refresh choices</Button>
        </div>
      </div>}
      {nativeTab && nativeQuery.data?.notes?.map((note) => <p key={note} className="py-2 text-xs text-muted-foreground">{note}</p>)}
      {sections.map((section) => {
        const filled = section.fields.filter((f) => formFieldText(local[f.key]).trim() !== "").length;
        return (
          <InspectorSection
            key={section.id}
            title={section.title}
            titleId={`listing-section-${tab}-${section.id}`}
            meta={section.id === "listing" ? null : `${filled} of ${section.fields.length} filled`}
          >
            <div className="grid min-w-0 grid-cols-2 gap-3 max-[900px]:grid-cols-1">
              {section.fields.map((f) => (
                <div key={f.key} className={cn("min-w-0", fullWidth(f) && "col-span-full")}>
                  <FieldLabel htmlFor={`listing-field-${f.key}`}>{f.label}</FieldLabel>
                  {control(f)}
                  {f.help && <p className="mt-1 text-xs text-muted-foreground">{f.help}</p>}
                  {f.maxLength != null && (f.multi ? formFieldValues(local[f.key], f.options) : [formFieldText(local[f.key])]).some((value) => value.length > f.maxLength!) &&
                    <p role="alert" className="mt-1 text-xs text-warning-foreground">Each value allows at most {f.maxLength} characters. Shorten the saved value.</p>}
                  {f.selectionOnly && !f.options?.length && <p role="alert" className="mt-1 text-xs text-warning-foreground">Choices unavailable. Refresh choices to load this field.</p>}
                  {f.optionsSource && f.optionsSource !== "saved" && <p className="mt-1 text-xs text-muted-foreground">
                    {f.optionsSource === "marketplace" ? "Marketplace choices" : f.optionsSource === "vendoo" ? "Imported category choices" : "Reference choices"}
                  </p>}
                </div>
              ))}
            </div>
          </InspectorSection>
        );
      })}
    </div>
  );
}

const SHARED_FORM_FIELDS: EditorField[] = [
  { key: "title", label: "Title" },
  { key: "description", label: "Description" },
  { key: "price", label: "Price", type: "number" },
  { key: "brand", label: "Brand" },
  { key: "condition", label: "Condition" },
  { key: "primaryColor", label: "Primary Color" },
  { key: "secondaryColor", label: "Secondary Color" },
  { key: "quantity", label: "Quantity", type: "number" },
  { key: "size", label: "Size" },
  { key: "sku", label: "SKU" },
  { key: "category_path", label: "Category" },
];

const PACKAGE_FORM_FIELDS: EditorField[] = [
  { key: "weight_oz", label: "Package Weight (oz)", type: "number" },
  { key: "package_dimensions_in", label: "Package Dimensions (LxWxH in)" },
];

const PACKAGE_MARKETPLACES = new Set(["etsy"]);
/**
 * Two rows are the same field when they share a label or edit the same value.
 * "productType" from the listing and "Product Type" from Vendoo's schema both
 * write depop_specifics.productType (see setListingEditorValue).
 */
function fieldIdentities(field: EditorField): string[] {
  const parts = field.key.split(".");
  const key = parts.length === 2 && parts[0].endsWith("_specifics")
    ? `${parts[0]}.${squashFieldKey(parts[1])}`
    : field.key.toLowerCase();
  return [`label:${field.label.replace(/\s*\*$/, "").trim().toLowerCase()}`, `key:${key}`];
}

/** First occurrence wins, unless a later row is the one with a choice list. */
function mergeEditorFields(fields: EditorField[]): EditorField[] {
  const index = new Map<string, number>();
  const out: EditorField[] = [];
  for (const field of fields) {
    const ids = fieldIdentities(field);
    const at = ids.map((id) => index.get(id)).find((hit) => hit != null);
    if (at == null) {
      ids.forEach((id) => index.set(id, out.length));
      out.push(field);
      continue;
    }
    ids.forEach((id) => { if (!index.has(id)) index.set(id, at); });
    if (!out[at].options?.length && field.options?.length) out[at] = field;
  }
  return out;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function sameField(a: EditorField, b: EditorField): boolean {
  const ids = new Set(fieldIdentities(a));
  return fieldIdentities(b).some((id) => ids.has(id));
}

const MERCARI_DERIVED_LABELS = new Set(["quantity", "shipping label", "delivery method", "who will pay for delivery?", "payer", "who pays"]);

const MARKETPLACE_PRICE_KEYS: Record<string, string> = {
  ebay: "ebay_specifics.price",
  poshmark: "poshmark_specifics.poshmarkPrice",
  mercari: "mercari_specifics.price",
  depop: "depop_specifics.price",
  etsy: "etsy_specifics.price",
  vinted: "vinted_specifics.price",
  sellwild: "sellwild_specifics.price",
};

function marketplacePriceField(tab: string): EditorField | null {
  const key = MARKETPLACE_PRICE_KEYS[tab];
  if (!key) return null;
  return { key, label: `${marketplaceName(tab)} price`, type: "number" };
}

function withMarketplacePrice(rows: EditorField[], tab: string): EditorField[] {
  const priceRow = marketplacePriceField(tab);
  if (!priceRow || rows.some((field) => field.key === priceRow.key)) return rows;
  return [...rows, priceRow];
}

const BEST_OFFER_ROWS: EditorField[] = [
  { key: "ebay_specifics.bestOffer.enabled", label: "Best Offer enabled", options: ["Yes", "No"], selectionOnly: true, optionsSource: "marketplace", defaultValue: "Yes" },
  { key: "ebay_specifics.bestOffer.autoAcceptPrice", label: "Auto Accept Price", type: "number" },
  { key: "ebay_specifics.bestOffer.minimumOfferPrice", label: "Minimum Offer Price", type: "number" },
];

function finishMarketplaceRows(rows: EditorField[], listing: ListingData | undefined, tab: string): EditorField[] {
  let visible = tab === "mercari"
    ? rows.filter((field) => field.native || !MERCARI_DERIVED_LABELS.has(field.label.replace(/\s*\*$/, "").toLowerCase()))
    : rows;
  if (tab === "depop") {
    for (const row of depopMeasurementFields(listing)) {
      if (!visible.some((field) => field.key === row.key)) visible = [...visible, row];
    }
    if (!depopSizeGroupingApplies(listing) && !isBottomsListing(listing)) {
      visible = visible.filter((field) => field.key !== "depop_specifics.sizeGrouping");
    }
  }
  if (tab === "ebay") {
    for (const row of BEST_OFFER_ROWS) {
      if (!visible.some((field) => field.key === row.key)) visible = [...visible, row];
    }
  }
  const respectMarketplaceSize = visible.some((field) => {
    const leaf = (field.key.split(".").pop() || "").toLowerCase().replace(/[^a-z]/g, "");
    return leaf === "size" && field.key.startsWith(`${tab}_specifics`) && field.optionsSource === "marketplace";
  });
  return applyBottomsFields(visible, listing, tab, { respectMarketplaceSize });
}

function depopMeasurementFields(listing: ListingData | undefined): EditorField[] {
  const depop = listing?.depop_specifics;
  const block = depop && typeof depop === "object" ? (depop as Record<string, unknown>).measurements : null;
  const values = block && typeof block === "object" ? (block as { values?: unknown }).values : null;
  if (!Array.isArray(values)) return [];
  return values.map((row, index) => {
    const item = row && typeof row === "object" ? row as Record<string, unknown> : {};
    return {
      key: `depop_specifics.measurements.values.${index}.value`,
      label: String(item.label || item.id || "Measurement"),
      type: "number" as const,
    };
  });
}

/** Labels of the controls a marketplace's listing form always has, apart from category attributes. */
const CATEGORY_FORM_LABELS: Record<string, string[]> = {
  ebay: ["Title", "Description", "Price", "eBay price", "Quantity", "Condition", "Condition Description",
    "eBay category", "Primary Store Category", "Secondary Store Category", "Inventory location",
    "Best Offer enabled", "Auto Accept Price", "Minimum Offer Price"],
  poshmark: ["Title", "Description", "Price", "Poshmark price", "Brand", "Condition", "Primary Color",
    "Secondary Color", "Original Price", "Sub-category", "Quantity"],
  mercari: ["Title", "Description", "Price", "Mercari price", "Brand", "Condition"],
  depop: ["Title", "Description", "Price", "Depop price", "Brand", "Condition", "Category", "Source",
    "Age", "Style", "Parcel Size"],
  etsy: ["Title", "Description", "Price", "Etsy price", "Quantity", "Who Made It?", "What Is It?",
    "When Was It Made?", "Etsy category"],
  sellwild: ["Title", "Description", "Price", "Sellwild price", "Category", "Condition"],
  vinted: ["Title", "Description", "Price", "Vinted price", "Category", "Condition", "Brand", "Size",
    "Color", "Package size"],
};

function foldFieldName(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, "");
}

/**
 * A known category shows its own attributes plus the marketplace's real controls.
 * Saved values the form does not show stay on the listing; Show all reveals their rows.
 */
function limitToCategoryForm(
  rows: EditorField[],
  forms: MarketplaceForm[] | undefined,
  tab: string,
  showAll: boolean,
): EditorField[] {
  if (showAll || tab === "general") return rows;
  const form = forms?.find((entry) => entry.marketplace === tab);
  if (!form?.known) return rows;
  const allowed = new Set(
    form.fields
      .filter((field) => field.on_form !== false)
      .flatMap((field) => [foldFieldName(field.label), foldFieldName(field.key)]),
  );
  const core = new Set((CATEGORY_FORM_LABELS[tab] ?? []).map(foldFieldName));
  return rows.filter((field) => {
    if (field.key.includes(".measurements.")) return true;
    const label = foldFieldName(field.label.replace(/\s*\*$/, ""));
    const leaf = foldFieldName(field.key.split(".").pop() || "");
    return core.has(label) || allowed.has(label) || allowed.has(leaf);
  });
}

/** Every field that marketplace submits, shared listing values first, then its own. */
export function fieldsForMarketplaceForm(
  listing: ListingData | undefined,
  tab: string,
  forms: MarketplaceForm[] | undefined,
  showAll = false,
): EditorField[] {
  const catalog = forms?.find((form) => form.marketplace === tab);
  // A direct marketplace with its native catalog, or with no category schema yet,
  // does not fall back to a hand-kept form. A known category schema still merges.
  if (DIRECT_FORM_MARKETS.has(tab) && (catalog?.native || !catalog?.fields.length)) {
    const rows = catalog?.native ? schemaFieldsForTab(forms, tab) || [] : [];
    return limitToCategoryForm(finishMarketplaceRows(withMarketplacePrice(rows, tab), listing, tab), forms, tab, showAll);
  }
  const own = getFieldsForTab(listing, tab);
  const withoutObjects = (fields: EditorField[]) => fields.filter((field) => !isPlainObject(getListingEditorValue(listing, field.key)));
  if (tab === "general") return withoutObjects(own);
  // Sellwild's category and condition are the direct picks beside List. Its other
  // category attributes still come from the same schema the other marketplaces use.
  const schema = (schemaFieldsForTab(forms, tab) ?? []).filter((field) => (
    tab !== "sellwild" || !["category", "condition"].includes(field.label.replace(/\s*\*$/, "").toLowerCase())
  ));
  let shared = PACKAGE_MARKETPLACES.has(tab)
    ? [...SHARED_FORM_FIELDS, ...PACKAGE_FORM_FIELDS]
    : SHARED_FORM_FIELDS;
  // A marketplace with its own category path is categorised by that, not the shared one.
  if (own.some((field) => field.label === "Category")) {
    shared = shared.filter((field) => field.key !== "category_path");
  }
  const merged = mergeEditorFields([...shared, ...own, ...schema]);
  const named = merged.map((field) => {
    // Vendoo's label is the name the marketplace uses, so it wins over a listing key.
    const match = schema.find((row) => sameField(row, field));
    return match && match !== field
      ? { ...field, label: match.label, required: match.required,
          multi: match.multi, selectionOnly: field.optionsSource === "marketplace" ? field.selectionOnly : match.selectionOnly,
          maxValues: match.maxValues, maxLength: match.maxLength,
          optionsSource: field.optionsSource === "marketplace" ? field.optionsSource : match.optionsSource }
      : field;
  });
  return limitToCategoryForm(
    finishMarketplaceRows(withoutObjects(withMarketplacePrice(named, tab)), listing, tab),
    forms,
    tab,
    showAll,
  );
}

/** Fields for this tab from Vendoo's own schema, or null when it has none. */
function schemaFieldsForTab(
  forms: MarketplaceForm[] | undefined,
  tab: string,
): EditorField[] | null {
  const form = forms?.find((entry) => entry.marketplace === tab);
  if (tab === "general") return null;
  // An unknown leaf still answers with the marketplace's own controls and the
  // values this listing holds, so render whatever rows came back.
  if (!form?.fields.length) return null;
  return form.fields.filter((field) => !isPlainObject(field.value)).map((field) => ({
    // Schema keys are Vendoo labels ("Sleeve Length"); listing JSON uses camelCase.
    // getListingEditorValue / setListingEditorValue bridge the two.
    key: form.native ? field.path || field.key : `${tab}_specifics.${field.key}`,
    native: form.native,
    type: field.input_type,
    locked: field.locked,
    optionValues: field.option_values,
    clears: field.clears,
    help: field.help,
    valueConstraints: field.value_constraints,
    label: field.required ? `${field.label} *` : field.label,
    required: field.required,
    multi: field.multi,
    selectionOnly: field.selection_only || field.options_source === "reference"
      || (field.options.length > 0 && (field.options_source === "marketplace" || field.options_source === "vendoo")),
    maxValues: field.max_values,
    maxLength: field.max_length,
    optionsSource: field.options_source,
    options: field.options,
    defaultValue: form.native ? field.value : formFieldText(field.value),
  }));
}

export function nativeEditorDependencies(fields: EditorField[], listing: ListingData): EditorField[] {
  return fields.map((field) => !field.valueConstraints ? field : { ...field,
    options: field.options?.filter((value) => (field.valueConstraints?.[value] || []).every((rule) => {
      const parent = fields.find((row) => row.label.replace(/\s*\*$/, "").toLowerCase() === rule.parentName.toLowerCase());
      const selected = parent && (getListingEditorValue(listing, parent.key) ?? parent.defaultValue);
      const values = formFieldValues(selected).map((item) => item.toLowerCase());
      return rule.parentValues.some((item) => values.includes(item.toLowerCase()));
    })),
  });
}

function liveCategoryFields(listing: ListingData | undefined, marketplace: "ebay" | "etsy"): EditorField[] {
  const ids = listing?.direct_category_ids;
  const path = ids && typeof ids === "object" ? String((ids as Record<string, unknown>)[`${marketplace}_path`] || "") : "";
  if (!path) return [];
  return [{ key: `direct_category_ids.${marketplace}_path`, label: marketplace === "ebay" ? "eBay category" : "Etsy category", locked: true }];
}

function getFieldsForTab(listing: ListingData | undefined, tab: string): EditorField[] {
  switch (tab) {
    case "general":
      return [
        { key: "title", label: "Title" },
        { key: "description", label: "Description" },
        { key: "price", label: "Price", type: "number" },
        { key: "cost", label: "Cost", type: "number" },
        { key: "quantity", label: "Quantity", type: "number" },
        { key: "brand", label: "Brand" },
        { key: "condition", label: "Condition" },
        { key: "primaryColor", label: "Primary Color" },
        { key: "secondaryColor", label: "Secondary Color" },
        { key: "size", label: "Size" },
        { key: "sku", label: "SKU" },
        { key: "zipCode", label: "Inventory location" },
        { key: "category_path", label: "Category" },
        { key: "department", label: "Department" },
        { key: "weight_oz", label: "Package Weight (oz)", type: "number" },
        { key: "package_dimensions_in", label: "Package Dimensions (LxWxH in)" },
      ];
    case "etsy":
      return mergeSpecificsWithDefaults(
        listing?.etsy_specifics,
        "etsy_specifics",
        [
          ...liveCategoryFields(listing, "etsy"),
          { key: "etsy_specifics.whoMade", label: "Who Made It?" },
          { key: "etsy_specifics.whatIsIt", label: "What Is It?" },
          { key: "etsy_specifics.whenMade", label: "When Was It Made?" },
          ...ETSY_CATEGORY_OPTIONALS.map((field) => ({
            key: `etsy_specifics.category_specifics.${field.key}`,
            label: field.label,
          })),
        ],
      );
    case "vinted":
      return [
        { key: "price", label: "Price", type: "number" },
        { key: "vinted_specifics.category", label: "Category" },
        { key: "vinted_specifics.condition", label: "Condition" },
        { key: "vinted_specifics.brand", label: "Brand" },
        { key: "vinted_specifics.size", label: "Size" },
        { key: "vinted_specifics.color", label: "Color" },
        { key: "vinted_specifics.packageSize", label: "Package size" },
        { key: "vinted_specifics.material", label: "Material" },
      ];
    case "grailed":
    case "facebook":
      // Their fields come from the category Vendoo maps to, once it is chosen.
      return [
        { key: "price", label: "Price", type: "number" },
        { key: "condition", label: "Condition" },
        { key: "brand", label: tab === "grailed" ? "Designer" : "Brand" },
        ...specificsFields(listing?.[`${tab}_specifics`], `${tab}_specifics`),
      ];
    default:
      return [];
  }
}

function mergeSpecificsWithDefaults(
  specs: unknown,
  prefix: string,
  defaults: EditorField[],
): EditorField[] {
  const existing = specificsFields(specs, prefix);
  const byKey = new Map(existing.map((field) => [field.key.toLowerCase(), field]));
  const ordered = defaults.map((field) => {
    const held = byKey.get(field.key.toLowerCase());
    return held ? { ...field, key: held.key } : field;
  });
  const seen = new Set(ordered.map((field) => field.key.toLowerCase()));
  const extras = existing.filter((field) => !seen.has(field.key.toLowerCase()));
  return [...ordered, ...extras];
}

// Keys Studio keeps in <marketplace>_specifics that no form renders.
const NON_FORM_SPECIFIC_KEYS = new Set(["categoryid", "category"]);

function specificsFields(specs: unknown, prefix: string, skip: string[] = []): EditorField[] {
  if (!specs || typeof specs !== "object") return [];
  const fields: EditorField[] = [];
  const add = (path: string, value: unknown, label: string) => {
    if (isPlainObject(value)) {
      for (const [nested, child] of Object.entries(value)) add(`${path}.${nested}`, child, humanizeFieldKey(nested));
      return;
    }
    if (Array.isArray(value) && value.some((item) => isPlainObject(item))) return;
    fields.push({ key: `${prefix}.${path}`, label,
      ...(Array.isArray(value) && label !== "Category" ? { multi: true } : {}),
      ...(typeof value === "number" ? { type: "number" as const } : {}) });
  };
  for (const [key, value] of Object.entries(specs as Record<string, unknown>)) {
    if (skip.includes(key) || NON_FORM_SPECIFIC_KEYS.has(squashFieldKey(key))) continue;
    const label = squashFieldKey(key) === "categorypath" ? "Category" : humanizeFieldKey(key);
    add(key, value, label);
  }
  return fields;
}

/** "parcelSize" -> "Parcel Size". Keys already written as labels pass through. */
export function humanizeFieldKey(key: string): string {
  const text = key.replace(/[_-]+/g, " ").trim();
  const spaced = text.includes(" ") ? text : text.replace(/([a-z0-9])([A-Z])/g, "$1 $2");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function notesVendooItemId(notes?: string | null): string | null {
  if (!notes) return null;
  try {
    const parsed = JSON.parse(notes);
    const itemId = String(parsed?.vendooItemId || "").trim();
    return itemId || null;
  } catch {
    return null;
  }
}

function notesVendooUrl(notes?: string | null): string | null {
  if (!notes) return null;
  try {
    const parsed = JSON.parse(notes);
    const url = String(parsed?.vendooUrl || "").trim();
    return url || null;
  } catch {
    return null;
  }
}

function VendooLinkControl({
  convId,
  itemId,
  url,
  onLinked,
}: {
  convId: string;
  itemId?: string | null;
  url?: string | null;
  onLinked?: (itemId: string) => void;
}) {
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState(url || itemId || "");
  const inputRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    if (!editing) setDraft(url || itemId || "");
  }, [editing, itemId, url]);

  React.useEffect(() => {
    if (!editing) return;
    inputRef.current?.focus();
    inputRef.current?.select();
  }, [editing]);

  const linkMutation = useMutation({
    mutationFn: (value: string) => api.conversations.linkVendoo(convId, value),
    onSuccess: (linked) => {
      setEditing(false);
      onLinked?.(linked.vendoo_item_id);
      toastManager.add({
        type: "success",
        title: "Vendoo draft linked",
        description: "Importing its fields and photos through Chrome…",
      });
    },
    onError: (err: Error) => {
      toastManager.add({
        type: "error",
        title: "Could not link Vendoo draft",
        description: err.message || "Check the link and try again.",
      });
    },
  });

  if (editing) {
    return (
      <form
        className="flex min-w-0 flex-1 items-center gap-1.5 [-webkit-app-region:no-drag]"
        onSubmit={(e) => {
          e.preventDefault();
          const next = draft.trim();
          if (!next || linkMutation.isPending) return;
          linkMutation.mutate(next);
        }}
      >
        <Input
          nativeInput
          size="compact"
          font="mono"
          className="flex-1"
          ref={inputRef}
          value={draft}
          placeholder="Paste Vendoo draft link or item ID"
          aria-label="Vendoo draft link or item ID"
          disabled={linkMutation.isPending}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              setEditing(false);
            }
          }}
        />
        <Button
          type="submit"
          variant="outline"
          size="xs"
          disabled={linkMutation.isPending || !draft.trim()}
        >
          {linkMutation.isPending ? "Linking…" : "Link"}
        </Button>
        <Button
          variant="ghost"
          size="xs"
          disabled={linkMutation.isPending}
          onClick={() => setEditing(false)}
        >
          Cancel
        </Button>
      </form>
    );
  }

  return (
    <Button
      variant="ghost-muted"
      size="xs"
      className={cn("max-w-full", itemId && "font-mono text-[11px]")}
      title={itemId ? (url || itemId) : "Link an existing Vendoo draft"}
      onClick={() => setEditing(true)}
    >
      <Link2Icon />
      <span className="truncate">{itemId ? `vendoo ← ${String(itemId).slice(0, 8)}` : "Link Vendoo draft"}</span>
    </Button>
  );
}

/** Opens each marketplace's own listing page, alphabetically. */
function MarketplaceLinks({ urls }: { urls?: Record<string, string> }) {
  const entries = Object.entries(urls || {}).sort(([a], [b]) => a.localeCompare(b));
  if (entries.length === 0) return null;
  return (
    <span className="flex shrink-0 items-center gap-1">
      {entries.map(([id, url]) => (
        <a
          key={id}
          className="inline-flex rounded-sm opacity-80 hover:opacity-100 focus-visible:opacity-100"
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`Open the ${marketplaceName(id)} listing`}
        >
          <MarketplaceLogo id={id} label={`Open the ${marketplaceName(id)} listing`} size={16} />
        </a>
      ))}
    </span>
  );
}

function coerce(val: string): string | number | null {
  if (val === "") return null;
  if (!isNaN(Number(val)) && val.trim() !== "") return Number(val);
  return val;
}

/** Send stays off while generation (or a refine) is still writing the listing. */
export function sendToVendooEnabled(canSend: boolean, generating: boolean, pending = false): boolean {
  return canSend && !generating && !pending;
}

const ACTIVE_SEND_STATUSES = new Set(["queued", "awaiting_extension", "dispatched"]);

/** Prefer a job that is still running over a newer finished one on the same listing. */
export function preferredSendJob<T extends { status?: string | null; mode?: string | null }>(jobs: T[]): T | undefined {
  const sends = jobs.filter((job) => job.mode !== "native_marketplace");
  return sends.find((job) => ACTIVE_SEND_STATUSES.has(String(job.status || ""))) ?? sends[0];
}

/**
 * A second click while this listing's send is already on screen is not another
 * Chrome job. The progress card is the status; the 409 is noise.
 */
export function suppressOwnSendConflict(message: string | null | undefined, ownSendVisible: boolean): string {
  const text = String(message || "").trim();
  if (!text || !ownSendVisible) return text;
  if (/already queued or sending/i.test(text) || /busy with another Vendoo job/i.test(text) || /Chrome is busy with "/i.test(text)) {
    return "";
  }
  return text;
}

function SendToVendooButton({
  convId,
  canSend,
  generating,
  sendBlockers,
  listing,
  listingTitle,
  selectedMarketplaces,
  liveMarketplaces,
  vendooItemId,
  onAskChat,
  onSelectBlocker,
}: {
  convId: string;
  canSend: boolean;
  generating: boolean;
  sendBlockers: { field?: string; message?: string }[];
  listing?: Record<string, unknown>;
  listingTitle?: string;
  selectedMarketplaces?: string[];
  /** Marketplaces already carrying this item, named for the copy under Send. */
  liveMarketplaces?: string[];
  vendooItemId?: string | null;
  onAskChat?: (text: string) => void;
  onSelectBlocker: (field: string) => void;
}) {
  const queryClient = useQueryClient();
  const [error, setError] = React.useState<string | null>(null);
  const sendLock = React.useRef(false);
  const bound = Boolean(vendooItemId);

  // Vendoo's dropdown lists ride along in the fix prompts, so chat repairs a
  // rejected option (Depop material "Other") with one the dropdown really has.
  // The category's own schema wins over the static lists (Depop kids' ages).
  const { data: dropdownOptions } = useQuery({
    queryKey: ["catalog-dropdown-options"],
    queryFn: api.catalog.dropdownOptions,
    staleTime: Infinity,
  });
  const { data: schemaForms } = useQuery({
    queryKey: ["listing-fields", convId],
    queryFn: () => api.vendooApi.listingFields(convId),
    staleTime: 60_000,
  });
  const promptOptions = React.useMemo(
    () => withSchemaOptions(dropdownOptions?.forms, schemaForms?.forms),
    [dropdownOptions?.forms, schemaForms?.forms],
  );

  const { data: extStatus } = useQuery({
    queryKey: ["extension-status"],
    queryFn: api.extension.status,
    refetchInterval: () => pollMs(EXTENSION_STATUS_POLL_MS),
  });

  const { data: jobs } = useQuery({
    queryKey: ["jobs", convId],
    queryFn: () => api.jobs.list(convId),
    refetchInterval: (query) => jobsPollMs(query.state.data),
  });

  const listingJob = jobs?.find((j) => j.conversation_id === convId && j.status !== "cancelled" && j.mode !== "native_marketplace");
  const listingJobId = listingJob?.id;
  const chromeConnected = Boolean(extStatus?.connected);
  const hasDraft = Boolean(vendooItemId || listingJob?.vendoo_item_id || listingJob?.vendoo_url);

  // Same draft + fill-log cache the ask-chat button uses, so one prompt counts the full schema.
  const { data: draft } = useQuery({
    queryKey: vendooItemQueryKey(listingJobId || ""),
    queryFn: ({ signal }) => api.jobs.vendooItem(listingJobId!, { refresh: true, signal }),
    enabled: Boolean(listingJobId && hasDraft && chromeConnected),
    staleTime: VENDOO_ITEM_STALE_MS,
    retry: 1,
  });
  const { data: fillReport } = useQuery({
    queryKey: ["fill-log", listingJobId],
    queryFn: () => api.jobs.fillLog(listingJobId!),
    enabled: Boolean(listingJobId),
    refetchInterval: () => fillLogPollMs(listingJob?.status),
  });
  const { data: hiddenData } = useQuery({
    queryKey: ["settings-hidden-fields", convId],
    queryFn: () => api.settings.hiddenFields(convId),
  });

  const hidden = hiddenData || emptyHiddenFields();
  const { sourceForms, fromVendooDraft } = React.useMemo(
    () => sourceFormsForJob(
      mergeDraftItem(draft),
      fillReport,
      listing,
      selectedMarketplaces,
    ),
    [draft, fillReport, listing, selectedMarketplaces],
  );
  const visibleSourceForms = React.useMemo(
    () => withoutHiddenFields(sourceForms, hidden),
    [sourceForms, hidden],
  );
  const hiddenKeys = React.useMemo(() => hiddenKeySet(hidden), [hidden]);
  const fillFailures = React.useMemo(
    () => (fillReport ? fillFailureEntries(fillReport) : []).filter(
      (entry) => !hiddenKeys.has(
        hiddenFieldKey(entry.marketplace.toLowerCase(), normalizeFieldName(entry.field)),
      ),
    ),
    [fillReport, hiddenKeys],
  );
  const emptyFieldsCount = React.useMemo(
    () => askChatTargetCount(visibleSourceForms, listing, fillFailures, fromVendooDraft),
    [visibleSourceForms, listing, fillFailures, fromVendooDraft],
  );
  const fillEmptyPrompt = React.useMemo(
    () => (emptyFieldsCount
      ? askChatGapsPrompt(
        visibleSourceForms,
        fromVendooDraft,
        listing,
        fillFailures,
        promptOptions,
      )
      : undefined),
    [
      emptyFieldsCount,
      visibleSourceForms,
      fromVendooDraft,
      listing,
      fillFailures,
      promptOptions,
    ],
  );

  const sendMutation = useMutation({
    mutationFn: () => api.jobs.send(convId),
    onSuccess: () => {
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["queue"] });
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      toastManager.add({
        type: "success",
        title: "Added to queue",
        description: "You can move on. The Vendoo draft will be saved in the background.",
      });
    },
    onError: (err: Error) => setError(err.message || "Failed to queue send"),
  });

  const cancelMutation = useMutation({
    mutationFn: (jobId: string) => api.jobs.cancel(jobId),
    onSuccess: () => {
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
    onError: (err: Error) => {
      setError(err.message || "Failed to cancel");
      // The job on screen was stale enough to fail; show what it really is.
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });

  const existingJob = preferredSendJob(
    (jobs ?? []).filter(
      (j) => j.conversation_id === convId && j.status !== "cancelled" && j.status !== "imported",
    ),
  );
  const isSchemaProbe = existingJob?.mode === "schema_probe";
  const probeActive = Boolean(
    isSchemaProbe && ["queued", "awaiting_extension", "dispatched"].includes(String(existingJob?.status || "")),
  );
  const step = String(existingJob?.current_step || "");
  const apiCreateActive = Boolean(
    existingJob
    && ACTIVE_SEND_STATUSES.has(existingJob.status)
    && (step === "vendoo_api_create" || step.startsWith("vendoo_api_")),
  );
  // A browser fix the seller asked chat for is typing into the Vendoo form.
  // Send itself never gets here: it is a Vendoo API call, reported above.
  const formFillActive = Boolean(
    existingJob
    && !isSchemaProbe
    && !apiCreateActive
    && ["queued", "awaiting_extension", "dispatched"].includes(String(existingJob.status || "")),
  );
  const shownSendError = suppressOwnSendConflict(error, apiCreateActive);
  const extensionConnected = extStatus?.connected ?? false;
  const uniqueBlockers = React.useMemo(() => {
    const seen = new Set<string>();
    return sendBlockers.filter((err) => {
      const key = `${err.field || ""}|${err.message || ""}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return Boolean(err.message);
    });
  }, [sendBlockers]);
  const blockerText = uniqueBlockers.map((err) => err.message).join(" · ");
  const sendLabel = bound ? "Update Vendoo" : "Send to Vendoo";
  const live = liveMarketplaces || [];
  // Mid-generation Send would ship a half-written listing; otherwise the hint
  // says what Send does — and for a live item, what it deliberately does not.
  const sendEnabled = sendToVendooEnabled(canSend, generating, sendMutation.isPending) && !apiCreateActive && !formFillActive;
  const sendHint = generating
    ? "Wait for generation to finish before sending."
    : !bound
      ? "Creates a Vendoo draft and links it. Nothing is published."
      : live.length
        ? "Writes changed fields onto the Vendoo form. "
          + (live.length > 2
            ? `The live listings on all ${live.length} marketplaces keep`
            : `The live ${joinMarketplaces(live)} ${live.length === 1 ? "listing keeps" : "listings keep"}`)
          + " the old version until you delist and relist in Vendoo. Keeps the photos already in Vendoo."
        : "Writes changed fields onto the linked Vendoo draft. Keeps the photos already in Vendoo. Nothing is published.";

  const startSend = () => {
    if (sendLock.current || sendMutation.isPending) return;
    if (generating) {
      setError("Wait for generation to finish before sending.");
      return;
    }
    if (!canSend) {
      setError(blockerText || "Listing is not ready. Add a title, description, price, and at least one photo.");
      return;
    }
    sendLock.current = true;
    setError(null);
    sendMutation.mutate(undefined, {
      onSettled: () => {
        sendLock.current = false;
      },
    });
  };

  if ((probeActive || apiCreateActive || formFillActive) && existingJob) {
    const stepKey = String(existingJob.current_step || "");
    const label = probeActive
      ? "Discovering fields"
      : apiCreateActive
        ? (bound ? "Updating Vendoo" : "Sending to Vendoo")
        : "Filling fields in the browser";
    const statusText = probeActive
      ? (existingJob.current_step || existingJob.status)
      : apiCreateActive
        ? sendProgressLabel(stepKey, existingJob.send_progress)
        : `${existingJob.status}: ${existingJob.current_step || "queued"}`;
    return (
      <div className="flex flex-col gap-2 rounded-xl border bg-card p-3 text-sm">
        <div className="flex items-start justify-between gap-3 max-[900px]:flex-col max-[900px]:items-stretch">
          <div className="min-w-0 flex-1">
            <div className="font-medium text-foreground">{label}</div>
            <div className="mt-1 text-muted-foreground text-xs">
              {apiCreateActive ? (
                <SendProgress label={statusText} startedAt={existingJob.started_at || existingJob.created_at}
                  photos={stepKey === "vendoo_api_photos" ? existingJob.send_progress : undefined} />
              ) : statusText}
              {probeActive && (
                <div className="mt-1 text-muted-foreground text-xs">
                  Matching the Vendoo category and reading marketplace fields into this listing.
                </div>
              )}
            </div>
          </div>
          <Button
            variant="outline"
            size="xs"
            className="shrink-0 max-[900px]:w-full"
            disabled={cancelMutation.isPending}
            onClick={() => { setError(null); cancelMutation.mutate(existingJob.id); }}
          >
            {cancelMutation.isPending ? "Cancelling…" : "Cancel"}
          </Button>
        </div>
        {existingJob.last_error && (
          <CopyableLlmError
            text={existingJob.last_error}
            prompt={jobErrorPrompt(existingJob.last_error, listingTitle, null, promptOptions)}
            onAskChat={onAskChat}
            emptyFieldsCount={emptyFieldsCount}
            emptyFieldsPrompt={fillEmptyPrompt}
          />
        )}
        {shownSendError && (
          <CopyableLlmError
            text={shownSendError}
            prompt={jobErrorPrompt(shownSendError, listingTitle, null, promptOptions)}
            onAskChat={onAskChat}
            emptyFieldsCount={emptyFieldsCount}
            emptyFieldsPrompt={fillEmptyPrompt}
          />
        )}
        {!existingJob.last_error && !shownSendError && (
          // No error to show, but empty fields still deserve their fill button.
          <CopyableLlmError
            text=""
            prompt=""
            onAskChat={onAskChat}
            emptyFieldsCount={emptyFieldsCount}
            emptyFieldsPrompt={fillEmptyPrompt}
          />
        )}
      </div>
    );
  }

  if (!extensionConnected) {
    return (
      <div className="grid justify-items-center gap-2 text-center">
        <div className="text-muted-foreground text-xs">Chrome is not connected yet.</div>
        <ConnectChromeButton />
        {!generating && <ListingBlockers issues={uniqueBlockers} onSelect={onSelectBlocker} />}
      </div>
    );
  }

  const displayError = error || (!canSend ? blockerText : "");
  const blockerPrompt = validationErrorsPrompt(
    uniqueBlockers.length ? uniqueBlockers : [{ message: displayError }],
    listingTitle,
    promptOptions,
  );
  // The grouped list below already names these issues. The red dump would repeat them.
  const blockersListed = !error && !canSend && uniqueBlockers.length > 0;
  const statusLine = sendEnabled
    ? sendHint
    : generating
      ? "Wait for generation to finish before sending."
      : uniqueBlockers.length
        ? `${uniqueBlockers.length} ${uniqueBlockers.length === 1 ? "issue" : "issues"} to fix before sending.`
        : sendHint;
  // Rendered even without an error so the fill button stays while fields are empty.
  const errorCard = (
    <CopyableLlmError
      text={displayError}
      hideDetails={blockersListed}
      issueCount={blockersListed ? uniqueBlockers.length : 0}
      prompt={uniqueBlockers.length
        ? blockerPrompt
        : jobErrorPrompt(displayError, listingTitle, null, promptOptions)}
      onAskChat={onAskChat}
      emptyFieldsCount={emptyFieldsCount}
      emptyFieldsPrompt={fillEmptyPrompt}
    />
  );

  return (
    <div className="flex flex-col gap-2">
      <Button
        variant={sendEnabled ? "default" : "secondary"}
        className={cn(
          "w-full",
          // Unbound sends create the Vendoo draft, so a ready button carries Vendoo's green.
          // A blocked send stays quiet so the issue count is what reads as the next step.
          !bound && sendEnabled && "border-(--color-vendoo) bg-(--color-vendoo) text-white shadow-none [:hover,[data-pressed]]:bg-(--color-vendoo)/90",
        )}
        disabled={!sendEnabled}
        aria-busy={sendMutation.isPending}
        title={generating
          ? "Wait for generation to finish before sending"
          : bound
            ? "Write this listing's changed fields onto the Vendoo draft"
            : "Create a Vendoo draft with marketplace fields filled"}
        onClick={startSend}
      >
        {sendMutation.isPending ? (
          <>
            <Spinner aria-hidden="true" role={undefined} />
            {bound ? "Updating…" : "Sending…"}
          </>
        ) : sendLabel}
      </Button>
      {!sendMutation.isPending && <div className="text-center text-muted-foreground text-xs">{statusLine}</div>}
      {sendMutation.isPending && (
        <SendProgress label={bound ? "Writing marketplace forms to Vendoo…" : "Starting the send…"} />
      )}
      {errorCard}
      {!generating && <ListingBlockers issues={uniqueBlockers} onSelect={onSelectBlocker} />}
    </div>
  );
}
