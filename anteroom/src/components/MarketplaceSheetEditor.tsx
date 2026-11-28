import { useEffect, useMemo, useRef, useState, useSyncExternalStore, type MutableRefObject } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRightIcon, CheckIcon, ExternalLinkIcon, SquareArrowOutUpRightIcon } from "lucide-react";
import { api } from "../api/client";
import { invalidateSheetForListing, refreshInventory } from "../api/inventoryRefresh";
import { listingEdits } from "../api/listingEdits";
import { nativeFormOptions } from "../api/listingForms";
import type { NativeMarketplace } from "../api/nativeMarketplaces";
import type { ListingData, ListingSheetRow, MarketplaceFormField } from "../api/types";
import { useWorkspaceRuntime } from "../app/runtime";
import {
  cloneListing,
  ebayPromotionMessage,
  ebayPromotionRate,
  expandJoinedChoices,
  formFieldText,
  marketplaceDisplayValue,
  setListingEditorValue,
} from "../listingPaths";
import { nativeFormCategoryKey } from "./nativeFormStability";
import { agentFieldLocked } from "./agentFieldLocks";
import { pathCategoryChoices } from "./categoryTree";
import { marketplaceCell } from "./inventoryTable";
import {
  CategoryTreeSelect,
  DependentSelectPair,
  EbayCategorySelect,
  FieldLabel,
  NativeSelect,
  SearchableMultiSelect,
  SearchableSelect,
  type CascadeDepartment,
} from "./ListingFormControls";
import { marketplaceName } from "./marketplaceNames";
import {
  canListFromSheet,
  orderedQuickEditColumns,
  sheetMissingFields,
  sheetPriceLabel,
  type SheetColumn,
} from "./marketplaceSheetConfig";
import {
  commitNativeField,
  independentFieldKey,
  isInheritedGeneralLock,
  optionLabelForValue,
  type CommitFieldSpec,
} from "./nativeFieldCommit";
import { resolvedEditorValue, type NativeFormRow } from "./nativeFormStability";
import { ListingThumb } from "./sidebar/ListingBits";
import { displayCoverUrl } from "./sidebar/coverSrc";
import type { Listing } from "./sidebar/listingShelves";
import { Badge } from "./ui/badge";
import { Button, InlineButton } from "./ui/button";
import { Input } from "./ui/input";
import { Popover, PopoverPopup } from "./ui/popover";
import { Sheet, SheetPopup, SheetTitle } from "./ui/sheet";
import { Skeleton } from "./ui/skeleton";
import { Spinner } from "./ui/spinner";
import { Textarea } from "./ui/textarea";
import { toastManager } from "./ui/toast";

interface MappedNativeField extends CommitFieldSpec, NativeFormRow {
  label: string;
  required: boolean;
  multi: boolean;
  selectionOnly: boolean;
  options: string[];
  optionsSource?: "marketplace" | "vendoo" | "reference" | "saved";
  maxValues?: number | null;
  maxLength?: number | null;
  minValue?: number;
  maxValue?: number;
  step?: number;
  placeholder?: string;
  optionGroups?: Record<string, string>;
  cascade?: CascadeDepartment[];
  rawField: MarketplaceFormField;
}

function mapFormField(field: MarketplaceFormField, marketplace: NativeMarketplace): MappedNativeField {
  const rawKey = field.path || field.key;
  const key = independentFieldKey(marketplace, rawKey);
  const inherited = isInheritedGeneralLock(field.locked, field.help);
  return {
    key,
    label: field.label,
    type: field.input_type,
    locked: inherited ? false : field.locked,
    optionValues: field.option_values,
    clears: field.clears,
    defaultValue: field.value,
    required: field.required,
    multi: field.multi,
    selectionOnly: field.selection_only,
    options: field.options ?? [],
    optionsSource: field.options_source,
    maxValues: field.max_values,
    maxLength: field.max_length,
    minValue: field.min_value,
    maxValue: field.max_value,
    step: field.step,
    placeholder: field.placeholder,
    optionGroups: field.option_groups,
    cascade: field.cascade as CascadeDepartment[] | undefined,
    rawField: field,
  };
}

function displayValueForField(
  field: MappedNativeField,
  listing: ListingData,
  marketplace: NativeMarketplace,
): string {
  const raw = field.selectionOnly || field.key === "direct_category_ids.ebay"
    ? resolvedEditorValue(field, listing)
    : marketplaceDisplayValue(listing, field.key, {
        tab: marketplace,
        locked: field.locked,
        native: true,
        defaultValue: field.defaultValue,
      });
  return optionLabelForValue(formFieldText(raw), field.optionValues);
}

