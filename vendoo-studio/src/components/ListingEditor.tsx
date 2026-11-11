import { VendooLayout, useListingMode } from "./VendooLayout";
import { useWorkspaceRuntime } from "../app/runtime";
import { editorTabs } from "./editorTabs";
import { GENERAL_LISTING_FIELDS } from "../generalListingFields";
import editorFields from "../../../skills/list-this/references/listing-editor-fields.json";
import React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { EbayVariationAspect, EbayVariations, ListingData, ListingDocument, MarketplaceForm, NativeInventoryGroup, NativeInventoryRow } from "../api/types";
import { cloneListing, editorValuePresent, expandJoinedChoices, formFieldText, formFieldValues, getListingEditorValue, marketplaceDisplayValue, setListingEditorValue, squashFieldKey } from "../listingPaths";
import { listingEdits } from "../api/listingEdits";
import { registerUnsavedWork } from "../lib/unsavedWork";
import { DIRECT_FORM_MARKETS, nativeFormOptions, prefetchListingForm } from "../api/listingForms";
import { PhotoTray } from "./PhotoTray";
import { useListingSessionActive } from "./listingSession";
import { NativeInventoryEditor } from "./NativeInventoryEditor";
import { EbayVariationsEditor, normalizeVariations, variationTotal } from "./EbayVariationsEditor";
import { MarketplaceMedia } from "./MarketplaceMedia";
import { MEDIA_MARKETPLACES } from "../marketplaceMedia";
import { ItemDetails } from "./ItemDetails";
import { ListingHistory } from "./ListingHistory";
import { blockerTarget, isEbayInventoryLocationEdit, matchingEditorField } from "./listingChanges";
import { SoldElsewhereNotice } from "./SoldElsewhereNotice";
import { marketplaceName } from "./marketplaceNames";
import { ETSY_CATEGORY_OPTIONALS } from "../marketplaceFields";
import { depopSizeGroupingApplies } from "../marketplaceFields";
import { withDropdownOptions } from "../dropdownOptions";
import { applyBottomsFields, isBottomsListing } from "../bottomsSizes";
import { activityPollMs, jobsPollMs } from "../api/polling";
import { toastManager } from "./ui/toast";
import { Radio, RadioGroup } from "./ui/radio-group";
import { Toggle } from "./ui/toggle";
import { RegenerateListingButton } from "./ClearListingButton";
import { MarketplaceMessagesContext, uniqueMarketplaceMessages } from "./marketplaceMessages";
import { MarketplacesLayout, useReportFieldsShown } from "./MarketplacesSection";
import { confirmDialog } from "../ui/confirmDialog";
import { useChatBusy } from "./chat/liveChatStore";
import { ListingActionsMenu } from "./ListingReviewActions";
import { type ListingReviewTab } from "./ListingReviewTabs";
import type { BulkListingUploadResult } from "../bulkPhotoUpload";
import { BookmarkCheckIcon, BookmarkPlusIcon, CircleAlertIcon, HistoryIcon, MessageCircleIcon } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "./ui/alert";
import { cn } from "../lib/utils";
import { pathCategoryChoices } from "./categoryTree";
import { CategoryTreeSelect, DependentSelectPair, EbayCategorySelect, FieldLabel, GeneralCategorySelect, InspectorSection, SearchableSelect, SearchableMultiSelect, SegmentedTabs, type CascadeDepartment } from "./ListingFormControls";
import { GENERAL_FOLDED_SECTION, generalFormSections, groupFieldsIntoSections } from "./listingFieldSections";
import { categorySpecificFieldsStale, nativeFieldVisible, nativeFormCategoryKey, nativeFormNotes, presentNativeField, resolvedEditorValue } from "./nativeFormStability";
import { gapsByField, rowForField } from "./nativeFieldWarnings";
import type { NativeMarketplace } from "../api/nativeMarketplaces";
import { Button, InlineButton } from "./ui/button";
import { Input } from "./ui/input";
import { InputGroup, InputGroupAddon, InputGroupInput } from "./ui/input-group";
import { Spinner } from "./ui/spinner";
import { Switch } from "./ui/switch";
import { Textarea } from "./ui/textarea";
import { Tooltip, TooltipPopup, TooltipTrigger } from "./ui/tooltip";
import { savedDefaultFor } from "./fieldDefaults";
import { agentFieldLocked } from "./agentFieldLocks";

