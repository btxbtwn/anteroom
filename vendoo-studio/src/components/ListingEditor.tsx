import { GENERAL_LISTING_FIELDS } from "../generalListingFields";
import editorFields from "../../../skills/list-this/references/listing-editor-fields.json";
import { SellwildAccountChoices } from "./SellwildAccountChoices";
import React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { EbayVariationAspect, EbayVariations, Job, ListingData, MarketplaceForm, NativeInventoryGroup, NativeInventoryRow } from "../api/types";
import { cloneListing, editorValuePresent, expandJoinedChoices, formFieldText, formFieldValues, getListingEditorValue, marketplaceDisplayValue, setListingEditorValue, squashFieldKey } from "../listingPaths";
import { listingEdits } from "../api/listingEdits";
import { DIRECT_FORM_MARKETS, nativeFormOptions, prefetchListingForm } from "../api/listingForms";
import { PhotoTray } from "./PhotoTray";
import { useListingSessionActive } from "./listingSession";
import { VintedBrandSelect } from "./VintedBrandSelect";
import { VintedCategorySelect } from "./VintedCategorySelect";
import { NativeInventoryEditor } from "./NativeInventoryEditor";
import { EbayVariationsEditor, normalizeVariations, variationTotal } from "./EbayVariationsEditor";
import { MarketplaceMedia } from "./MarketplaceMedia";
import { MEDIA_MARKETPLACES } from "../marketplaceMedia";
import { ItemDetails } from "./ItemDetails";
import { ConnectChromeButton } from "./ConnectChromeButton";
import { SendProgress, sendProgressLabel } from "./SendProgress";
import { ListingBlockers } from "./ListingBlockers";
import { ListingHistory } from "./ListingHistory";
import { blockerTarget, isEbayInventoryLocationEdit, matchingEditorField } from "./listingChanges";
import { useListingSync } from "./useListingSync";
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
import { Radio, RadioGroup } from "./ui/radio-group";
import { Toggle } from "./ui/toggle";
import { RegenerateListingButton } from "./ClearListingButton";
import { MarketplaceMessagesContext, uniqueMarketplaceMessages } from "./marketplaceMessages";
import { MarketplacesLayout, useReportFieldsShown } from "./MarketplacesSection";
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
import { ListingActionsMenu, ListingBrowserButton, ListingReviewActions } from "./ListingReviewActions";
import { type ListingReviewTab } from "./ListingReviewTabs";
import type { BulkListingUploadResult } from "../bulkPhotoUpload";
import { BookmarkCheckIcon, BookmarkPlusIcon, HistoryIcon, Link2Icon, RotateCcwIcon } from "lucide-react";
import { cn } from "../lib/utils";
import { pathCategoryChoices } from "./categoryTree";
import { CategoryTreeSelect, DependentSelectPair, EbayCategorySelect, FieldLabel, InspectorSection, SearchableSelect, SearchableMultiSelect, SegmentedTabs, type CascadeDepartment } from "./ListingFormControls";
import { groupFieldsIntoSections } from "./listingFieldSections";
import { categorySpecificFieldsStale, nativeFieldVisible, nativeFormCategoryKey, nativeFormNotes, presentNativeField, resolvedEditorValue } from "./nativeFormStability";
import { gapsByField, rowForField } from "./nativeFieldWarnings";
import type { NativeMarketplace } from "../api/nativeMarketplaces";
import { Alert, AlertDescription, AlertTitle } from "./ui/alert";
import { Badge } from "./ui/badge";
import { Button, InlineButton } from "./ui/button";
import { Input } from "./ui/input";
import { InputGroup, InputGroupAddon, InputGroupInput } from "./ui/input-group";
import { Spinner } from "./ui/spinner";
import { Switch } from "./ui/switch";
import { Textarea } from "./ui/textarea";
import { Tooltip, TooltipPopup, TooltipTrigger } from "./ui/tooltip";
import { savedDefaultFor } from "./fieldDefaults";

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
  /** The listing's chat, floated over the form's corner on desktop. */
  chat?: React.ReactNode;
}