function ebayCategoryAgentLocked(agentLocks: readonly string[]) {
  return agentFieldLocked("direct_category_ids.ebay", agentLocks)
    || agentFieldLocked("direct_category_ids.ebay_path", agentLocks);
}

const EMPTY_EDIT_SNAPSHOT = { draft: null, saving: false, error: null } as const;
const NOOP_SUBSCRIBE = () => () => {};
const GET_EMPTY_SNAPSHOT = () => EMPTY_EDIT_SNAPSHOT;

/** Lazily loads the listing document + native form + activity locks and saves edits via `listingEdits`. */
function useSheetListingForm(convId: string | null, marketplace: NativeMarketplace) {
  const queryClient = useQueryClient();
  const runtime = useWorkspaceRuntime();
  const formDisabled = runtime.hosted && !runtime.marketplaceForms.has(marketplace);
  const edits = useMemo(() => (convId ? listingEdits(queryClient, convId) : null), [queryClient, convId]);
  const editState = useSyncExternalStore(
    edits ? edits.subscribe : NOOP_SUBSCRIBE,
    edits ? edits.getSnapshot : GET_EMPTY_SNAPSHOT,
    GET_EMPTY_SNAPSHOT,
  );
  const docQuery = useQuery({
    queryKey: ["listing", convId],
    queryFn: () => api.listings.get(convId!),
    enabled: Boolean(convId),
    staleTime: 30_000,
  });

  const activityQuery = useQuery({
    queryKey: ["activity", convId],
    queryFn: ({ signal }) => api.conversations.activity(convId!, { signal }),
    enabled: Boolean(convId),
    staleTime: 5_000,
  });
  const agentLocks = useMemo(
    () => activityQuery.data?.locked_fields ?? [],
    [activityQuery.data?.locked_fields],
  );

  const persistedListing: ListingData = useMemo(
    () => docQuery.data?.listing ?? {},
    [docQuery.data?.listing],
  );
  const listing: ListingData = useMemo(
    () => editState.draft ?? persistedListing,
    [editState.draft, persistedListing],
  );
  const revisionId = docQuery.data?.current_revision_id ?? null;
  const categoryChanged = nativeFormCategoryKey(listing, marketplace) !== nativeFormCategoryKey(persistedListing, marketplace);

  // Match ListingEditor: wait until in-flight/unpersisted edits settle before
  // querying nativeForm so category changes are persisted before fetching dependent choices.
  const formQuery = useQuery({
    ...nativeFormOptions(convId ?? "", marketplace, persistedListing, revisionId),
    enabled: Boolean(convId && docQuery.data && !editState.saving && !editState.draft),
  });

  const fields = useMemo(
    () => (formQuery.data?.fields ?? []).map((entry) => mapFormField(entry, marketplace)),
    [formQuery.data?.fields, marketplace],
  );

  const commit = (key: string, value: string | string[] | boolean, also?: Record<string, string>) => {
    if (!edits || !docQuery.data) return;
    const targetKey = independentFieldKey(marketplace, key);
    const updated = commitNativeField(listing, fields, targetKey, value, {
      also,
      agentLocks,
      disabled: formDisabled,
    });
    if (updated) edits.save(updated);
  };

  const commitEbayCategory = (categoryId: string, path: string) => {
    if (
      !edits
      || !docQuery.data
      || formDisabled
      || ebayCategoryAgentLocked(agentLocks)
    ) return;
    const updated = cloneListing(listing);
    setListingEditorValue(updated, "direct_category_ids.ebay", categoryId);
    setListingEditorValue(updated, "direct_category_ids.ebay_path", path);
    edits.save(updated);
  };

  return {
    docQuery,
    formQuery,
    listing,
    revisionId,
    fields,
    editState,
    formDisabled,
    categoryChanged,
    agentLocks,
    commit,
    commitEbayCategory,
    retrySave: () => edits?.retry(),
    discardDraft: () => edits?.discard(),
  };
}