interface Props {
  convId: string;
  onAskChat?: (text: string) => void;
  onCleared?: () => void;
  reviewTab: ListingReviewTab;
  onReviewTabChange: (tab: ListingReviewTab) => void;
  /** "general" or a marketplace id; App keeps it so other pages can open a tab. */
  editTab: string;
  onEditTabChange: (tab: string) => void;
  onBulkListingsCreated?: (listings: BulkListingUploadResult[], generate: boolean) => void;
  onEditInventoryLocation?: () => void;
  /** The listing's chat, beside the form or floated over its corner on desktop. */
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
  reviewTab,
  onReviewTabChange,
  editTab: requestedTab,
  onEditTabChange: setEditTab,
  onBulkListingsCreated,
  onEditInventoryLocation,
  chat,
}: Props) {
  const runtime = useWorkspaceRuntime();
  const mode = useListingMode();
  const vendoo = mode.data?.listing_mode === "vendoo";
  const queryClient = useQueryClient();
  const edits = React.useMemo(() => listingEdits(queryClient, convId), [queryClient, convId]);
  const editState = React.useSyncExternalStore(edits.subscribe, edits.getSnapshot, edits.getSnapshot);
  const unsaved = editState.saving || Boolean(editState.draft);
  const [historyOpen, setHistoryOpen] = React.useState(false);
  const [fieldTarget, setFieldTarget] = React.useState<{ field: string; sequence: number } | null>(null);
  const editorRef = React.useRef<HTMLDivElement>(null);
  const sessionActive = useListingSessionActive();
  const { data: jobs } = useQuery({
    queryKey: ["jobs", convId],
    queryFn: ({ signal }) => api.jobs.list(convId, { signal }),
    refetchInterval: (query) => (sessionActive ? jobsPollMs(query.state.data) : false),
  });

  const listingQuery = useQuery({
    queryKey: ["listing", convId],
    queryFn: ({ signal }) => api.listings.get(convId, { signal }),
    enabled: !unsaved,
  });
  const { data } = listingQuery;

  const { data: marketplaceSettings } = useQuery({
    queryKey: ["settings-marketplaces"],
    queryFn: api.settings.marketplaces,
  });
  const conversationQuery = useQuery({
    queryKey: ["conversation", convId],
    queryFn: ({ signal }) => api.conversations.get(convId, { signal }),
  });
  const { data: conversation } = conversationQuery;
  // A failed read must not look like an empty listing: blank labels, notes and
  // photos read as lost data, and a label added there would replace the saved ones.
  // Fields stay editable while the first read is only slow.
  const loadError = unsaved ? null : (!data && listingQuery.error) || (!conversation && conversationQuery.error) || null;
  const [retrying, setRetrying] = React.useState(false);
  const retryLoad = async () => {
    setRetrying(true);
    try {
      await Promise.all(["listing", "conversation", "photos"].map((key) =>
        queryClient.refetchQueries({ queryKey: [key, convId], exact: true })));
    } finally {
      setRetrying(false);
    }
  };

  const Layout = vendoo ? VendooLayout : MarketplacesLayout;
  const tabs = React.useMemo(() => editorTabs(marketplaceSettings?.selected, runtime), [marketplaceSettings?.selected, runtime]);

  // A marketplace the seller no longer lists to falls back to General.
  const editTab = tabs.includes(requestedTab) ? requestedTab : "general";

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
    queryFn: ({ signal }) => api.conversations.activity(convId, { signal }),
    refetchInterval: (query) => (sessionActive ? activityPollMs(Boolean(query.state.data?.busy)) : false),
  });
  const generating = chatBusy || Boolean(activity?.busy);
  // A List running on one marketplace locks only that marketplace's row, not the others.
  const writing = chatBusy || Boolean(activity?.writing);
  // While chat is busy an Ask chat joins its queue and runs next.
  const askChat = runtime.features.has("generation") ? onAskChat : undefined;

  const listing = editState.draft ?? data?.listing ?? {};
  const listingTitle = String(listing.title || "Listing");
  const garmentCategory = String(listing.category_path || "");
  const garmentTitle = String(listing.title || "");
  const garmentHint = React.useMemo(() => ({ category: garmentCategory, title: garmentTitle }), [garmentCategory, garmentTitle]);
  // The listing's own answer is fresher than the sidebar row's after an edit.
  const etsyEligible = data?.etsy_eligible ?? conversation?.etsy_eligible ?? true;

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
      </div>

      <SoldElsewhereNotice convId={convId} />




      <div className={cn("listing-form-area", chat && "has-chat")}>
      {mode.isPending ? <p role="status" className="p-3 text-xs text-muted-foreground">Loading listing workflow…</p> : mode.error && !mode.data ?
        <p role="alert" className="p-3 text-xs text-destructive-foreground">{mode.error.message}</p> : loadError ?
        <Alert variant="error" className="m-3 self-start">
          <CircleAlertIcon />
          <AlertTitle>This listing didn't load</AlertTitle>
          <AlertDescription className="gap-2">
            <p>Your saved details weren't changed. Try again in a moment.</p>
            <p className="text-xs">{loadError.message}</p>
            <div>
              <Button size="xs" variant="outline" disabled={retrying} onClick={() => void retryLoad()}>
                {retrying ? "Retrying…" : "Retry"}
              </Button>
            </div>
          </AlertDescription>
        </Alert> :
      <Layout key={convId} convId={convId} revisionId={data?.current_revision_id || null} title={listingTitle}
        disabled={writing || unsaved} etsyEligible={etsyEligible} onAskChat={askChat} onEditField={openBlocker} ids={tabs}
        onPrepare={(tab) => {
          if (vendoo) return;
          if (!unsaved && data?.current_revision_id) void prefetchListingForm(queryClient, convId, tab, data.listing, data.current_revision_id);
        }}
        selected={reviewTab === "forms" && editTab !== "general" ? editTab : "general"}
        railActions={runtime.features.has("generation") ? <RegenerateListingButton convId={convId} variant="ghost-muted" onRegenerated={onCleared} /> : undefined}
        railMenu={<ListingActionsMenu convId={convId} onCleared={onCleared} />}
        railStatus={editState.saving ? <span role="status" className="text-xs text-muted-foreground">{generating ? "Edits kept. Waiting for chat to finish before saving…" : "Saving…"}</span> : undefined}
        onSelect={(id) => {
          setFieldTarget(null);
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
            </>
          )}
          {/* A direct marketplace's form shows its photos where its own page has them. */}
          {reviewTab === "forms" && MEDIA_MARKETPLACES.has(editTab) && !DIRECT_FORM_MARKETS.has(editTab) && listing && (
            <MarketplaceMedia key={editTab} convId={convId} marketplace={editTab} listing={listing}
              disabled={false} onChange={edits.save} />
          )}
          <StructuredEditor
            convId={convId}
            listing={listing}
            revisionId={data?.current_revision_id}
            tab={reviewTab === "forms" && editTab !== "general" ? editTab : "general"}
            target={fieldTarget}
            onAskChat={askChat}
            disabled={runtime.hosted && reviewTab === "forms" && editTab !== "general" && !runtime.marketplaceForms.has(editTab)}
            saving={unsaved}
            agentLocks={activity?.locked_fields}
            onChange={edits.save}
            generalSections={{
              measurements: <ItemDetails convId={convId} part="measurements" garmentHint={garmentHint} />,
              notes: <ItemDetails convId={convId} part="notes" garmentHint={garmentHint} />,
            }}
          />
          {/* Past versions are a look-back, so they close the form instead of crowding its header. */}
          <div className="flex border-t pt-2">
            <Button variant="ghost-muted" size="xs" disabled={!data?.current_revision_id} onClick={() => { setFieldTarget(null); setHistoryOpen(true); }}>
              <HistoryIcon />
              History
            </Button>
          </div>
        </>
      </Layout>}
      {chat}
      </div>

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
 * A field's secondary tools: where its choices come from and Ask chat. They stay
 * out of sight until the pointer is over the field or focus is in it, so the
 * form reads as fields first. The button stays in the tab order and shows on
 * focus; on touch it shows once the seller taps into the field.
 */