interface EditorField {
  key: string;
  label: string;
  type?: string;
  minValue?: number;
  maxValue?: number;
  step?: number;
  defaultValue?: string | string[] | number | boolean | NativeInventoryRow[] | EbayVariations;
  inventoryGroups?: NativeInventoryGroup[];
  variationAspects?: EbayVariationAspect[];
  native?: boolean;
  optionValues?: Record<string, string>;
  clears?: string[];
  help?: string;
  valueConstraints?: Record<string, AspectConstraintRule[]>;
  required?: boolean;
  options?: string[];
  locked?: boolean;
  multi?: boolean;
  selectionOnly?: boolean;
  maxValues?: number | null;
  maxLength?: number | null;
  optionsSource?: "marketplace" | "vendoo" | "reference" | "saved";
  /** Choosing another category adds or removes this row. */
  categorySpecific?: boolean;
  /** The marketplace form's heading for this field. */
  section?: string;
  /** The button the marketplace hides this field behind. */
  reveal?: string;
  slotLabels?: string[];
  /** File-dialog filter from the marketplace's photo area (Depop: JPEG and PNG). */
  accept?: string;
  hashtagMax?: number;
  optionGroups?: Record<string, string>;
  width?: "half" | "third" | "quarter";
  /** The control itself is half the row, stacked, as on Poshmark's price and private fields. */
  controlWidth?: "half";
  cascade?: CascadeDepartment[];
  optionAmounts?: Record<string, number>;
  /** A saved value the picker cannot show, and the choice it stands for. */
  optionAliases?: Record<string, string>;
  group?: string;
  groupHelp?: string;
  sectionHelp?: string;
  placeholder?: string;
  prefix?: string;
  suffix?: string;
  /** The seller can keep this value as the default every new listing starts with. */
  saveAsDefault?: boolean;
  labelHidden?: boolean;
  counter?: boolean;
  presentation?: "radio" | "radio-row" | "button";
  optionHelp?: Record<string, string>;
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
  chat,
}: Props) {
  const queryClient = useQueryClient();
  const edits = React.useMemo(() => listingEdits(queryClient, convId), [queryClient, convId]);
  const editState = React.useSyncExternalStore(edits.subscribe, edits.getSnapshot, edits.getSnapshot);
  const unsaved = editState.saving || Boolean(editState.draft);
  const [editTab, setEditTab] = React.useState("general");
  const [historyOpen, setHistoryOpen] = React.useState(false);
  const [fieldTarget, setFieldTarget] = React.useState<{ field: string; sequence: number } | null>(null);
  const editorRef = React.useRef<HTMLDivElement>(null);
  const sessionActive = useListingSessionActive();
  const { data: jobs, isLoading: jobsLoading } = useQuery({
    queryKey: ["jobs", convId],
    queryFn: () => api.jobs.list(convId),
    refetchInterval: (query) => (sessionActive ? jobsPollMs(query.state.data) : false),
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
    enabled: !unsaved,
    refetchInterval: sessionActive && schemaProbeActive ? 3000 : false,
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
  const directMarketplaces = conversation?.direct_marketplaces ?? [];

  React.useEffect(() => {
    if (listingJob?.mode === "schema_probe" && listingJob.status === "completed") {
      queryClient.invalidateQueries({ queryKey: ["listing", convId] });
      queryClient.invalidateQueries({ queryKey: ["fill-log"] });
      queryClient.invalidateQueries({ queryKey: ["conversation", convId] });
    }
  }, [listingJob?.mode, listingJob?.status, listingJob?.id, convId, queryClient]);

  // Shared with the Marketplaces rail. An item already on Facebook shows Facebook even when it is not a selected marketplace.
  const { data: liveListings } = useQuery({
    queryKey: ["marketplace-listings", convId],
    queryFn: () => api.marketplaceListings.get(convId),
  });
  const onFacebook = Boolean(liveListings?.facebook);
  const tabs = React.useMemo(() => {
    const selected = new Set(marketplaceSettings?.selected ?? ["ebay", "etsy", "poshmark", "mercari", "depop"]);
    if (onFacebook) selected.add("facebook");
    return [
      "general",
      ...["ebay", "poshmark", "mercari", "depop", "etsy", "grailed", "vinted", "facebook", "sellwild"].filter((id) => id === "vinted" || selected.has(id)),
    ];
  }, [marketplaceSettings?.selected, onFacebook]);

  React.useEffect(() => {
    if (!data?.current_revision_id || unsaved || !sessionActive) return;
    let cancelled = false;
    const remaining = tabs.filter(tab => tab !== "general");
    const warm = async () => {
      while (!cancelled && remaining.length) {
        const tab = remaining.shift()!;
        await prefetchListingForm(queryClient, convId, tab, data.listing, data.current_revision_id!);
      }
    };
    // Leave connections available for the selected form and the seller's edits.
    void Promise.all([warm(), warm()]);
    return () => { cancelled = true; };
  }, [queryClient, convId, data, tabs, unsaved, sessionActive]);

  React.useEffect(() => {
    if (!tabs.includes(editTab)) setEditTab("general");
  }, [editTab, tabs]);

  const openBlocker = (field: string, marketplace?: string) => {
    if (isEbayInventoryLocationEdit(field)) {
      onEditInventoryLocation?.();
      return;
    }
    const target = blockerTarget(field, marketplace);
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
    refetchInterval: (query) => (sessionActive ? activityPollMs(Boolean(query.state.data?.busy)) : false),
  });
  const generating = chatBusy || Boolean(activity?.busy) || schemaProbeActive;
  const askChat = generating ? undefined : onAskChat;

  const listing = editState.draft ?? data?.listing ?? {};
  const listingTitle = String(listing.title || "Listing");
  const importedItemId = listingJob?.vendoo_item_id || notesVendooItemId(conversation?.notes);
  const importedUrl = listingJob?.vendoo_url || notesVendooUrl(conversation?.notes);
  const { vendooConflict } = useListingSync(convId, vendooEnabled && Boolean(importedItemId));

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
        {/* Desktop's tab and titlebar already name the listing. */}
        <h2
          className="pywebview-drag-region hidden min-w-0 truncate font-semibold text-[15px] text-foreground max-[900px]:block"
          title={listingTitle}
        >
          {listingTitle}
        </h2>
        {/* Only Vendoo's controls earn a row of their own; sync and More sit at the rail's foot. */}
        {vendooEnabled && <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-muted-foreground text-xs">
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
          {conversation?.unsent_edits ? (
            <>
              <Badge
                variant="info"
                title={
                  directMarketplaces.length
                    ? `This listing has changed since it was listed on ${directMarketplaces.map(marketplaceName).join(", ")}. Update it there from Marketplaces.`
                    : vendooEnabled
                      ? "This listing has changed since Vendoo last had it. Update Vendoo writes it onto the Vendoo form."
                      : "This listing has changed since it was last saved."
                }
              >
                Unsent edits
              </Badge>
              {/* Discard pulls Vendoo's copy, which a directly listed item no longer follows. */}
              {vendooEnabled && !directMarketplaces.length && (listingJob?.vendoo_item_id || importedItemId) ? (
                <InlineButton
                  tone="muted"
                  className="text-xs"
                  disabled={discardUnsent.isPending || generating}
                  title={vendooConflict
                    ? "Vendoo's copy and this listing were both edited, so this listing was kept. Discard takes Vendoo's copy instead."
                    : "Replace this listing with the copy Vendoo has"}
                  onClick={() => void confirmDiscardUnsent()}
                >
                  {discardUnsent.isPending ? "Pulling…" : "Discard"}
                </InlineButton>
              ) : null}
            </>
          ) : null}
          <ListingReviewActions convId={convId}>
            <ListingBrowserButton
              convId={convId}
              browserOpen={browserOpen}
              onOpenBrowser={onOpenBrowser}
            />
          </ListingReviewActions>
        </div>}
      </div>

      <SoldElsewhereNotice
        convId={convId}
        openInVendoo={
          vendooEnabled && listingJob ? (
            <OpenListingButton jobId={listingJob.id} vendooItemId={importedItemId} vendooUrl={importedUrl} />
          ) : null
        }
      />


      {vendooEnabled && relistMarketplaces.length > 0 && (
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

      {vendooEnabled && schemaProbeActive && (
        <Alert variant="info" role="status" className="mx-3 mt-2.5 shrink-0">
          <Spinner />
          <AlertDescription>
            Discovering Vendoo marketplace fields for this category… Empty keys will appear on each marketplace as they arrive.
          </AlertDescription>
        </Alert>
      )}

      <div className={cn("listing-form-area", chat && "has-chat")}>
      <MarketplacesLayout key={convId} convId={convId} revisionId={data?.current_revision_id || null} title={listingTitle}
        disabled={generating || unsaved} etsyEligible={etsyEligible} onAskChat={askChat} onEditField={openBlocker} ids={tabs}
        onPrepare={(tab) => {
          if (!unsaved && data?.current_revision_id) void prefetchListingForm(queryClient, convId, tab, data.listing, data.current_revision_id);
        }}
        selected={reviewTab === "forms" && editTab !== "general" ? editTab : "general"}
        railActions={<RegenerateListingButton convId={convId} variant="ghost-muted" onRegenerated={onCleared} />}
        railStatus={<>
          {editState.saving && <span role="status" className="text-xs text-muted-foreground">Saving…</span>}
          <span className="ms-auto"><ListingActionsMenu convId={convId} onCleared={onCleared} /></span>
        </>}
        onSelect={(id) => {
          setFieldTarget(null);
          if (id === "general") onReviewTabChange("input");
          else onReviewTabChange("forms");
          setEditTab(id);
        }}>
        <>
          {editState.error && <div role="alert" className="flex flex-wrap items-center gap-2 text-xs text-destructive-foreground">
            <span>Edits not saved: {editState.error}</span>
            <InlineButton onClick={edits.retry}>Retry save</InlineButton>
            <InlineButton onClick={async () => {
              if (await confirmDialog("Reload the saved listing and discard your unsaved edits?", { confirmLabel: "Discard and reload", variant: "destructive" })) edits.discard();
            }}>Discard and reload</InlineButton>
          </div>}
          {(reviewTab !== "forms" || editTab === "general") && (
            <>
              <PhotoTray convId={convId} onBulkListingsCreated={onBulkListingsCreated} />
              <ItemDetails convId={convId} />
            </>
          )}
          {/* A direct marketplace's form shows its photos where its own page has them. */}
          {reviewTab === "forms" && MEDIA_MARKETPLACES.has(editTab) && !DIRECT_FORM_MARKETS.has(editTab) && listing && (
            <MarketplaceMedia key={editTab} convId={convId} marketplace={editTab} listing={listing}
              disabled={generating} onChange={edits.save} />
          )}
          <StructuredEditor
            convId={convId}
            listing={listing}
            revisionId={data?.current_revision_id}
            tab={reviewTab === "forms" && editTab !== "general" ? editTab : "general"}
            target={fieldTarget}
            onAskChat={askChat}
            disabled={generating}
            saving={unsaved}
            onChange={edits.save}
          />
          {/* Past versions are a look-back, so they close the form instead of crowding its header. */}
          <div className="flex border-t pt-2">
            <Button variant="ghost-muted" size="xs" disabled={!data?.current_revision_id} onClick={() => { setFieldTarget(null); setHistoryOpen(true); }}>
              <HistoryIcon />
              History
            </Button>
          </div>
        </>
      </MarketplacesLayout>
      {chat}
      </div>

      {vendooEnabled && <div className="listing-send-footer flex max-h-[min(42vh,320px)] min-w-0 shrink-0 flex-col overflow-y-auto overflow-x-hidden border-t px-3 py-2.5 max-[900px]:max-h-[min(28vh,220px)] max-[900px]:pb-[max(10px,env(safe-area-inset-bottom,0px))]">
        <SendToVendooButton
          convId={convId}
          canSend={data?.can_send ?? false}
          generating={generating}
          saving={unsaved}
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
        busy={generating || unsaved || Boolean(jobs?.some((job) => ACTIVE_SEND_STATUSES.has(job.status)))} onClose={() => setHistoryOpen(false)} />}
    </div>
  );
}

/** A field's label without its required marker or page punctuation. */
function fieldName(label: string): string {
  return label.replace(/\s*\*$/, "").replace(/:\s*$/, "").trim() || "This field";
}

/**
 * Saves one field's value as the answer every new listing starts with.
 * The bookmark is the field's own state: outline means not saved, filled means
 * saved. Clicking a filled one stops using it. Nothing is saved until a value
 * is there, so the control stays out of the label row until it can do something.
 */
function FieldDefaultControl({
  label,
  value,
  saved,
  busy,
  onSave,
  onClear,
}: {
  label: string;
  value: string;
  saved: boolean;
  busy: boolean;
  onSave: () => void;
  onClear: () => void;
}) {
  if (!value.trim()) return null;
  const name = fieldName(label);
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            type="button"
            size="icon-micro"
            variant="ghost-muted"
            className="-my-1 shrink-0"
            aria-label={saved
              ? `Remove the default for ${name}`
              : `Save ${name} as the default for new listings`}
            aria-pressed={saved}
            disabled={busy}
            onMouseDown={(event) => event.preventDefault()}
            onClick={saved ? onClear : onSave}
          />
        }
      >
        {busy
          ? <Spinner size="xs" />
          : saved
            ? <BookmarkCheckIcon className="size-3.5 text-primary" />
            : <BookmarkPlusIcon className="size-3.5" />}
      </TooltipTrigger>
      <TooltipPopup side="right" sideOffset={6}>
        {saved
          ? "Saved as the default for new listings. Click to remove it."
          : "Save as the default for new listings"}
      </TooltipPopup>
    </Tooltip>
  );
}