/** Shared control renderer for both the desktop cell popover and the phone Quick Edit sheet. */
function SheetFieldControl({
  field,
  column,
  listing,
  marketplace,
  phoneNativeSelect,
  formDisabled,
  categoryChanged,
  agentLocks,
  flushRef,
  onCommit,
  onCommitEbayCategory,
  onOpenFull,
  onDone,
}: {
  field: MappedNativeField | undefined;
  column: SheetColumn;
  listing: ListingData;
  marketplace: NativeMarketplace;
  phoneNativeSelect: boolean;
  formDisabled: boolean;
  categoryChanged: boolean;
  agentLocks: readonly string[];
  flushRef?: MutableRefObject<(() => void) | null>;
  onCommit: (key: string, value: string | string[] | boolean, also?: Record<string, string>) => void;
  onCommitEbayCategory: (categoryId: string, path: string) => void;
  onOpenFull: () => void;
  onDone?: () => void;
}) {
  const fieldKey = field?.key ?? independentFieldKey(marketplace, column.key);
  const id = `sheet-field-${column.key}`;
  const text = field
    ? displayValueForField(field, listing, marketplace)
    : formFieldText(marketplaceDisplayValue(listing, fieldKey, { tab: marketplace }));
  const isAgentLocked = fieldKey === "direct_category_ids.ebay"
    ? ebayCategoryAgentLocked(agentLocks)
    : agentFieldLocked(fieldKey, agentLocks);
  const staleChoices = categoryChanged && column.kind !== "category"
    && (column.kind === "select" || column.kind === "multi"
      || Boolean(field?.selectionOnly || field?.multi || field?.options.length || field?.type === "cascade"));
  const controlDisabled = Boolean(formDisabled || field?.locked || isAgentLocked || staleChoices);

  const [draft, setDraft] = useState(text);
  const draftRef = useRef(draft);
  const textRef = useRef(text);
  const fieldKeyRef = useRef(fieldKey);
  const disabledRef = useRef(controlDisabled);
  const onCommitRef = useRef(onCommit);
  draftRef.current = draft;
  fieldKeyRef.current = fieldKey;
  disabledRef.current = controlDisabled;
  onCommitRef.current = onCommit;

  useEffect(() => {
    if (textRef.current !== text) {
      textRef.current = text;
      setDraft(text);
    }
  }, [text]);

  const isPlainTextOrNumber = column.kind === "text" || column.kind === "textarea" || column.kind === "number";
  const usesTextDraft = Boolean(
    (field && !field.multi && !field.selectionOnly && field.options.length === 0 && field.type !== "cascade"
      && field.type !== "inventory" && field.type !== "variations" && fieldKey !== "direct_category_ids.ebay")
    || (!field && isPlainTextOrNumber),
  );

  useEffect(() => {
    if (!usesTextDraft) {
      if (flushRef) flushRef.current = null;
      return;
    }
    const flush = () => {
      if (!disabledRef.current && draftRef.current !== textRef.current) {
        const next = draftRef.current;
        textRef.current = next;
        onCommitRef.current(fieldKeyRef.current, next);
      }
    };
    if (flushRef) flushRef.current = flush;
    return () => {
      flush();
      if (flushRef && flushRef.current === flush) flushRef.current = null;
    };
  }, [usesTextDraft, flushRef]);

  const lockNote = isAgentLocked
    ? "Updating in chat…"
    : formDisabled
      ? `Editing ${marketplaceName(marketplace)} fields is not available on this workspace.`
      : null;

  if (column.kind === "complex" || field?.type === "inventory" || field?.type === "variations") {
    return (
      <div className="flex items-center justify-between gap-2 rounded-lg border border-border/60 bg-card/40 px-3 py-2.5">
        <span className="truncate text-sm text-muted-foreground">
          {text || `Edit ${column.label.toLowerCase()} in the ${marketplaceName(marketplace)} form`}
        </span>
        <Button type="button" variant="outline" size="sm" className={phoneNativeSelect ? "min-h-11 shrink-0" : "shrink-0"} onClick={onOpenFull}>
          Open form
        </Button>
      </div>
    );
  }

  // B2: Never fall back to a free-text input for category/select/multi columns when the native field is missing.
  if (!field && !isPlainTextOrNumber) {
    return (
      <div className="flex items-center justify-between gap-2 rounded-lg border border-border/60 bg-card/40 px-3 py-2.5">
        <span className="truncate text-sm text-muted-foreground">
          {text || `Open the ${marketplaceName(marketplace)} form to choose ${column.label.toLowerCase()}`}
        </span>
        <Button type="button" variant="outline" size="sm" className={phoneNativeSelect ? "min-h-11 shrink-0" : "shrink-0"} onClick={onOpenFull}>
          Open form
        </Button>
      </div>
    );
  }

  if (field?.type === "cascade" && field.cascade?.length) {
    const stored = field.optionValues?.[text] ?? text;
    return (
      <div className="space-y-1">
        <DependentSelectPair
          id={id}
          value={stored}
          departments={field.cascade}
          disabled={controlDisabled}
          onChange={(next) => {
            onCommit(field.key, next);
            onDone?.();
          }}
        />
        {lockNote ? <p className="text-2xs text-muted-foreground">{lockNote}</p> : null}
      </div>
    );
  }

  if (fieldKey === "direct_category_ids.ebay") {
    return (
      <div className="space-y-1">
        <EbayCategorySelect
          id={id}
          value={text}
          disabled={controlDisabled}
          onChange={(categoryId, path) => {
            onCommitEbayCategory(categoryId, path);
            onDone?.();
          }}
        />
        {lockNote ? <p className="text-2xs text-muted-foreground">{lockNote}</p> : null}
      </div>
    );
  }

  const categoryChoices = field && !field.multi ? pathCategoryChoices(field) : null;
  if (categoryChoices) {
    return (
      <div className="space-y-1">
        <CategoryTreeSelect
          key={text}
          id={id}
          fieldKey={field!.key}
          value={text}
          choices={categoryChoices}
          disabled={controlDisabled}
          onChange={(choice) => {
            onCommit(
              field!.key,
              choice.label,
              field!.key === "direct_category_ids.etsy" ? { "direct_category_ids.etsy_path": choice.label } : undefined,
            );
            onDone?.();
          }}
        />
        {lockNote ? <p className="text-2xs text-muted-foreground">{lockNote}</p> : null}
      </div>
    );
  }

  if (field?.multi) {
    const raw = resolvedEditorValue(field, listing);
    const selected = expandJoinedChoices(
      Array.isArray(raw) ? raw.map((item) => optionLabelForValue(String(item), field.optionValues)) : text,
      field.options,
    );
    return (
      <div className="space-y-1">
        <SearchableMultiSelect
          id={id}
          fieldKey={field.key}
          value={selected}
          options={field.options}
          selectionOnly={field.selectionOnly ?? Boolean(field.options.length)}
          choicesVerified={field.optionsSource === "marketplace"}
          disabled={controlDisabled}
          maxValues={field.maxValues ?? undefined}
          maxLength={field.maxLength ?? undefined}
          onChange={(values) => onCommit(field.key, values)}
        />
        {lockNote ? <p className="text-2xs text-muted-foreground">{lockNote}</p> : null}
      </div>
    );
  }

  if (field && (field.options.length > 0 || field.selectionOnly)) {
    // On phones, DESIGN.md explicitly prefers NativeSelect for marketplace option lists so iOS/Android
    // show their native wheel picker; keep SearchableSelect when there are > 150 options (e.g. Brands).
    if (phoneNativeSelect && field.options.length > 0 && field.options.length <= 150 && field.selectionOnly) {
      const hasCurrent = !text || field.options.includes(text);
      return (
        <div className="space-y-1">
          <NativeSelect
            id={id}
            data-listing-field={field.key}
            value={text}
            disabled={controlDisabled}
            className="[&_select]:h-11"
            onChange={(event) => {
              const next = event.target.value;
              if (!next && text) return;
              onCommit(field.key, next);
            }}
          >
            <option value="" disabled={Boolean(text)}>Choose {column.label.toLowerCase()}…</option>
            {!hasCurrent ? <option value={text}>{text}</option> : null}
            {field.options.map((opt) => (
              <option key={opt} value={opt}>{opt}</option>
            ))}
          </NativeSelect>
          {lockNote ? <p className="text-2xs text-muted-foreground">{lockNote}</p> : null}
        </div>
      );
    }
    return (
      <div className="space-y-1">
        <SearchableSelect
          id={id}
          fieldKey={field.key}
          value={text}
          options={field.options}
          disabled={controlDisabled}
          selectionOnly={field.selectionOnly}
          choicesVerified={field.optionsSource === "marketplace"}
          maxLength={field.maxLength ?? undefined}
          optionGroups={field.optionGroups}
          onChange={(value) => {
            onCommit(field.key, value);
            onDone?.();
          }}
        />
        {lockNote ? <p className="text-2xs text-muted-foreground">{lockNote}</p> : null}
      </div>
    );
  }

  if (column.kind === "textarea" || field?.type === "textarea") {
    return (
      <div className="space-y-1">
        <Textarea
          id={id}
          data-listing-field={fieldKey}
          value={draft}
          disabled={controlDisabled}
          maxLength={field?.maxLength ?? undefined}
          placeholder={field?.placeholder}
          className="[&_textarea]:min-h-24 [&_textarea]:py-2"
          onChange={(event) => setDraft(event.target.value)}
          onBlur={() => {
            if (!controlDisabled && draft !== textRef.current) {
              textRef.current = draft;
              onCommit(fieldKey, draft);
            }
          }}
        />
        {lockNote ? <p className="text-2xs text-muted-foreground">{lockNote}</p> : null}
      </div>
    );
  }

  const isNumber = column.kind === "number" || field?.type === "number";
  return (
    <div className="space-y-1">
      <Input
        nativeInput
        id={id}
        data-listing-field={fieldKey}
        type={isNumber ? "number" : "text"}
        inputMode={isNumber ? "decimal" : undefined}
        min={field?.minValue}
        max={field?.maxValue}
        step={isNumber ? field?.step ?? "any" : undefined}
        maxLength={field?.maxLength ?? undefined}
        placeholder={field?.placeholder || (isNumber ? "0.00" : `Enter ${column.label.toLowerCase()}`)}
        disabled={controlDisabled}
        className={phoneNativeSelect ? "h-11" : undefined}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => {
          if (!controlDisabled && draft !== textRef.current) {
            textRef.current = draft;
            onCommit(fieldKey, draft);
          }
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            if (!controlDisabled && draft !== textRef.current) {
              textRef.current = draft;
              onCommit(fieldKey, draft);
            }
            onDone?.();
          }
        }}
      />
      {lockNote ? <p className="text-2xs text-muted-foreground">{lockNote}</p> : null}
    </div>
  );
}