function FieldTools({ label, source, onAsk }: { label: string; source: string | null; onAsk?: () => void }) {
  const name = fieldName(label);
  return (
    <span className={cn("ms-auto flex shrink-0 items-center gap-1.5 opacity-0 transition-opacity",
      "pointer-events-none group-hover/field:pointer-events-auto group-hover/field:opacity-100",
      "group-focus-within/field:pointer-events-auto group-focus-within/field:opacity-100")}>
      {source && <span className="text-2xs text-muted-foreground">{source}</span>}
      {onAsk && (
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                type="button"
                size="icon-micro"
                variant="ghost-muted"
                className="-my-1 shrink-0"
                aria-label={`Ask chat to fill ${name}`}
                onMouseDown={(event) => event.preventDefault()}
                onClick={onAsk}
              />
            }
          >
            <MessageCircleIcon className="size-3.5" />
          </TooltipTrigger>
          <TooltipPopup side="top" sideOffset={6}>Ask chat to fill this field</TooltipPopup>
        </Tooltip>
      )}
    </span>
  );
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
  agentLocks = [],
  generalSections,
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
  agentLocks?: string[];
  /** The seller's own General sections: measurements after the item's details, notes last. */
  generalSections?: { measurements: React.ReactNode; notes: React.ReactNode };
}) {
  const runtime = useWorkspaceRuntime();
  const formRef = React.useRef<HTMLFieldSetElement>(null);
  const editingDisabled = React.useRef(Boolean(disabled));
  editingDisabled.current = Boolean(disabled);
  const emitChange = (updated: ListingData) => {
    if (!editingDisabled.current) onChange(updated);
  };
  const vendoo = useListingMode().data?.listing_mode === "vendoo";
  const nativeTab = !vendoo && DIRECT_FORM_MARKETS.has(tab);
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
  // Field checks appear beside the form as advice; the marketplace validates
  // the seller-confirmed List attempt.
  const preflight = useQuery({
    queryKey: ["native-preflight", convId, revisionId],
    queryFn: () => api.nativeMarketplaces.preflight(convId),
    enabled: runtime.actionMarketplaces.size > 0 && nativeTab && Boolean(revisionId) && !saving,
    staleTime: 15_000,
    placeholderData: (previous) => previous,
  });
  // The category schema is this form. It is read again when the category
  // changes, and a price or brand edit does not fetch it again.
  const formsQuery = useQuery({
    queryKey: ["listing-fields", convId],
    queryFn: ({ signal }) => api.listings.fields(convId, { signal }),
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
  const { data: packageDefault } = useQuery({
    queryKey: ["settings-package-dimensions"],
    queryFn: api.settings.packageDimensions,
    enabled: tab === "general",
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
      const packageHint = tab === "general" && packageDefault
        ? `${packageDefault.length}x${packageDefault.width}x${packageDefault.height}` : "";
      const saved = savedDefaults?.defaults;
      if (!fallback && !packageHint && !saved) return rows;
      return rows.map((field) => {
        if (fallback && field.key === "zipCode" && !formFieldText(getListingEditorValue(listing, field.key))) {
          return { ...field, defaultValue: fallback };
        }
        if (packageHint && field.key === "package_dimensions_in") {
          return { ...field, placeholder: packageHint };
        }
        if (field.saveAsDefault && saved?.[field.key]
          && !formFieldText(getListingEditorValue(listing, field.key))) {
          return { ...field, defaultValue: saved[field.key].label };
        }
        return field;
      });
    },
    [nativeTab, nativeQuery.data, forms, listing, tab, dropdownOptions?.forms, inventoryLocation?.location, packageDefault, savedDefaults?.defaults],
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
  // General's More details starts folded. An "Edit …" target inside it opens it
  // until the seller folds it again.
  const [moreToggle, setMoreToggle] = React.useState<{ open: boolean; sequence?: number }>({ open: false });
  const targetFolded = React.useMemo(() => {
    if (!target || tab !== "general") return false;
    const key = matchingEditorField(fields, target.field) ?? rowForField(tab, target.field, fields);
    return Boolean(key && generalFormSections(fields).find((section) => section.id === GENERAL_FOLDED_SECTION)
      ?.fields.some((field) => field.key === key));
  }, [target, tab, fields]);
  const moreOpen = targetFolded && moreToggle.sequence !== target?.sequence ? true : moreToggle.open;
  const setMoreOpen = (open: boolean) => setMoreToggle({ open, sequence: target?.sequence });
  React.useEffect(() => {
    if (!target || reachedTarget.current === target.sequence) return;
    // A marketplace's answer can name the listing field its form row comes from.
    const key = matchingEditorField(fields, target.field) ?? rowForField(tab, target.field, fields);
    if (!key) return;
    const row = [...(formRef.current?.querySelectorAll<HTMLElement>("[data-listing-row]") || [])].find((element) => element.dataset.listingRow === key);
    if (!row) return;
    // The label row puts "Save as default" and "Ask chat" before the control, so
    // look for the field's own control first.
    const focusable = 'input:not([disabled]), textarea:not([disabled]), button:not([disabled]), [tabindex="0"]';
    const control = row.querySelector<HTMLElement>("[data-listing-field]");
    const input = (control?.matches(focusable) ? control : control?.querySelector<HTMLElement>(focusable))
      ?? row.querySelector<HTMLElement>(focusable);
    reachedTarget.current = target.sequence;
    row.scrollIntoView({ block: "center" });
    (input ?? row).focus({ preventScroll: true });
  }, [target, fields, tab, moreOpen]);

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

  /** Writes a typed field into `target` (a copy of the listing); false when there is nothing to save. */
  const applyDraft = (target: ListingData, key: string): boolean => {
    if (agentFieldLocked(key, agentLocks) || local[key] == null) return false;
    const field = fields.find((entry) => entry.key === key);
    if (field?.locked) return false;
    const text = formFieldText(local[key]);
    if (text.trim() === "[object Object]") return false;
    const alias = field?.optionAliases?.[text.trim()];
    // A saved value the picker cannot hold ("S" where Mercari lists "S (4-6)")
    // is saved as the choice it stands for, even when nothing else changed.
    if (alias) {
      setListingEditorValue(target, key, alias);
      return true;
    }
    // Compare with the labeled value the control was showing. The form's original
    // value stays in defaultValue and must not block a later edit that matches it.
    const labelOf = (value: string) => Object.entries(field?.optionValues || {}).find(([, id]) => id === value)?.[0] ?? value;
    const followed = field ? marketplacePriceFollowsGeneral(listing, field, tab) : false;
    const raw = field ? editorFieldValue(listing, field, tab) : getListingEditorValue(listing, key);
    const current = followed && listing?.price != null ? formFieldText(listing.price) : labelOf(formFieldText(raw));
    if (text === current) return false;
    setListingEditorValue(target, key, field?.type === "number" ? coerce(text) : text);
    return true;
  };

  const handleBlur = (key: string) => {
    if (editingDisabled.current || agentFieldLocked(key, agentLocks) || local[key] == null) return;
    setDrafts(current => {
      const next = { ...current };
      delete next[`${tab}|${key}`];
      return next;
    });
    const updated = cloneListing(listing);
    if (applyDraft(updated, key)) emitChange(updated);
  };

  // A field saves on blur. Closing the listing, switching to another one, or
  // leaving the page does not always blur it, so save whatever is typed then.
  const commitDrafts = () => {
    const prefix = `${tab}|`;
    const keys = Object.keys(drafts).filter((id) => id.startsWith(prefix)).map((id) => id.slice(prefix.length));
    if (!keys.length || editingDisabled.current) return;
    const updated = cloneListing(listing);
    let changed = false;
    for (const key of keys) changed = applyDraft(updated, key) || changed;
    setDrafts({});
    if (changed) emitChange(updated);
  };
  const commitDraftsRef = React.useRef(commitDrafts);
  const hasDrafts = Object.keys(drafts).length > 0;
  const reloadFields = useMutation({
    mutationFn: ({ market, revision }: { market: string; revision: string }) =>
      api.nativeMarketplaces.refreshForm(convId, market, revision),
    onSuccess: async (result, { market }) => {
      await queryClient.invalidateQueries({ queryKey: ["listing", convId] });
      // Use the returned revision even before React renders it. This also
      // refreshes notes when rebuilding did not change the saved definition.
      const current = queryClient.getQueryData<ListingDocument>(["listing", convId]);
      if (current?.current_revision_id === result.revision_id) {
        await queryClient.fetchQuery({
          ...nativeFormOptions(convId, market, current.listing, result.revision_id), staleTime: 0,
        });
      }
      await Promise.all(["native-preflight", "revisions"].map(key =>
        queryClient.invalidateQueries({ queryKey: [key, convId] })));
    },
    onError: (err: Error) => {
      toastManager.add({ type: "error", title: "Could not reload fields", description: err.message });
    },
  });
  React.useLayoutEffect(() => {
    commitDraftsRef.current = commitDrafts;
  });
  React.useEffect(() => {
    const pending = { current: hasDrafts };
    const unregister = registerUnsavedWork({
      pending: () => pending.current,
      commit: () => {
        pending.current = false;
        commitDraftsRef.current();
      },
    });
    return () => {
      // Unmounting: save what was typed, then stop counting it as unsaved.
      if (pending.current) commitDraftsRef.current();
      unregister();
    };
  }, [hasDrafts]);

  const commitField = (key: string, value: string | string[] | boolean, also: Record<string, string> = {}) => {
    if (editingDisabled.current || agentFieldLocked(key, agentLocks) || fields.find((field) => field.key === key)?.locked) return;
    if (typeof value === "string" && value.trim() === "[object Object]") return;
    const updated = cloneListing(listing);
    const field = fields.find((entry) => entry.key === key);
    const stored = typeof value === "string" ? field?.optionValues?.[value] ?? (field?.type === "number" ? coerce(value) : value)
      : Array.isArray(value) ? value.map(label => field?.optionValues?.[label] ?? label) : value;
    setListingEditorValue(updated, key, stored);
    for (const [path, extra] of Object.entries(also)) setListingEditorValue(updated, path, extra);
    if (key === "poshmark_specifics.nativeSizeId") {
      setListingEditorValue(updated, "poshmark_specifics.sizeSetId", fields.find((row) => row.key === "poshmark_specifics.sizeSetId")?.defaultValue || "");
    }
    for (const dependent of field?.clears || []) setListingEditorValue(updated, dependent,
      dependent.endsWith(".attributes") ? {} : dependent.endsWith(".inventoryRows") ? [] : "");
    emitChange(updated);
  };

  const setFieldDraft = (key: string, value: string) => {
    if (editingDisabled.current || agentFieldLocked(key, agentLocks) || fields.find((field) => field.key === key)?.locked) return;
    setDrafts((current) => ({ ...current, [`${tab}|${key}`]: value }));
  };

  // Rows, visibility and sections only change with the form, the listing or a typed value,
  // not with the saving flag, a poll or a parent re-render.
  const { viewFields, renderedFields } = React.useMemo(() => {
    const viewContext = { tab, listing, local, fields, categoryStale };
    const presented = fields.map((field) => presentNativeField(field, viewContext));
    return { viewFields: presented, renderedFields: presented.filter((field) => nativeFieldVisible(field, viewContext)) };
  }, [tab, listing, local, fields, categoryStale]);
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
  const sections = React.useMemo(() => pageLayout ? nativeFormSections(renderedFields)
    : tab === "general" ? generalFormSections(renderedFields) : groupFieldsIntoSections(renderedFields), [pageLayout, tab, renderedFields]);
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
              ? "Some choices could not be refreshed. Check each field's choices before listing."
              : categoryForm?.known === false
                ? "The full category form is unavailable. Reference choices are shown where available."
                : null;
  // Renders its own hint beside the selected count.
  const multiSelectControl = (f: EditorField) => Boolean(f.multi)
    && !["inventory", "variations", "boolean", "textarea"].includes(String(f.type))
    && fieldLeaf(f.key) !== "description";
  const control = (f: EditorField) => {
    const id = `listing-field-${f.key}`;
    const locked = Boolean(disabled || f.locked || agentFieldLocked(f.key, agentLocks));
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
      disabled={locked} onChange={(choice) => commitField(f.key, choice.label,
        // Etsy's category keeps its breadcrumb beside the id, as category fills write it.
        f.key === "direct_category_ids.etsy" ? { "direct_category_ids.etsy_path": choice.label } : {})} />;
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
    if (tab === "general" && f.key === "category_path") {
      return <GeneralCategorySelect id={id} fieldKey={f.key} value={text} disabled={locked}
        onChange={(path) => commitField(f.key, path)}
        fallback={<Input nativeInput id={id} data-listing-field={f.key} value={text} readOnly={locked} disabled={locked}
          placeholder="Men > Shirts > T-Shirts" onChange={(e) => { setFieldDraft(f.key, e.target.value); }} onBlur={() => handleBlur(f.key)} />} />;
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
      {nativeTab && <div className="flex justify-end py-2">
        <Button size="xs" variant="ghost" disabled={disabled || saving || hasDrafts || !revisionId || reloadFields.isPending}
          onClick={() => { if (revisionId) reloadFields.mutate({ market: tab, revision: revisionId }); }}>
          {reloadFields.isPending ? "Reloading fields…" : "Reload fields"}
        </Button>
      </div>}
      {tab !== "general" && formStatus && <p role="status" className="border-b py-3 text-xs text-muted-foreground">{formStatus}</p>}
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

      {sections.map((section, sectionIndex) => {
        const measurementsHere = tab === "general" && generalSections && sectionIndex === measurementsAt(sections);
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
                const source = !nativeTab && f.optionsSource && f.optionsSource !== "saved"
                  ? f.optionsSource === "marketplace" ? "Marketplace choices" : f.optionsSource === "vendoo" ? "Imported category choices" : "Reference choices"
                  : null;
                const ask = onAskChat && !f.locked ? () => onAskChat(
                  `Fill or correct only ${marketplaceName(tab)} / ${f.label}${f.section ? ` (${f.section})` : ""} using the photos and seller evidence. Preserve all other fields.`,
                ) : undefined;
                const tools = source || ask ? <FieldTools label={f.label} source={source} onAsk={ask} /> : null;
                const labelRow = f.type !== "cascade" && f.presentation !== "button" && Boolean(f.label);
                return (
                <React.Fragment key={f.key}>
                {pageLayout && f.group && visible[index - 1]?.group !== f.group && (
                  <div className="col-span-full min-w-0">
                    <p className="font-medium text-sm">{f.group}</p>
                    {f.groupHelp && <p className="mt-0.5 text-muted-foreground text-xs">{f.groupHelp}</p>}
                  </div>
                )}
                <fieldset disabled={agentFieldLocked(f.key, agentLocks)} data-listing-row={f.key} className={cn("group/field min-w-0", nativeTab
                  ? f.width === "half" ? "@xl:col-span-6" : f.width === "third" ? "@xl:col-span-4"
                    : f.width === "quarter" ? "@xl:col-span-3" : "col-span-full"
                  : fullWidth(f) && "col-span-full")} tabIndex={-1}
                  onFocus={event => event.currentTarget.classList.add("ring-2", "ring-ring", "rounded-lg")}
                  onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) event.currentTarget.classList.remove("ring-2", "ring-ring", "rounded-lg"); }}>
                  {agentFieldLocked(f.key, agentLocks) && <span role="status" className="text-xs text-muted-foreground">Agent editing…</span>}
                  {labelRow && (f.hashtagMax != null ? (
                    <div className="mb-1.5 flex items-baseline justify-between gap-2">
                      <span className="flex min-w-0 items-center gap-1.5">
                        <FieldLabel htmlFor={`listing-field-${f.key}`} className={cn("mb-0", f.labelHidden && "sr-only")}>{f.label}</FieldLabel>
                        {fieldDefault(f)}
                      </span>
                      {tools}
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
                      {tools}
                      <span className="text-muted-foreground text-xs tabular-nums">{formFieldText(local[f.key]).length}/{f.maxLength}</span>
                    </div>
                  ) : (
                    <div className="mb-1.5 flex items-center gap-2">
                      <FieldLabel htmlFor={`listing-field-${f.key}`} className={cn("mb-0", f.labelHidden && "sr-only")}>{f.label}</FieldLabel>
                      {fieldDefault(f)}
                      {badge && <button type="button" disabled={disabled || badge.locked} aria-pressed={badgeOn} className="rounded-full border px-2 py-0.5 text-xs font-medium"
                        onClick={() => commitField(badge.key, !badgeOn)}>{badgeOn ? "Smart Sell ON" : "Smart Sell OFF"}</button>}
                      {tools}
                    </div>
                  ))}
                  <div className={cn(f.controlWidth === "half" && "@xl:w-1/2")}>{control(f)}</div>
                  {suggestedCategory(listing, tab, f.key) && <p role="status" className="mt-1 flex flex-wrap items-baseline gap-x-2 text-xs text-warning-foreground">
                    <span>Suggested: check it. No exact {marketplaceName(tab)} match.</span>
                    {!disabled && !f.locked && <InlineButton type="button" className="text-xs" onClick={() => {
                      const updated = cloneListing(listing);
                      setListingEditorValue(updated, `direct_category_ids.${tab}_suggested`, undefined);
                      emitChange(updated);
                    }}>Looks right</InlineButton>}
                  </p>}
                  {!labelRow && tools && <div className="mt-1 flex">{tools}</div>}
                  {f.help && f.type !== "photos" && !multiSelectControl(f) && <p className="mt-1 text-xs text-muted-foreground">{f.help}</p>}
                  {uniqueMarketplaceMessages(f.type === "photos" ? [] : fieldWarnings.get(f.key) ?? [], shownMessages).map((message) =>
                    <p key={message} role="alert" className="mt-1 text-xs text-warning-foreground">{message}</p>)}
                  {f.maxLength != null && (f.multi ? formFieldValues(local[f.key], f.options) : [formFieldText(local[f.key])]).some((value) => value.length > f.maxLength!) &&
                    <p role="alert" className="mt-1 text-xs text-warning-foreground">Each value allows at most {f.maxLength} characters. Shorten the saved value.</p>}
                  {f.required && f.selectionOnly && !f.options?.length && <p role="alert" className="mt-1 text-xs text-warning-foreground">Choices unavailable. Reopen the listing to load this field.</p>}
                </fieldset>
                </React.Fragment>
                );
              })}
            </div>
            {revealButtons(section.title, section.fields, (name) => !name.startsWith("See "))}
          </>
        );
        // A part of the page with no heading (Mercari's photos) has none here either.
        if (!section.title) return <section key={section.id} className="min-w-0 border-b py-4 first:pt-1 last:border-b-0">{rows}</section>;
        return (
          <React.Fragment key={`${tab}-${section.id}`}>
          {measurementsHere && generalSections.measurements}
          <InspectorSection
            className="gap-3 py-4"
            title={section.title}
            titleId={`listing-section-${tab}-${section.id}`}
            meta={section.id === "listing" || !counted.length || (tab === "general" && counted.length < 2) ? null
              : `${filled} of ${counted.length} filled`}
            {...(tab === "general" && section.id === GENERAL_FOLDED_SECTION ? { open: moreOpen, onOpenChange: setMoreOpen } : {})}
          >
            {rows}
          </InspectorSection>
          </React.Fragment>
        );
      })}
      {tab === "general" && generalSections && measurementsAt(sections) === sections.length && generalSections.measurements}
      {tab === "general" && generalSections?.notes}
    </fieldset>
  );
}