export function StructuredEditor({
  convId,
  listing,
  revisionId,
  tab,
  onChange,
  target,
  disabled,
  saving = false,
  onAskChat,
}: {
  convId: string;
  listing: ListingData;
  revisionId?: string | null;
  tab: string;
  onChange: (v: ListingData) => void;
  target: { field: string; sequence: number } | null;
  disabled?: boolean;
  saving?: boolean;
  onAskChat?: (text: string) => void;
}) {
  const formRef = React.useRef<HTMLFieldSetElement>(null);
  const editingDisabled = React.useRef(Boolean(disabled));
  editingDisabled.current = Boolean(disabled);
  const emitChange = (updated: ListingData) => {
    if (!editingDisabled.current) onChange(updated);
  };
  const nativeTab = DIRECT_FORM_MARKETS.has(tab);
  // The form's rows come from the category. A price, brand, format, or shipping
  // edit must not start a new read: that used to drop every row until the form
  // answered, leaving only the marketplace price on screen. A new category,
  // chosen here or written onto the listing, is what loads a new set of rows.
  // A refill can replace the saved definition without changing the category;
  // include it so its refreshed dependent choices reach the editor too.
  const nativeQuery = useQuery({
    ...nativeFormOptions(convId, tab, listing, revisionId),
    enabled: nativeTab && !saving,
    placeholderData: (previous, previousQuery) => {
      const key = previousQuery?.queryKey;
      if (!previous || previous.marketplace !== tab || key?.[1] !== convId || key?.[2] !== tab) return undefined;
      return previous;
    },
  });
  // What List would say about a field is said under that field, where the seller
  // is typing. The same gaps stay beside List, which is what a blocking one holds.
  const preflight = useQuery({
    queryKey: ["native-preflight", convId, revisionId],
    queryFn: () => api.nativeMarketplaces.preflight(convId),
    enabled: nativeTab && Boolean(revisionId) && !saving,
    staleTime: 15_000,
    placeholderData: (previous) => previous,
  });
  // The category schema is this form. It is read again when the category
  // changes, and a price or brand edit does not fetch it again.
  const formsQuery = useQuery({
    queryKey: ["listing-fields", convId],
    queryFn: () => api.vendooApi.listingFields(convId),
    enabled: !nativeTab,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    placeholderData: (previous) => previous,
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
  const { data: savedDefaults } = useQuery({
    queryKey: ["settings-listing-defaults"],
    queryFn: api.settings.listingDefaults,
    staleTime: 30_000,
  });
  const queryClient = useQueryClient();
  const saveDefault = useMutation({
    mutationFn: (input: { path: string; value: string; label: string; fieldLabel: string }) =>
      api.settings.setListingDefault({ path: input.path, value: input.value, label: input.label }),
    onSuccess: (payload, input) => {
      queryClient.setQueryData(["settings-listing-defaults"], { defaults: payload.defaults });
      toastManager.add({
        type: "success",
        title: `${fieldName(input.fieldLabel)} saved as the default`,
        description: `New listings start with ${input.label}.`,
      });
    },
    onError: (err: Error) => {
      toastManager.add({ type: "error", title: "Could not save the default", description: err.message });
    },
  });
  const clearDefault = useMutation({
    mutationFn: (input: { path: string; fieldLabel: string }) =>
      api.settings.clearListingDefault(input.path),
    onSuccess: (payload, input) => {
      queryClient.setQueryData(["settings-listing-defaults"], { defaults: payload.defaults });
      toastManager.add({
        type: "success",
        title: `${fieldName(input.fieldLabel)} default removed`,
        description: "New listings will ask for this again.",
      });
    },
    onError: (err: Error) => {
      toastManager.add({ type: "error", title: "Could not remove the default", description: err.message });
    },
  });
  // Direct listing forms are the category's fields plus that marketplace's standard
  // rows. They do not expand. Other editors can still open every saved field.
  const categoryForm = nativeTab ? nativeQuery.data : forms?.forms.find((form) => form.marketplace === tab);
  const loadedCategoryId = nativeTab
    ? (nativeQuery.isPlaceholderData ? nativeQuery.data?.category_id : undefined)
    : (formsQuery.isPlaceholderData ? forms?.forms.find((form) => form.marketplace === tab)?.category_id : undefined);
  const categoryStale = categorySpecificFieldsStale(
    loadedCategoryId,
    nativeFormCategoryKey(listing, tab),
    nativeTab ? nativeQuery.isPlaceholderData : formsQuery.isPlaceholderData,
  );
  const fields = React.useMemo(
    () => {
      // No form yet: don't invent a lone marketplace-price row while the real rows load.
      if (nativeTab && !nativeQuery.data) return [];
      const source = nativeTab ? (nativeQuery.data ? [nativeQuery.data] : []) : forms?.forms;
      const built = fieldsForMarketplaceForm(listing, tab, source);
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
    [nativeTab, nativeQuery.data, forms, listing, tab, dropdownOptions?.forms, inventoryLocation?.location],
  );
  const [drafts, setDrafts] = React.useState<Record<string, string>>({});
  // The seller's open or closed choice for "Show more" and "See … options". A saved value starts open.
  const [revealOverride, setRevealOverride] = React.useState<ReadonlyMap<string, boolean>>(new Map());
  const revealed = React.useMemo(() => {
    const open = new Set<string>();
    for (const field of fields) {
      if (!field.reveal || !field.section) continue;
      const value = editorFieldValue(listing, field, tab);
      if (value === false || value === "false" || !editorValuePresent(value)) continue;
      open.add(`${tab}|${field.section}|${field.reveal}`);
    }
    for (const [id, on] of revealOverride) {
      if (on) open.add(id);
      else open.delete(id);
    }
    return open;
  }, [fields, listing, tab, revealOverride]);
  // Go to an "Edit …" target once. The rows rebuild on every save, and
  // following the target again then pulled the seller back from whatever they were editing.
  const reachedTarget = React.useRef<number | null>(null);
  React.useEffect(() => {
    if (!target || reachedTarget.current === target.sequence) return;
    // A marketplace's answer can name the listing field its form row comes from.
    const key = matchingEditorField(fields, target.field) ?? rowForField(tab, target.field, fields);
    if (!key) return;
    const input = [...(formRef.current?.querySelectorAll<HTMLElement>("[data-listing-field]") || [])].find((element) => element.dataset.listingField === key);
    if (!input) return;
    reachedTarget.current = target.sequence;
    input.scrollIntoView({ block: "center" });
    input.focus();
  }, [target, fields, tab]);

  // Derive saved values during render: a cached form is filled on its first frame.
  const local = React.useMemo(() => {
    const init: Record<string, string | string[]> = {};
    fields.forEach((f) => {
      if (f.type === "inventory" || f.type === "variations" || f.type === "photos") return;
      const value = editorFieldValue(listing, f, tab);
      const label = (v: string) => Object.entries(f.optionValues || {}).find(([, id]) => id === v)?.[0] ?? v;
      const followed = marketplacePriceFollowsGeneral(listing, f, tab);
      const shown = followed && listing?.price != null
        ? formFieldText(listing.price)
        : f.multi ? formFieldValues(value, f.options).map(label) : label(formFieldText(value));
      init[f.key] = f.type !== "boolean" && f.key === "ebay_specifics.bestOffer.enabled"
        ? (/^(no|false|0|off)$/i.test(formFieldText(shown)) ? "No" : "Yes")
        : shown;
      const draft = drafts[`${tab}|${f.key}`];
      if (draft !== undefined) init[f.key] = draft;
    });
    return init;
  }, [listing, tab, fields, drafts]);

  const fieldDefault = (field: EditorField) => {
    if (!field.saveAsDefault || field.locked) return null;
    const shown = formFieldText(local[field.key]);
    const stored = field.optionValues?.[shown] ?? shown;
    const saved = savedDefaultFor(savedDefaults?.defaults, field.key, stored, shown);
    const busy = Boolean(disabled) || (saveDefault.isPending && saveDefault.variables?.path === field.key)
      || (clearDefault.isPending && clearDefault.variables?.path === field.key);
    return (
      <FieldDefaultControl
        label={field.label}
        value={shown}
        saved={Boolean(saved)}
        busy={busy}
        onSave={() => {
          if (editingDisabled.current || !shown.trim()) return;
          // The click keeps the input's focus, so a typed answer is saved here
          // rather than waiting for a blur that will not come. A closed choice
          // already saves itself when selected, and writing its id now would pin
          // a weight-specific class to this listing.
          if (!field.selectionOnly
            && String(getListingEditorValue(listing, field.key) ?? "") !== String(stored)) {
            commitField(field.key, shown);
          }
          saveDefault.mutate({ path: field.key, value: String(stored), label: shown, fieldLabel: field.label });
        }}
        onClear={() => { if (!editingDisabled.current) clearDefault.mutate({ path: field.key, fieldLabel: field.label }); }}
      />
    );
  };

  const handleBlur = (key: string) => {
    if (editingDisabled.current || local[key] == null) return;
    setDrafts(current => {
      const next = { ...current };
      delete next[`${tab}|${key}`];
      return next;
    });
    const field = fields.find((entry) => entry.key === key);
    if (field?.locked) return;
    const text = formFieldText(local[key]);
    if (text.trim() === "[object Object]") return;
    const alias = field?.optionAliases?.[text.trim()];
    // A saved value the picker cannot hold ("S" where Mercari lists "S (4-6)")
    // is saved as the choice it stands for, even when nothing else changed.
    if (alias) {
      const updated = cloneListing(listing);
      setListingEditorValue(updated, key, alias);
      emitChange(updated);
      return;
    }
    // Compare with the labeled value the control was showing. The form's original
    // value stays in defaultValue and must not block a later edit that matches it.
    const labelOf = (value: string) => Object.entries(field?.optionValues || {}).find(([, id]) => id === value)?.[0] ?? value;
    const followed = field ? marketplacePriceFollowsGeneral(listing, field, tab) : false;
    const raw = field ? editorFieldValue(listing, field, tab) : getListingEditorValue(listing, key);
    const current = followed && listing?.price != null ? formFieldText(listing.price) : labelOf(formFieldText(raw));
    if (text === current) return;
    const updated = cloneListing(listing);
    setListingEditorValue(updated, key, field?.type === "number" ? coerce(text) : text);
    emitChange(updated);
  };

  const commitField = (key: string, value: string | string[] | boolean) => {
    if (editingDisabled.current || fields.find((field) => field.key === key)?.locked) return;
    if (typeof value === "string" && value.trim() === "[object Object]") return;
    const updated = cloneListing(listing);
    const field = fields.find((entry) => entry.key === key);
    const stored = typeof value === "string" ? field?.optionValues?.[value] ?? (field?.type === "number" ? coerce(value) : value)
      : Array.isArray(value) ? value.map(label => field?.optionValues?.[label] ?? label) : value;
    setListingEditorValue(updated, key, stored);
    if (key === "poshmark_specifics.nativeSizeId") {
      setListingEditorValue(updated, "poshmark_specifics.sizeSetId", fields.find((row) => row.key === "poshmark_specifics.sizeSetId")?.defaultValue || "");
    }
    for (const dependent of field?.clears || []) setListingEditorValue(updated, dependent,
      dependent.endsWith(".attributes") ? {} : dependent.endsWith(".inventoryRows") ? [] : "");
    emitChange(updated);
  };

  const setFieldDraft = (key: string, value: string) => {
    if (editingDisabled.current || fields.find((field) => field.key === key)?.locked) return;
    setDrafts((current) => ({ ...current, [`${tab}|${key}`]: value }));
  };

  const viewContext = { tab, listing, local, fields, categoryStale };
  const viewFields = fields.map((field) => presentNativeField(field, viewContext));
  const renderedFields = viewFields.filter((field) => nativeFieldVisible(field, viewContext));
  // Placeholder checks describe the previous revision, before its fields were filled.
  const gaps = nativeTab && !preflight.isPlaceholderData && !preflight.isError
    ? preflight.data?.marketplaces?.[tab as NativeMarketplace]?.gaps ?? [] : [];
  const shownMessages = React.useContext(MarketplaceMessagesContext);
  const fieldWarnings = gapsByField(tab, gaps, renderedFields);
  // List's photo answer belongs with the photos, on the control that sends them.
  const photoWarnings = gapsByField(tab, gaps, [{ key: `marketplace_photos.${tab}` }]).get(`marketplace_photos.${tab}`) ?? [];
  // The answers read here are not read again beside List. A gap counts when this
  // form resolved it to a row, which is the same test that put it under that row.
  useReportFieldsShown(tab, gaps.filter((gap) => fieldWarnings.has(rowForField(tab, String(gap.field || ""), renderedFields))
    || (photoWarnings.length > 0 && String(gap.field || "") === "photos")).map((gap) => String(gap.field || "")));
  const formNotes = nativeTab
    ? uniqueMarketplaceMessages([...(nativeQuery.data?.notes ?? []), ...nativeFormNotes(tab, listing)], [...shownMessages, ...gaps.map((gap) => gap.message)])
    : [];
  // A marketplace form that names its headings keeps them, with its order and row layout.
  const pageLayout = nativeTab && renderedFields.some((field) => field.section);
  const sections = pageLayout ? nativeFormSections(renderedFields) : groupFieldsIntoSections(renderedFields);
  // Only loading, errors and gaps get a line; a form that loaded fine needs none.
  const formStatus = nativeTab && !nativeQuery.data && nativeQuery.isFetching
    ? "Loading the marketplace’s category fields and choices…"
    : nativeTab && nativeQuery.isError && !nativeQuery.data
      ? (nativeQuery.error instanceof Error ? nativeQuery.error.message : "The marketplace form could not be loaded. Reopen the listing to try again.")
      : categoryStale
        ? "Updating this category’s fields…"
        : nativeTab && nativeQuery.isFetching
          ? "Refreshing choices…"
          : !nativeTab && formsQuery.isFetching
            ? "Refreshing category fields and choices…"
            : !nativeTab && (formsQuery.isError || dropdownQuery.isError)
              ? "Some choices could not be refreshed. Check the source below each field before listing."
              : categoryForm?.known === false
                ? "The full category form is unavailable. Reference choices are shown where available."
                : null;
  // Renders its own hint beside the selected count.
  const multiSelectControl = (f: EditorField) => Boolean(f.multi)
    && !["inventory", "variations", "boolean", "textarea"].includes(String(f.type))
    && fieldLeaf(f.key) !== "description";
  const control = (f: EditorField) => {
    const id = `listing-field-${f.key}`;
    const locked = Boolean(disabled || f.locked);
    // A format or shipping change narrows the choices. Show the choice that fits
    // now when the typed value belonged to the previous set.
    const typed = formFieldText(local[f.key]);
    const fallback = formFieldText(f.defaultValue);
    const fallbackLabel = Object.entries(f.optionValues || {}).find(([, optionId]) => optionId === fallback)?.[0] ?? fallback;
    const text = f.options?.length && !f.options.includes(typed)
      && ["ebay_specifics.listingDuration", "ebay_specifics.listingFormat", "mercari_specifics.shippingPayerId"].includes(f.key)
      ? (f.options.includes(fallbackLabel) ? fallbackLabel : f.options.includes(fallback) ? fallback : f.options[0] ?? typed)
      : typed;
    // The marketplace's own photo area, where its page has it: this form's pick, order and limit.
    if (f.type === "photos") {
      return <MarketplaceMedia convId={convId} marketplace={tab} listing={listing} disabled={locked} onChange={emitChange}
        warnings={uniqueMarketplaceMessages(photoWarnings, shownMessages)} page={{ hint: f.help, slots: f.slotLabels, accept: f.accept }} />;
    }
    if (f.type === "inventory") {
      const stored = resolvedEditorValue(f, listing);
      const rows = Array.isArray(stored) ? stored.filter((row): row is NativeInventoryRow => typeof row === "object" && row !== null) : [];
      const multiField = viewFields.find(row => row.key === "poshmark_specifics.multiItem");
      const multiShown = formFieldText(local["poshmark_specifics.multiItem"] ?? resolvedEditorValue(multiField || { key: "poshmark_specifics.multiItem" }, listing));
      const multi = tab !== "poshmark" || (multiField?.optionValues?.[multiShown] ?? multiShown) === "true";
      const sizeSystem = formFieldText(getListingEditorValue(listing, "poshmark_specifics.sizeSystem")) || "us";
      return <NativeInventoryEditor
        value={rows}
        groups={f.inventoryGroups || []} disabled={locked} multi={multi} sizeSystem={sizeSystem}
        onSizeSystem={(system, next) => {
          const updated = cloneListing(listing);
          setListingEditorValue(updated, "poshmark_specifics.sizeSystem", system);
          setListingEditorValue(updated, f.key, next);
          emitChange(updated);
        }}
        onChange={(next) => {
          const updated = cloneListing(listing);
          setListingEditorValue(updated, f.key, next);
          emitChange(updated);
        }} />;
    }
    if (f.type === "cascade") {
      // The draft stores the id. The control's text is that id's label until it is chosen again.
      const stored = f.optionValues?.[text] ?? text;
      return <DependentSelectPair id={id} value={stored} departments={f.cascade || []} disabled={locked}
        onChange={next => commitField(f.key, next)} />;
    }
    if (f.type === "segmented") return <SegmentedTabs label={f.label} value={text}
      disabled={locked} tabs={(f.options || []).map(label => ({ id: label, label }))} onChange={next => commitField(f.key, next)} />;
    if (f.type === "badge") return null;
    if (f.type === "variations") return <EbayVariationsEditor convId={convId} listing={listing}
      value={normalizeVariations(resolvedEditorValue(f, listing))} aspects={f.variationAspects || []} disabled={locked} onChange={value => {
        const updated = cloneListing(listing);
        setListingEditorValue(updated, f.key, value);
        // The listing's quantity is the stock across its variations.
        if (value.rows.length) setListingEditorValue(updated, "quantity", variationTotal(value));
        emitChange(updated);
      }} />;
    if (f.native && f.key === "direct_category_ids.ebay") return <EbayCategorySelect id={id} value={text} disabled={locked} onChange={(categoryId, path) => {
      const updated = cloneListing(listing);
      setListingEditorValue(updated, f.key, categoryId);
      setListingEditorValue(updated, "direct_category_ids.ebay_path", path);
      emitChange(updated);
    }} />;
    if (f.native && f.key === "ebay_specifics.secondaryCategoryId") return <EbayCategorySelect id={id} value={text} disabled={locked} onChange={(categoryId, path) => {
      const updated = cloneListing(listing);
      setListingEditorValue(updated, f.key, categoryId);
      setListingEditorValue(updated, "ebay_specifics.secondaryCategoryPath", path);
      emitChange(updated);
    }} />;
    const categoryChoices = f.native && !f.multi ? pathCategoryChoices(f) : null;
    if (categoryChoices) return <CategoryTreeSelect key={text} id={id} fieldKey={f.key} value={text} choices={categoryChoices}
      disabled={locked} onChange={(choice) => commitField(f.key, choice.label)} />;
    if (f.type === "boolean" && f.presentation === "button") return <Toggle id={id} data-listing-field={f.key} variant="outline"
      className="w-full" pressed={text === "true"} disabled={locked} onPressedChange={(pressed) => commitField(f.key, pressed)}>{f.label}</Toggle>;
    if (f.type === "boolean") return <Switch id={id} data-listing-field={f.key} checked={text === "true"}
      disabled={locked} onCheckedChange={(checked) => commitField(f.key, checked)} />;
    if (f.type === "note") return null;
    if (fieldLeaf(f.key) === "description" || f.type === "textarea") {
      return <Textarea disabled={locked} id={id} data-listing-field={f.key} className="[&_textarea]:min-h-25 [&_textarea]:py-2 [&_textarea]:leading-relaxed" placeholder={f.placeholder} value={text} readOnly={locked} maxLength={f.maxLength ?? undefined} onChange={(e) => { setFieldDraft(f.key, e.target.value); }} onBlur={() => handleBlur(f.key)} />;
    }
    if (multiSelectControl(f)) return <SearchableMultiSelect id={id} fieldKey={f.key}
      value={expandJoinedChoices(local[f.key], f.options)} options={f.options ?? []}
      selectionOnly={f.selectionOnly ?? Boolean(f.options?.length)} disabled={locked}
      choicesVerified={f.optionsSource === "marketplace"}
      maxValues={f.maxValues ?? undefined} maxLength={f.maxLength ?? undefined} help={f.help ?? undefined}
      onChange={(value) => commitField(f.key, value)} />;
    if (f.options?.length && (f.presentation === "radio" || f.presentation === "radio-row")) {
      return (
        <RadioGroup id={id} data-listing-field={f.key} value={text} disabled={locked}
          className={f.presentation === "radio-row" ? "grid grid-cols-[repeat(auto-fit,minmax(7rem,1fr))] gap-3" : undefined}
          onValueChange={(value) => commitField(f.key, String(value))}>
          {f.options.map((option) => (
            <label key={option} className="flex min-w-0 items-start gap-2 text-sm">
              <Radio value={option} className="mt-0.5" />
              <span className="min-w-0">
                <span className="block font-medium">{option}</span>
                {f.optionHelp?.[option] && <span className="block text-muted-foreground text-xs">{f.optionHelp[option]}</span>}
              </span>
            </label>
          ))}
        </RadioGroup>
      );
    }
    if (tab === "vinted" && f.key === "vinted_specifics.category") {
      return <VintedCategorySelect convId={convId} id={id} fieldKey={f.key} value={text} disabled={locked}
        onChange={(value) => commitField(f.key, value)} />;
    }
    if (tab === "vinted" && f.key === "vinted_specifics.brand") {
      return <VintedBrandSelect convId={convId} category={formFieldText(local["vinted_specifics.category"])}
        id={id} fieldKey={f.key} value={text} disabled={locked}
        onChange={(value) => commitField(f.key, value)} />;
    }
    if (f.options?.length || f.selectionOnly) {
      return (
        <SearchableSelect
          id={id}
          fieldKey={f.key}
          value={text}
          options={f.options ?? []}
          disabled={locked}
          selectionOnly={f.selectionOnly}
          choicesVerified={f.optionsSource === "marketplace"}
          maxLength={f.maxLength ?? undefined}
          optionGroups={f.optionGroups}
          onChange={(value) => commitField(f.key, value)}
        />
      );
    }
    if (f.type === "datetime-local") {
      return <Input nativeInput id={id} data-listing-field={f.key} type="datetime-local" readOnly={locked} disabled={locked}
        value={text ? text.slice(0, 16) : ""} onChange={(e) => { setFieldDraft(f.key, e.target.value); }} onBlur={() => handleBlur(f.key)} />;
    }
    if (f.key === "poshmark_specifics.earnings") {
      return <Input nativeInput id={id} data-listing-field={f.key} readOnly value={formFieldText(f.defaultValue)} />;
    }
    const inputProps = {
      nativeInput: true,
      id,
      "data-listing-field": f.key,
      type: f.type || "text",
      min: f.minValue,
      max: f.maxValue,
      step: f.type === "number" ? f.step ?? "any" : undefined,
      readOnly: f.locked,
      disabled: locked || (f.selectionOnly && !f.options?.length),
      maxLength: f.maxLength ?? undefined,
      placeholder: f.placeholder,
      value: text,
      onChange: (e: React.ChangeEvent<HTMLInputElement>) => { setFieldDraft(f.key, e.target.value); },
      onBlur: () => handleBlur(f.key),
    };
    if (f.prefix || f.suffix) {
      return (
        <InputGroup>
          {f.prefix && <InputGroupAddon>{f.prefix}</InputGroupAddon>}
          <InputGroupInput {...inputProps} />
          {f.suffix && <InputGroupAddon align="inline-end">{f.suffix}</InputGroupAddon>}
        </InputGroup>
      );
    }
    return <Input {...inputProps} />;
  };
  // Long text and the category path need the full row. A marketplace title is
  // stored under that form, so the leaf is what still makes it full width.
  const fullWidth = (f: EditorField) => {
    const leaf = fieldLeaf(f.key);
    return leaf === "title" || leaf === "description" || f.key === "category_path" || leaf === "categorypath"
      || f.label === "Category" || ["textarea", "inventory", "variations", "photos", "note"].includes(f.type || "");
  };
  const revealButtons = (title: string, rows: EditorField[], include: (name: string) => boolean) =>
    [...new Set(rows.flatMap((f) => (f.reveal && include(f.reveal) ? [f.reveal] : [])))].map((reveal) => {
      const id = `${tab}|${title}|${reveal}`;
      const open = revealed.has(id);
      return (
        <InlineButton key={reveal} type="button" disabled={disabled} className="mt-2 text-xs" aria-expanded={open} onClick={() => { if (editingDisabled.current) return; setRevealOverride((current) => {
          const next = new Map(current);
          next.set(id, !open);
          return next;
        }); }}>{open && reveal === "Show more" ? "Show less" : reveal}</InlineButton>
      );
    });

  return (
    // Keyed by tab so switching marketplaces rebuilds the form instead of reusing rows.
    <fieldset ref={formRef} key={tab} disabled={disabled} aria-busy={disabled || undefined}
      aria-label={`${marketplaceName(tab)} listing fields`} className="@container flex min-w-0 flex-col border-0 p-0">
      {tab !== "general" && formStatus && <p role="status" className="border-b py-3 text-xs text-muted-foreground">{formStatus}</p>}
      {tab === "sellwild" && <SellwildAccountChoices convId={convId} disabled={disabled} connected={categoryForm?.account_connected === true} />}
      {formNotes.map((note) => <p key={note} className="py-2 text-xs text-muted-foreground">{note}</p>)}
      {formNotes.length > 0 && onAskChat && <div className="pb-2">
        <Button size="xs" variant="outline" disabled={disabled}
          title="Chat fixes the fields in this listing. It does not list the item."
          onClick={() => onAskChat([
            `Review these ${marketplaceName(tab)} form messages and fix the affected fields in this listing:`,
            ...formNotes,
            "Save the corrections in this listing. Do not list or publish it; I will press List after reviewing the changes.",
          ].join("\n"))}>Ask chat</Button>
      </div>}

      {sections.map((section) => {
        // Photos are counted on the photo area. A note is page text, and Smart Sell is a badge on Listing Price.
        const counted = section.fields.filter((f) => f.type !== "photos" && f.type !== "note" && f.type !== "badge");
        const filled = counted.filter((f) => {
          if (f.type === "inventory" || f.type === "variations") {
            const stored = resolvedEditorValue(f, listing);
            if (Array.isArray(stored)) return stored.length > 0;
            const rows = stored && typeof stored === "object" ? (stored as { rows?: unknown }).rows : undefined;
            return Array.isArray(rows) && rows.length > 0;
          }
          return formFieldText(local[f.key]).trim() !== "";
        }).length;
        const visible = section.fields.filter((f) => f.type !== "badge" && (!f.reveal || revealed.has(`${tab}|${section.title}|${f.reveal}`)));
        const rows = (
          <>
            {/* "See … options" sits at the top of eBay's section; "Show more" sits under the fields. */}
            {revealButtons(section.title, section.fields, (name) => name.startsWith("See "))}
            {/* Pair short fields when the editor itself has room, including beside a sidebar. */}
            <div className={cn("grid min-w-0 grid-cols-1 gap-x-4 gap-y-3", nativeTab ? "@xl:grid-cols-12" : "@xl:grid-cols-2")}>
              {pageLayout && section.fields[0]?.sectionHelp && <p className="col-span-full text-muted-foreground text-xs">{section.fields[0].sectionHelp}</p>}
              {visible.map((f, index) => {
                const badge = section.fields.find((row) => row.type === "badge" && (f.key === "price" || f.key === "poshmark_specifics.poshmarkPrice"));
                const badgeOn = badge ? formFieldText(local[badge.key] ?? badge.defaultValue) === "true" : false;
                return (
                <React.Fragment key={f.key}>
                {pageLayout && f.group && visible[index - 1]?.group !== f.group && (
                  <div className="col-span-full min-w-0">
                    <p className="font-medium text-sm">{f.group}</p>
                    {f.groupHelp && <p className="mt-0.5 text-muted-foreground text-xs">{f.groupHelp}</p>}
                  </div>
                )}
                <div data-listing-row={f.key} className={cn("min-w-0", nativeTab
                  ? f.width === "half" ? "@xl:col-span-6" : f.width === "third" ? "@xl:col-span-4"
                    : f.width === "quarter" ? "@xl:col-span-3" : "col-span-full"
                  : fullWidth(f) && "col-span-full")}>
                  {f.type !== "cascade" && f.presentation !== "button" && f.label && (f.hashtagMax != null ? (
                    <div className="mb-1.5 flex items-baseline justify-between gap-2">
                      <span className="flex min-w-0 items-center gap-1.5">
                        <FieldLabel htmlFor={`listing-field-${f.key}`} className={cn("mb-0", f.labelHidden && "sr-only")}>{f.label}</FieldLabel>
                        {fieldDefault(f)}
                      </span>
                      <span className="flex gap-3 text-muted-foreground text-xs tabular-nums">
                        <span>Hashtags: {Math.max(0, f.hashtagMax - (formFieldText(local[f.key]).match(/#[^\s#]+/g) || []).length)}</span>
                        {f.maxLength != null && <span>{Math.max(0, f.maxLength - formFieldText(local[f.key]).length)}</span>}
                      </span>
                    </div>
                  ) : f.counter && f.maxLength != null ? (
                    <div className="mb-1.5 flex items-baseline justify-between gap-2">
                      <span className="flex min-w-0 items-center gap-1.5">
                        <FieldLabel htmlFor={`listing-field-${f.key}`} className={cn("mb-0", f.labelHidden && "sr-only")}>{f.label}</FieldLabel>
                        {fieldDefault(f)}
                      </span>
                      <span className="text-muted-foreground text-xs tabular-nums">{formFieldText(local[f.key]).length}/{f.maxLength}</span>
                    </div>
                  ) : (
                    <div className="mb-1.5 flex items-center gap-2">
                      <FieldLabel htmlFor={`listing-field-${f.key}`} className={cn("mb-0", f.labelHidden && "sr-only")}>{f.label}</FieldLabel>
                      {fieldDefault(f)}
                      {badge && <button type="button" disabled={disabled || badge.locked} aria-pressed={badgeOn} className="rounded-full border px-2 py-0.5 text-xs font-medium"
                        onClick={() => commitField(badge.key, !badgeOn)}>{badgeOn ? "Smart Sell ON" : "Smart Sell OFF"}</button>}
                    </div>
                  ))}
                  <div className={cn(f.controlWidth === "half" && "@xl:w-1/2")}>{control(f)}</div>
                  {f.help && f.type !== "photos" && !multiSelectControl(f) && <p className="mt-1 text-xs text-muted-foreground">{f.help}</p>}
                  {uniqueMarketplaceMessages(f.type === "photos" ? [] : fieldWarnings.get(f.key) ?? [], shownMessages).map((message) =>
                    <p key={message} role="alert" className="mt-1 text-xs text-warning-foreground">{message}</p>)}
                  {f.maxLength != null && (f.multi ? formFieldValues(local[f.key], f.options) : [formFieldText(local[f.key])]).some((value) => value.length > f.maxLength!) &&
                    <p role="alert" className="mt-1 text-xs text-warning-foreground">Each value allows at most {f.maxLength} characters. Shorten the saved value.</p>}
                  {f.selectionOnly && !f.options?.length && <p role="alert" className="mt-1 text-xs text-warning-foreground">Choices unavailable. Reopen the listing to load this field.</p>}
                  {!nativeTab && f.optionsSource && f.optionsSource !== "saved" && <p className="mt-1 text-xs text-muted-foreground">
                    {f.optionsSource === "marketplace" ? "Marketplace choices" : f.optionsSource === "vendoo" ? "Imported category choices" : "Reference choices"}
                  </p>}
                </div>
                </React.Fragment>
                );
              })}
            </div>
            {revealButtons(section.title, section.fields, (name) => !name.startsWith("See "))}
          </>
        );
        // A part of the page with no heading (Mercari's and Sellwild's photos) has none here either.
        if (!section.title) return <section key={section.id} className="min-w-0 border-b py-4 first:pt-1 last:border-b-0">{rows}</section>;
        return (
          <InspectorSection
            key={`${tab}-${section.id}`}
            className="gap-3 py-4"
            title={section.title}
            titleId={`listing-section-${tab}-${section.id}`}
            meta={section.id === "listing" || !counted.length ? null : `${filled} of ${counted.length} filled`}
          >
            {rows}
          </InspectorSection>
        );
      })}
    </fieldset>
  );
}

/** Consecutive fields under the same marketplace heading, in the form's own order. */
export function nativeFormSections<T extends { key: string; section?: string }>(fields: T[]): { id: string; title: string; fields: T[] }[] {
  const sections: { id: string; title: string; fields: T[] }[] = [];
  for (const field of fields) {
    const title = field.section ?? "Listing";
    const last = sections[sections.length - 1];
    if (last && last.title === title) last.fields.push(field);
    else sections.push({ id: `${sections.length}-${title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`, title, fields: [field] });
  }
  return sections;
}

function fieldLeaf(key: string): string {
  return (key.split(".").pop() || "").toLowerCase();
}

const FOLLOWED_GENERAL = new Set(editorFields.followed_general);

/** General fills these. A marketplace edit is stored on that form alone. */
function independentFieldKey(tab: string, key: string): string {
  if (tab === "general" || key.includes(".")) return key;
  if (key === "price") return MARKETPLACE_PRICE_KEYS[tab] ?? key;
  if (FOLLOWED_GENERAL.has(key)) return `${tab}_specifics.${key}`;
  return key;
}

function independentRows(rows: EditorField[], tab: string): EditorField[] {
  if (tab === "general") return rows;
  return mergeEditorFields(rows.flatMap((field) => {
    // The form's Price edits this marketplace's own price, which follows General
    // until it is set here. The shared price would change every form.
    if (field.key === "price" && MARKETPLACE_PRICE_KEYS[tab]) return [{ ...field, key: MARKETPLACE_PRICE_KEYS[tab] }];
    const key = independentFieldKey(tab, field.key);
    const inherited = Boolean(field.locked && /change it there/i.test(field.help || ""));
    if (key === field.key && !inherited) return [field];
    return [{
      ...field,
      key,
      ...(fieldLeaf(key) === "description" && !field.type ? { type: "textarea" as const } : {}),
      ...(inherited ? { locked: false, help: "Filled from General. A change here stays on this marketplace." } : {}),
    }];
  }));
}

function marketplacePriceFollowsGeneral(listing: ListingData | undefined, field: EditorField, tab: string): boolean {
  const canonical = MARKETPLACE_PRICE_KEYS[tab];
  const priceRow = field.key === canonical || (field.label.endsWith(" price") && field.key.includes("_specifics."));
  if (!priceRow) return false;
  return !(Array.isArray(listing?.seller_fields) && listing.seller_fields.includes(field.key));
}

/** This form's own value, otherwise the native or General value it was filled with. */
function sourceValue(listing: ListingData | undefined, field: EditorField, tab: string): unknown {
  return marketplaceDisplayValue(listing, field.key, {
    tab,
    locked: field.locked,
    native: field.native,
    defaultValue: field.defaultValue,
  });
}

/** What a control shows before it is mapped to a choice label. */
export function editorFieldValue(listing: ListingData | undefined, field: EditorField, tab: string): unknown {
  // eBay's category control shows the breadcrumb. The id lives beside it.
  if (field.key === "direct_category_ids.ebay") return resolvedEditorValue(field, listing);
  // A locked row keeps the form's computed value, such as a generated SKU.
  if (field.locked && field.defaultValue !== undefined) return field.defaultValue;
  const displayed = sourceValue(listing, field, tab);
  // A selection whose saved text is not one of the choices keeps the form's resolved id.
  if (field.selectionOnly && editorValuePresent(displayed) && field.defaultValue !== undefined && !selectionChoiceMatches(field, displayed)) {
    return field.defaultValue;
  }
  return displayed;
}

function selectionChoiceMatches(field: EditorField, value: unknown): boolean {
  const values = field.multi ? formFieldValues(value, field.options) : [formFieldText(value)].filter(Boolean);
  if (!values.length) return false;
  const ids = new Set(Object.values(field.optionValues || {}));
  return values.every((item) => field.options?.includes(item) || ids.has(item));
}

const SHARED_FORM_FIELDS: EditorField[] = editorFields.shared;

const PACKAGE_FORM_FIELDS: EditorField[] = editorFields.package;

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

const MARKETPLACE_PRICE_KEYS: Record<string, string> = editorFields.marketplace_price_keys;

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

function finishMarketplaceRows(rows: EditorField[], listing: ListingData | undefined, tab: string, native = false): EditorField[] {
  let visible = tab === "mercari"
    ? rows.filter((field) => field.native || !MERCARI_DERIVED_LABELS.has(field.label.replace(/\s*\*$/, "").toLowerCase()))
    : rows;
  if (native) return visible;
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
    "Color", "Package size", "Material"],
  grailed: ["Title", "Description", "Price", "Condition", "Designer", "Brand"],
  facebook: ["Title", "Description", "Price", "Condition", "Brand"],
};

function coreFieldLabel(tab: string, label: string): boolean {
  const folded = foldFieldName(label.replace(/\s*\*$/, ""));
  return (CATEGORY_FORM_LABELS[tab] ?? []).some((item) => foldFieldName(item) === folded);
}

function foldFieldName(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, "");
}

/**
 * A known category shows its own attributes plus the marketplace's standard
 * controls. Until that schema is loaded, only those controls show. Saved values
 * the form does not show stay on the listing. Direct listing forms never expand
 * past that set.
 */
function limitToCategoryForm(
  rows: EditorField[],
  forms: MarketplaceForm[] | undefined,
  tab: string,
): EditorField[] {
  if (tab === "general") return rows;
  const form = forms?.find((entry) => entry.marketplace === tab);
  if (form?.native) return rows;
  const core = new Set((CATEGORY_FORM_LABELS[tab] ?? []).map(foldFieldName));
  const listed = (field: EditorField) => {
    if (field.key.includes(".measurements.")) return true;
    const label = foldFieldName(field.label.replace(/\s*\*$/, ""));
    return core.has(label);
  };
  // No category schema yet. A direct listing shows its standard rows. Another
  // editor shows those rows plus the fields every listing has, such as brand.
  // A saved attribute that belongs to a category stays off until that schema loads.
  if (!form?.known) {
    if (!DIRECT_FORM_MARKETS.has(tab)) {
      const shared = new Set(
        [...SHARED_FORM_FIELDS, ...(PACKAGE_MARKETPLACES.has(tab) ? PACKAGE_FORM_FIELDS : [])]
          .map((field) => foldFieldName(field.label)),
      );
      return rows.filter((field) => {
        const label = foldFieldName(field.label.replace(/\s*\*$/, ""));
        return core.has(label) || shared.has(label) || field.key.includes(".measurements.");
      });
    }
    return core.size ? rows.filter(listed) : rows;
  }
  const allowed = new Set(
    form.fields
      .filter((field) => field.on_form !== false)
      .flatMap((field) => [foldFieldName(field.label), foldFieldName(field.key)]),
  );
  return rows.filter((field) => {
    if (listed(field)) return true;
    const label = foldFieldName(field.label.replace(/\s*\*$/, ""));
    const leaf = foldFieldName(field.key.split(".").pop() || "");
    return allowed.has(label) || allowed.has(leaf);
  });
}

/** Every field that marketplace submits, shared listing values first, then its own. */
export function fieldsForMarketplaceForm(
  listing: ListingData | undefined,
  tab: string,
  forms: MarketplaceForm[] | undefined,
): EditorField[] {
  const catalog = forms?.find((form) => form.marketplace === tab);
  // A direct marketplace with its native catalog, or with no category schema yet,
  // does not fall back to a hand-kept form. A known category schema still merges.
  if (DIRECT_FORM_MARKETS.has(tab) && (catalog?.native || !catalog?.fields.length)) {
    const rows = catalog?.native ? schemaFieldsForTab(forms, tab) || [] : [];
    // The marketplace's own form, row for row: nothing is added to it here.
    return limitToCategoryForm(finishMarketplaceRows(independentRows(rows, tab), listing, tab, true), forms, tab);
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
  // The label carries a required marker ("Category *"), so compare without it.
  if (own.some((field) => field.label.replace(/\s*\*$/, "") === "Category")) {
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
    finishMarketplaceRows(withoutObjects(withMarketplacePrice(independentRows(named, tab), tab)), listing, tab),
    forms,
    tab,
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
  return form.fields.filter((field) => !isPlainObject(field.value) || field.input_type === "variations").map((field) => ({
    // Schema keys are Vendoo labels ("Sleeve Length"); listing JSON uses camelCase.
    // getListingEditorValue / setListingEditorValue bridge the two.
    key: form.native ? field.path || field.key : `${tab}_specifics.${field.key}`,
    native: form.native,
    type: field.input_type,
    inventoryGroups: field.inventory_groups,
    variationAspects: field.variation_aspects,
    minValue: field.min_value,
    maxValue: field.max_value,
    step: field.step,
    locked: field.locked,
    optionValues: field.option_values,
    ...(field.option_aliases ? { optionAliases: field.option_aliases } : {}),
    clears: field.clears,
    help: field.help,
    valueConstraints: field.value_constraints,
    label: field.required && !form.page_labels ? `${field.label} *` : field.label,
    required: field.required,
    multi: field.multi,
    selectionOnly: form.native ? field.selection_only : field.selection_only || field.options_source === "reference"
      || (field.options.length > 0 && (field.options_source === "marketplace" || field.options_source === "vendoo")),
    maxValues: field.max_values,
    maxLength: field.max_length,
    optionsSource: field.options_source,
    options: field.options,
    // A Vendoo category attribute is replaced when the category changes.
    // The marketplace's own controls, such as title and price, stay.
    ...(field.category_specific || (!form.native && !coreFieldLabel(tab, field.label)) ? { categorySpecific: true } : {}),
    ...(field.section !== undefined ? { section: field.section } : {}),
    ...(field.reveal ? { reveal: field.reveal } : {}),
    ...(field.slot_labels ? { slotLabels: field.slot_labels } : {}),
    ...(field.accept ? { accept: field.accept } : {}),
    ...(field.option_groups ? { optionGroups: field.option_groups } : {}),
    ...(field.width ? { width: field.width } : {}),
    ...(field.control_width ? { controlWidth: field.control_width } : {}),
    ...(field.cascade ? { cascade: field.cascade as EditorField["cascade"] } : {}),
    ...(field.option_amounts ? { optionAmounts: field.option_amounts } : {}),
    ...(field.group ? { group: field.group, groupHelp: field.group_help } : {}),
    ...(field.section_help ? { sectionHelp: field.section_help } : {}),
    ...(field.placeholder ? { placeholder: field.placeholder } : {}),
    ...(field.prefix ? { prefix: field.prefix } : {}),
    ...(field.suffix ? { suffix: field.suffix } : {}),
    ...(field.label_hidden ? { labelHidden: true } : {}),
    ...(field.counter ? { counter: true } : {}),
    ...(field.hashtag_max != null ? { hashtagMax: field.hashtag_max } : {}),
    ...(field.save_as_default ? { saveAsDefault: true } : {}),
    ...(field.presentation ? { presentation: field.presentation } : {}),
    ...(field.option_help ? { optionHelp: field.option_help } : {}),
    defaultValue: form.native ? field.value : formFieldText(field.value),
  }));
}

type AspectConstraintRule = {
  parentName?: string;
  parentValues?: string[];
  applicableForLocalizedAspectName?: string;
  applicableForLocalizedAspectValues?: string[];
};

function aspectConstraintRules(rules: AspectConstraintRule[] | undefined): { parentName: string; parentValues: string[] }[] {
  const merged = new Map<string, { parentName: string; parentValues: string[] }>();
  for (const rule of rules || []) {
    const parentName = rule.parentName || rule.applicableForLocalizedAspectName || "";
    const parentValues = rule.parentValues || rule.applicableForLocalizedAspectValues || [];
    if (!parentName) continue;
    const key = parentName.toLowerCase();
    const entry = merged.get(key) || { parentName, parentValues: [] };
    for (const item of parentValues) {
      const text = String(item);
      if (!entry.parentValues.some((known) => known.toLowerCase() === text.toLowerCase())) entry.parentValues.push(text);
    }
    merged.set(key, entry);
  }
  return [...merged.values()];
}

export function nativeEditorDependencies(fields: EditorField[], listing: ListingData): EditorField[] {
  return fields.map((field) => !field.valueConstraints ? field : { ...field,
    options: field.options?.filter((value) => {
      const rules = aspectConstraintRules(field.valueConstraints?.[value]);
      if (!rules.length) return true;
      return rules.every((rule) => {
        const parent = fields.find((row) => row.label.replace(/\s*\*$/, "").toLowerCase() === rule.parentName.toLowerCase());
        const selected = parent && (getListingEditorValue(listing, parent.key) ?? parent.defaultValue);
        const values = formFieldValues(selected).map((item) => item.toLowerCase());
        return rule.parentValues.some((item) => values.includes(item.toLowerCase()));
      });
    }),
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
      return GENERAL_LISTING_FIELDS;
    case "etsy":
      return mergeSpecificsWithDefaults(
        listing?.etsy_specifics,
        "etsy_specifics",
        [
          ...liveCategoryFields(listing, "etsy"),
          ...editorFields.etsy,
          ...ETSY_CATEGORY_OPTIONALS.map((field) => ({
            key: `etsy_specifics.category_specifics.${field.key}`,
            label: field.label,
          })),
        ],
      );
    case "vinted":
      return editorFields.vinted;
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
  saving,
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
  saving: boolean;
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
  const sessionActive = useListingSessionActive();
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
    refetchInterval: () => (sessionActive ? pollMs(EXTENSION_STATUS_POLL_MS) : false),
  });

  const { data: jobs } = useQuery({
    queryKey: ["jobs", convId],
    queryFn: () => api.jobs.list(convId),
    refetchInterval: (query) => (sessionActive ? jobsPollMs(query.state.data) : false),
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
    refetchInterval: () => (sessionActive ? fillLogPollMs(listingJob?.status) : false),
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
  const sendEnabled = sendToVendooEnabled(canSend, generating, sendMutation.isPending || saving) && !apiCreateActive && !formFillActive;
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
    if (saving) return;
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
  const statusLine = saving ? "Save your edits before sending."
    : sendEnabled
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