export interface ActiveDesktopCell {
  listing: Listing;
  column: SheetColumn;
  anchor: HTMLElement;
}

/**
 * Single desktop inline editor popover mounted outside the virtualizer so
 * scrolling never tears down the active editor or drops focus.
 */
export function DesktopCellPopover({
  active,
  marketplace,
  sheetRow,
  onClose,
  onOpenListing,
}: {
  active: ActiveDesktopCell | null;
  marketplace: NativeMarketplace;
  sheetRow: ListingSheetRow | undefined;
  onClose: () => void;
  onOpenListing: (id: string, tab?: string) => void;
}) {
  const convId = active?.listing.id ?? null;
  const { docQuery, formQuery, listing, fields, editState, formDisabled, categoryChanged, agentLocks, commit, commitEbayCategory } =
    useSheetListingForm(convId, marketplace);
  const flushRef = useRef<(() => void) | null>(null);
  const column = active?.column;
  const targetKey = column ? independentFieldKey(marketplace, column.key) : null;
  const field = targetKey ? fields.find((entry) => entry.key === targetKey) : undefined;
  const cell = column ? sheetRow?.cells?.[column.key] : undefined;
  const loading = Boolean(active) && (docQuery.isLoading || (formQuery.isLoading && !formQuery.data));
  const loadFailed = docQuery.isError || formQuery.isError;
  const marketLabel = marketplaceName(marketplace);

  const flushAndClose = () => {
    flushRef.current?.();
    onClose();
  };

  return (
    <Popover
      open={Boolean(active)}
      onOpenChange={(open) => {
        if (!open) flushAndClose();
      }}
    >
      <PopoverPopup
        anchor={active?.anchor ?? null}
        align="start"
        side="bottom"
        sideOffset={6}
        width="md"
        padding="compact"
        touchBackdrop
      >
        {active && column ? (
          <div className="flex flex-col gap-2.5 px-3 py-2">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-xs font-medium text-foreground">
                {column.label}
                {cell?.required ? <span className="text-muted-foreground"> · Required</span> : null}
              </span>
              {editState.saving ? (
                <span className="inline-flex items-center gap-1 text-2xs text-muted-foreground">
                  <Spinner size="sm" tone="muted" /> Saving…
                </span>
              ) : null}
            </div>
            {cell?.rejected && cell.error ? (
              <p className="text-xs text-destructive-foreground">{cell.error}</p>
            ) : cell?.missing && cell.error ? (
              <p className="text-xs text-warning-foreground">{cell.error}</p>
            ) : null}
            {editState.error ? (
              <p className="text-xs text-destructive-foreground">{editState.error}</p>
            ) : null}
            {loading ? (
              <div
                className="space-y-1.5 py-1"
                aria-label={sheetRow?.needs_load ? `Loading ${marketLabel} form` : "Loading choices"}
              >
                {sheetRow?.needs_load ? (
                  <p className="text-2xs text-muted-foreground">Loading {marketLabel} form…</p>
                ) : null}
                <Skeleton className="h-8 w-full" />
              </div>
            ) : loadFailed ? (
              <p className="text-xs text-destructive-foreground">
                Could not load {marketLabel} choices. Open the full form to edit.
              </p>
            ) : (
              <SheetFieldControl
                field={field}
                column={column}
                listing={listing}
                marketplace={marketplace}
                phoneNativeSelect={false}
                formDisabled={formDisabled}
                categoryChanged={categoryChanged}
                agentLocks={agentLocks}
                flushRef={flushRef}
                onCommit={commit}
                onCommitEbayCategory={commitEbayCategory}
                onOpenFull={() => {
                  flushAndClose();
                  onOpenListing(active.listing.id, marketplace);
                }}
                onDone={flushAndClose}
              />
            )}
            <div className="flex items-center justify-between border-t border-border/50 pt-2">
              <InlineButton
                type="button"
                className="inline-flex items-center gap-1 text-xs"
                onClick={() => {
                  flushAndClose();
                  onOpenListing(active.listing.id, marketplace);
                }}
              >
                Open {marketLabel} form
                <SquareArrowOutUpRightIcon className="size-3" />
              </InlineButton>
              <Button type="button" variant="ghost" size="xs" onClick={flushAndClose}>
                Done
              </Button>
            </div>
          </div>
        ) : null}
      </PopoverPopup>
    </Popover>
  );
}