/** Measurements follow what the item is and its condition, ahead of shipping and stock. */
const AFTER_MEASUREMENTS = new Set(["shipping", "inventory", "other"]);
function measurementsAt(sections: { id: string }[]): number {
  const at = sections.findIndex((section) => AFTER_MEASUREMENTS.has(section.id));
  return at < 0 ? sections.length : at;
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
  etsy: ["Title", "Description", "Price", "Etsy price", "Quantity", "What type of item is it?",
    "Who Made It?", "What Is It?", "When Was It Made?", "Etsy category"],

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
  const schema = schemaFieldsForTab(forms, tab) ?? [];
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

/** Anteroom filled this tab's category with the closest option, not an exact match, and the seller has not checked it. */
export function suggestedCategory(listing: ListingData | undefined, tab: string, key: string): boolean {
  if (key !== `direct_category_ids.${tab}`) return false;
  const ids = listing?.direct_category_ids;
  if (!ids || typeof ids !== "object" || Array.isArray(ids)) return false;
  const direct = ids as Record<string, unknown>;
  return direct[`${tab}_suggested`] === true && Boolean(direct[tab]);
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

function coerce(val: string): string | number | null {
  if (val === "") return null;
  if (!isNaN(Number(val)) && val.trim() !== "") return Number(val);
  return val;
}

const ACTIVE_SEND_STATUSES = new Set(["queued", "awaiting_extension", "dispatched"]);