/** Per-row List mutation with revision fencing and explicit toast/cache updates. */
export function useSheetListMutation(marketplace: NativeMarketplace) {
  const queryClient = useQueryClient();
  const marketLabel = marketplaceName(marketplace);
  return useMutation({
    mutationFn: async ({ convId, revisionId, confirmedEbayAdRate }: {
      convId: string; revisionId: string; confirmedEbayAdRate?: string;
    }) => {
      return api.nativeMarketplaces.list(convId, marketplace, revisionId, confirmedEbayAdRate);
    },
    retry: false,
    onSuccess: (_draft, { convId }) => {
      toastManager.add({
        type: "success",
        title: `Listing on ${marketLabel}`,
        description: "Started publishing this saved listing.",
      });
      refreshInventory(queryClient);
      invalidateSheetForListing(queryClient, convId, marketplace);
      void queryClient.invalidateQueries({ queryKey: ["native-marketplaces", convId] });
      void queryClient.invalidateQueries({ queryKey: ["marketplace-listings", convId] });
    },
    onError: (error: Error) => {
      toastManager.add({
        type: "error",
        title: `Could not list on ${marketLabel}`,
        description: error.message || "Check the listing and try again.",
      });
    },
  });
}

/**
 * Phone Quick Edit bottom sheet (`< 580px`):
 * - Explicit Close button (`×`) per WCAG 2.2 SC 2.5.7
 * - Rejected and missing required fields floated to the top
 * - 44px touch targets with NativeSelect for marketplace option lists
 * - One-handed `Next draft →` traversal and 2-step `List` confirmation
 */
export function MarketplaceQuickEditSheet({
  listing,
  marketplace,
  sheetRow,
  nextDraftId,
  onClose,
  onSelectListing,
  onOpenListing,
}: {
  listing: Listing | null;
  marketplace: NativeMarketplace;
  sheetRow: ListingSheetRow | undefined;
  nextDraftId: string | null;
  onClose: () => void;
  onSelectListing: (id: string) => void;
  onOpenListing: (id: string, tab?: string) => void;
}) {
  const runtime = useWorkspaceRuntime();
  const convId = listing?.id ?? null;
  const {
    docQuery,
    formQuery,
    listing: docListing,
    revisionId,
    fields,
    editState,
    formDisabled,
    categoryChanged,
    agentLocks,
    commit,
    commitEbayCategory,
    retrySave,
    discardDraft,
  } = useSheetListingForm(convId, marketplace);
  const targetKey = convId ? `${marketplace}:${convId}` : null;
  const [confirmSnapshot, setConfirmSnapshot] = useState<{
    key: string;
    revisionId: string;
    priceLabel: string | null;
  } | null>(null);
  // Closing or changing the target ends the confirmation session.
  if (confirmSnapshot && confirmSnapshot.key !== targetKey) {
    setConfirmSnapshot(null);
  }
  const listMutation = useSheetListMutation(marketplace);
  const isListingThisRow = Boolean(
    listing && listMutation.isPending && listMutation.variables?.convId === listing.id,
  );

  const columns = useMemo(
    () => orderedQuickEditColumns(marketplace, sheetRow),
    [marketplace, sheetRow],
  );
  const status = listing ? marketplaceCell(listing, marketplace, sheetMissingFields(sheetRow)) : null;
  const marketLabel = marketplaceName(marketplace);
  const canAct = !runtime.hosted || runtime.actionMarketplaces.has(marketplace);
  const effectiveRevision = revisionId ?? sheetRow?.revision_id ?? null;
  const hasUnsavedDraft = Boolean(editState.draft);
  const canList = canListFromSheet(listing, status, sheetRow, canAct, effectiveRevision);
  const priceLabel = sheetPriceLabel(
    marketplace,
    sheetRow,
    docQuery.data ? docListing : null,
    listing?.price,
    docQuery.data ? revisionId : null,
  );
  const confirmingList = Boolean(
    targetKey
    && confirmSnapshot
    && confirmSnapshot.key === targetKey
    && confirmSnapshot.revisionId === effectiveRevision
    && confirmSnapshot.priceLabel === priceLabel
    && canList
    && !editState.saving
    && !hasUnsavedDraft,
  );
  const loading = Boolean(listing) && (docQuery.isLoading || (formQuery.isLoading && !formQuery.data));
  const loadFailed = docQuery.isError || formQuery.isError;

  return (
    <Sheet
      open={Boolean(listing)}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetPopup
        side="bottom"
        showCloseButton
        className="max-h-[88dvh] rounded-t-2xl"
      >
        {listing ? (
          <>
            <div className="flex items-center gap-3 border-b border-border/60 px-4 py-3.5 pe-12">
              <ListingThumb
                src={displayCoverUrl(listing.cover_photo_url || listing.vendoo_cover_url)}
                className="size-10 shrink-0"
              />
              <div className="min-w-0 flex-1">
                <SheetTitle className="truncate text-base font-semibold text-foreground">
                  {listing.title || "Untitled"}
                </SheetTitle>
                <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <span>{marketLabel}</span>
                  {priceLabel ? <span className="tabular-nums text-foreground">{priceLabel}</span> : null}
                  {status ? (
                    <Badge
                      variant={
                        status.kind === "listed" ? "success"
                          : status.kind === "sold" ? "info"
                            : status.kind === "failed" ? "error"
                              : status.kind === "needs" ? "warning"
                                : "outline"
                      }
                    >
                      {status.label}
                    </Badge>
                  ) : null}
                </div>
              </div>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-4">
              {editState.error ? (
                <div className="mb-3 flex flex-col gap-2 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive-foreground">
                  <p>{editState.error}</p>
                  {hasUnsavedDraft ? (
                    <div className="flex items-center gap-2">
                      <Button type="button" variant="outline" size="xs" disabled={editState.saving} onClick={retrySave}>
                        Retry save
                      </Button>
                      <Button type="button" variant="ghost" size="xs" disabled={editState.saving} onClick={discardDraft}>
                        Discard changes
                      </Button>
                    </div>
                  ) : null}
                </div>
              ) : null}
              {loading ? (
                <div
                  className="space-y-4"
                  aria-label={sheetRow?.needs_load ? `Loading ${marketLabel} form` : "Loading listing fields"}
                >
                  {sheetRow?.needs_load ? (
                    <p className="text-xs text-muted-foreground">Loading {marketLabel} form…</p>
                  ) : null}
                  <Skeleton className="h-11 w-full" />
                  <Skeleton className="h-11 w-full" />
                  <Skeleton className="h-11 w-full" />
                </div>
              ) : loadFailed ? (
                <p className="text-sm text-destructive-foreground">
                  Could not load {marketLabel} fields. Open the full form to edit.
                </p>
              ) : (
                <div className="space-y-4">
                  {columns.map((column) => {
                    const targetFieldKey = independentFieldKey(marketplace, column.key);
                    const field = fields.find((entry) => entry.key === targetFieldKey);
                    const cell = sheetRow?.cells?.[column.key];
                    return (
                      <div key={column.key} className="space-y-1">
                        <div className="flex items-center justify-between gap-2">
                          <FieldLabel htmlFor={`sheet-field-${column.key}`} className="mb-0">
                            {column.label}
                            {cell?.required ? <span className="text-muted-foreground"> *</span> : null}
                          </FieldLabel>
                          {cell?.rejected ? (
                            <span className="text-2xs font-medium text-destructive-foreground">Rejected</span>
                          ) : cell?.missing ? (
                            <span className="text-2xs font-medium text-warning-foreground">Missing</span>
                          ) : null}
                        </div>
                        <SheetFieldControl
                          field={field}
                          column={column}
                          listing={docListing}
                          marketplace={marketplace}
                          phoneNativeSelect
                          formDisabled={formDisabled}
                          categoryChanged={categoryChanged}
                          agentLocks={agentLocks}
                          onCommit={commit}
                          onCommitEbayCategory={commitEbayCategory}
                          onOpenFull={() => {
                            onClose();
                            onOpenListing(listing.id, marketplace);
                          }}
                        />
                        {cell?.rejected && cell.error ? (
                          <p className="text-xs text-destructive-foreground">{cell.error}</p>
                        ) : cell?.missing && cell.error ? (
                          <p className="text-xs text-warning-foreground">{cell.error}</p>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            <div className="border-t border-border/60 bg-background px-4 py-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))]">
              {confirmingList && confirmSnapshot ? (
                <div className="flex flex-col gap-2.5">
                  <p className="text-xs text-foreground">
                    List on <span className="font-medium">{marketLabel}</span>
                    {confirmSnapshot.priceLabel ? (
                      <> for <span className="font-medium tabular-nums">{confirmSnapshot.priceLabel}</span></>
                    ) : null}?
                  </p>
                  {marketplace === "ebay" && ebayPromotionMessage(docQuery.data?.listing) && (
                    <p className="text-xs">{ebayPromotionMessage(docQuery.data?.listing)}</p>
                  )}
                  <div className="flex items-center gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      className="min-h-11 flex-1"
                      disabled={isListingThisRow}
                      onClick={() => setConfirmSnapshot(null)}
                    >
                      Cancel
                    </Button>
                    <Button
                      type="button"
                      className="min-h-11 flex-1"
                      disabled={isListingThisRow || editState.saving || hasUnsavedDraft}
                      onClick={() => {
                        const submittedKey = confirmSnapshot.key;
                        const submittedRev = confirmSnapshot.revisionId;
                        listMutation.mutate(
                          { convId: listing.id, revisionId: submittedRev,
                            confirmedEbayAdRate: marketplace === "ebay" ? ebayPromotionRate(docQuery.data?.listing) : undefined },
                          {
                            onSuccess: () =>
                              setConfirmSnapshot((cur) =>
                                cur?.key === submittedKey && cur?.revisionId === submittedRev ? null : cur,
                              ),
                          },
                        );
                      }}
                    >
                      {isListingThisRow ? <Spinner size="sm" /> : <CheckIcon className="size-4" />}
                      List on {marketLabel}
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="flex items-center gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    className="min-h-11"
                    onClick={() => {
                      onClose();
                      onOpenListing(listing.id, marketplace);
                    }}
                  >
                    Full form
                  </Button>
                  {nextDraftId ? (
                    <Button
                      type="button"
                      variant="outline"
                      className="min-h-11"
                      onClick={() => onSelectListing(nextDraftId)}
                    >
                      Next draft
                      <ArrowRightIcon className="size-4" />
                    </Button>
                  ) : null}
                  {status?.kind === "listed" && status.url ? (
                    <Button
                      type="button"
                      variant="outline"
                      className="ms-auto min-h-11"
                      render={<a href={status.url} target="_blank" rel="noopener noreferrer" />}
                    >
                      View
                      <ExternalLinkIcon className="size-4" />
                    </Button>
                  ) : canList ? (
                    <Button
                      type="button"
                      className="ms-auto min-h-11"
                      disabled={editState.saving || hasUnsavedDraft || isListingThisRow}
                      onClick={() => {
                        if (targetKey && effectiveRevision) {
                          setConfirmSnapshot({ key: targetKey, revisionId: effectiveRevision, priceLabel });
                        }
                      }}
                    >
                      {editState.saving ? "Saving…" : "List"}
                    </Button>
                  ) : null}
                </div>
              )}
            </div>
          </>
        ) : null}
      </SheetPopup>
    </Sheet>
  );
}
