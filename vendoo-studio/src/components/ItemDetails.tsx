import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { labelColor, labelDotClass } from "../labelColors";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { addLabel, removeLabel, splitLabels } from "./itemLabels";
import { historyRows } from "./vendooHistory";
import { BOXES_QUERY_KEY, boxStoreName } from "./boxPurchases";
import { GARMENTS, type Garment, type Measurements } from "./garmentMeasurements";
import { XIcon } from "lucide-react";
import { cn } from "../lib/utils";
import { FieldLabel, InspectorSection, NativeSelect } from "./ListingFormControls";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Spinner } from "./ui/spinner";
import { Textarea } from "./ui/textarea";
import { toggleVariants } from "./ui/toggle";

interface Props {
  convId: string;
}

interface ItemDetailsData {
  sku: string;
  sellerNotes: string;
  knownFlaws: string;
  descriptionMeasurements: string;
  cog: string;
  packageDimensions: string;
  vendooLabels: string;
  garment: Garment;
  measurements: Measurements;
}

type TextKey = Exclude<keyof ItemDetailsData, "garment" | "measurements">;

const RECENT_LABELS_KEY = "vendoo-studio.recent-labels";

function readLegacyStoredLabels(): string[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(RECENT_LABELS_KEY) || "[]");
    return Array.isArray(parsed) ? parsed.filter((item) => typeof item === "string" && item.trim()) : [];
  } catch {
    return [];
  }
}

function clearLegacyStoredLabels() {
  try {
    localStorage.removeItem(RECENT_LABELS_KEY);
  } catch {
    /* ignore quota / private-mode failures */
  }
}

function rememberLabels(
  raw: string,
  restore: string[],
  queryClient?: ReturnType<typeof useQueryClient>,
) {
  void api.settings
    .setUi({ remember_labels: raw, ...(restore.length ? { restore_labels: restore } : {}) })
    .then(() => {
      queryClient?.invalidateQueries({ queryKey: ["settings-ui"] });
    })
    .catch(() => {
      /* disk prefs are best-effort; listing notes still save */
    });
}

const DEFAULTS: ItemDetailsData = {
  sku: "",
  sellerNotes: "",
  knownFlaws: "",
  descriptionMeasurements: "",
  cog: "",
  packageDimensions: "13x10x3",
  vendooLabels: "",
  garment: "top",
  measurements: { top: {}, pants: {}, skirt: {} },
};

function parseMeasurements(raw: unknown): Measurements {
  const source = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const out: Measurements = { top: {}, pants: {}, skirt: {} };
  for (const garment of GARMENTS) {
    // Listings saved before shorts folded into pants kept a separate shorts set.
    const values = source[garment.id] ?? (garment.id === "pants" ? source.shorts : undefined);
    if (!values || typeof values !== "object") continue;
    for (const field of garment.fields) {
      const value = (values as Record<string, unknown>)[field.key];
      if (typeof value === "string" && value) out[garment.id][field.key] = value;
    }
  }
  return out;
}

export function parseNotes(notes: string | null, defaultPackageDimensions = DEFAULTS.packageDimensions): ItemDetailsData {
  if (!notes) return { ...DEFAULTS, packageDimensions: defaultPackageDimensions, measurements: parseMeasurements(null) };
  try {
    const value: unknown = JSON.parse(notes);
    const parsed = value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).some(key => key in DEFAULTS) ? value as Omit<Partial<ItemDetailsData>, "garment"> & { garment?: string } : { sellerNotes: notes };
    return {
      sku: parsed.sku || "",
      sellerNotes: parsed.sellerNotes || "",
      knownFlaws: parsed.knownFlaws || "",
      descriptionMeasurements: parsed.descriptionMeasurements || "",
      cog: parsed.cog || "",
      packageDimensions: parsed.packageDimensions || defaultPackageDimensions,
      vendooLabels: parsed.vendooLabels ?? DEFAULTS.vendooLabels,
      garment: parsed.garment === "shorts"
        ? "pants"
        : GARMENTS.some((g) => g.id === parsed.garment) ? parsed.garment as Garment : DEFAULTS.garment,
      measurements: parseMeasurements(parsed.measurements),
    };
  } catch {
    return { ...DEFAULTS, sellerNotes: notes, packageDimensions: defaultPackageDimensions, measurements: parseMeasurements(null) };
  }
}

export function ItemDetails({ convId }: Props) {
  const queryClient = useQueryClient();
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveChainRef = useRef<Promise<void>>(Promise.resolve());
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const saveGenRef = useRef(0);
  const [labelMenuOpen, setLabelMenuOpen] = useState(false);
  const [activeSuggestion, setActiveSuggestion] = useState(0);
  const [labelDraft, setLabelDraft] = useState("");
  const labelDraftRef = useRef("");
  const labelInputRef = useRef<HTMLInputElement | null>(null);
  const migratedLabelsRef = useRef(false);

  const { data: conv } = useQuery({
    queryKey: ["conversation", convId],
    queryFn: () => api.conversations.get(convId),
  });

  const { data: conversations } = useQuery({
    queryKey: ["conversations"],
    queryFn: api.conversations.list,
  });

  const { data: uiPrefs } = useQuery({
    queryKey: ["settings-ui"],
    queryFn: api.settings.ui,
  });
  const vendooOn = uiPrefs?.vendoo_enabled !== false;

  const { data: packageDimensionSettings } = useQuery({
    queryKey: ["settings-package-dimensions"],
    queryFn: api.settings.packageDimensions,
  });
  const defaultPackageDimensions = packageDimensionSettings
    ? `${packageDimensionSettings.length}x${packageDimensionSettings.width}x${packageDimensionSettings.height}`
    : DEFAULTS.packageDimensions;

  useEffect(() => {
    if (!uiPrefs || migratedLabelsRef.current) return;
    migratedLabelsRef.current = true;
    const legacy = readLegacyStoredLabels();
    if (!legacy.length) return;
    if ((uiPrefs.recent_vendoo_labels || []).length) {
      clearLegacyStoredLabels();
      return;
    }
    void api.settings
      .setUi({ recent_vendoo_labels: legacy })
      .then(() => {
        clearLegacyStoredLabels();
        queryClient.invalidateQueries({ queryKey: ["settings-ui"] });
      })
      .catch(() => {
        migratedLabelsRef.current = false;
      });
  }, [uiPrefs, queryClient]);

  const hiddenLabelKeys = useMemo(
    () => new Set((uiPrefs?.hidden_vendoo_labels || []).map((label) => label.toLowerCase())),
    [uiPrefs?.hidden_vendoo_labels],
  );

  const recentLabels = useMemo(() => {
    const seen = new Set<string>(hiddenLabelKeys);
    const ordered: string[] = [];
    for (const label of [
      ...(uiPrefs?.recent_vendoo_labels || []),
      ...readLegacyStoredLabels(),
      ...(conversations || []).flatMap((item) => item.vendoo_labels || []),
    ]) {
      const key = label.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      ordered.push(label);
    }
    return ordered;
  }, [conversations, uiPrefs?.recent_vendoo_labels, hiddenLabelKeys]);

  const [details, setDetails] = useState<ItemDetailsData>({ ...DEFAULTS });
  const detailsRef = useRef(details);

  useEffect(() => {
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
    saveGenRef.current += 1;
    if (conv) {
      const parsed = parseNotes(conv.notes, defaultPackageDimensions);
      detailsRef.current = parsed;
      setDetails(parsed);
    } else {
      const defaults = { ...DEFAULTS, packageDimensions: defaultPackageDimensions };
      detailsRef.current = defaults;
      setDetails(defaults);
    }
  }, [convId, conv?.notes, conv?.updated_at, defaultPackageDimensions]);

  useEffect(() => {
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      saveGenRef.current += 1;
    };
  }, []);

  useEffect(() => {
    setLabelMenuOpen(false);
    setActiveSuggestion(0);
    labelDraftRef.current = "";
    setLabelDraft("");
  }, [convId]);

  const save = useCallback(async (updated: ItemDetailsData) => {
    const gen = ++saveGenRef.current;
    setSaving(true);
    setSaveError(null);
    detailsRef.current = updated;
    setDetails(updated);
    // Labels newly typed into this listing come back even if removed from the history.
    const savedKeys = new Set(splitLabels(parseNotes(conv?.notes ?? null).vendooLabels).map((l) => l.toLowerCase()));
    const added = splitLabels(updated.vendooLabels).filter((label) => !savedKeys.has(label.toLowerCase()));
    rememberLabels(updated.vendooLabels, added, queryClient);
    const persist = async () => {
      try {
        if (gen !== saveGenRef.current) return;
        await api.conversations.update(convId, { notes: JSON.stringify({
          // Clear obsolete seller-entered condition; categoryOverride stays agent-managed.
          condition: "",
          sku: updated.sku,
          sellerNotes: updated.sellerNotes,
          knownFlaws: updated.knownFlaws,
          descriptionMeasurements: updated.descriptionMeasurements,
          cog: updated.cog,
          packageDimensions: updated.packageDimensions,
          vendooLabels: updated.vendooLabels,
          poshmarkOriginalPrice: "0",
          garment: updated.garment,
          measurements: updated.measurements,
        })});
        if (gen !== saveGenRef.current) return;
        queryClient.invalidateQueries({ queryKey: ["conversation", convId] });
      } catch (err) {
        if (gen !== saveGenRef.current) return;
        setSaveError(err instanceof Error ? err.message : "Could not save seller details");
      } finally {
        if (gen === saveGenRef.current) setSaving(false);
      }
    };
    const queued = saveChainRef.current.catch(() => undefined).then(persist);
    saveChainRef.current = queued;
    await queued;
  }, [convId, conv?.notes, queryClient]);

  const scheduleSave = useCallback((updated: ItemDetailsData) => {
    detailsRef.current = updated;
    setDetails(updated);
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => save(updated), 500);
  }, [save]);

  const f = (key: TextKey, type = "text") => ({
    type,
    value: details[key],
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => scheduleSave({ ...details, [key]: e.target.value }),
  });

  const garment = GARMENTS.find((g) => g.id === details.garment) || GARMENTS[0];
  const setMeasurement = (key: string, value: string) => scheduleSave({
    ...details,
    measurements: {
      ...details.measurements,
      [garment.id]: { ...details.measurements[garment.id], [key]: value },
    },
  });

  const { data: boxData } = useQuery({ queryKey: BOXES_QUERY_KEY, queryFn: api.boxes.list });
  const boxes = boxData?.boxes ?? [];
  const box = boxes.find((row) => row.id === conv?.box_id);
  const chooseBox = async (boxId: string) => {
    try {
      const updated = await api.conversations.update(convId, { box_id: boxId || null });
      queryClient.setQueryData(["conversation", convId], updated);
      void queryClient.invalidateQueries({ queryKey: BOXES_QUERY_KEY });
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Could not save the box");
    }
  };

  // Vendoo's own dates for the item: when it went live, sold, and was touched.
  const history = useMemo(() => historyRows(conv || {}), [conv]);
  const currentLabels = splitLabels(details.vendooLabels);
  const selectedKeys = new Set(currentLabels.map((label) => label.toLowerCase()));
  const token = labelDraft.trim().toLowerCase();
  const labelSuggestions = recentLabels.filter((label) => {
    const key = label.toLowerCase();
    if (selectedKeys.has(key)) return false;
    return !token || key.includes(token);
  });

  const commitLabels = (vendooLabels: string, draft = "") => {
    const next = { ...detailsRef.current, vendooLabels };
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    labelDraftRef.current = draft;
    setLabelDraft(draft);
    setActiveSuggestion(0);
    void save(next);
  };

  const chooseLabel = (suggestion: string) => {
    commitLabels(addLabel(detailsRef.current.vendooLabels, suggestion));
    setLabelMenuOpen(true);
    labelInputRef.current?.focus();
  };

  const commitDraft = (rawDraft = labelDraftRef.current) => {
    const draft = rawDraft.trim();
    if (!draft) return;
    commitLabels(addLabel(detailsRef.current.vendooLabels, draft));
    setLabelMenuOpen(true);
  };

  const removeCommittedLabel = (label: string) => {
    commitLabels(removeLabel(detailsRef.current.vendooLabels, label), labelDraftRef.current);
    labelInputRef.current?.focus();
  };

  const forgetLabel = (label: string) => {
    const key = label.toLowerCase();
    queryClient.setQueryData(["settings-ui"], (prev: typeof uiPrefs) => prev && {
      ...prev,
      recent_vendoo_labels: prev.recent_vendoo_labels.filter((item) => item.toLowerCase() !== key),
      hidden_vendoo_labels: [label, ...(prev.hidden_vendoo_labels || [])],
    });
    setActiveSuggestion((i) => Math.max(0, Math.min(i, labelSuggestions.length - 2)));
    void api.settings
      .setUi({ forget_label: label })
      .catch(() => { /* best-effort; refetch below restores truth */ })
      .finally(() => queryClient.invalidateQueries({ queryKey: ["settings-ui"] }));
  };

  return (
    <div className="@container relative flex min-w-0 flex-col">
      <InspectorSection title="Notes & Labels" titleId={`item-section-listing-${convId}`} className="gap-2">
        <div>
          <FieldLabel htmlFor={`item-sku-${convId}`}>SKU</FieldLabel>
          <Input nativeInput id={`item-sku-${convId}`} {...f("sku")} autoComplete="off" spellCheck={false} placeholder="Optional" />
        </div>
        <div>
          <FieldLabel htmlFor={`item-notes-${convId}`}>Notes</FieldLabel>
          <Textarea
            id={`item-notes-${convId}`}
            value={details.sellerNotes}
            placeholder="Provenance, sizing quirks, or anything the description should mention"
            rows={2}
            onChange={(e) => scheduleSave({ ...details, sellerNotes: e.target.value })}
          />
        </div>
        <div>
          <FieldLabel htmlFor={`item-flaws-${convId}`}>Flaws</FieldLabel>
          <Textarea
            id={`item-flaws-${convId}`}
            size="sm"
            value={details.knownFlaws}
            placeholder="Stains, holes, or wear to mention in the description"
            rows={2}
            onChange={(e) => scheduleSave({ ...details, knownFlaws: e.target.value })}
          />
        </div>
        <div>
          <FieldLabel htmlFor={`item-labels-${convId}`}>Labels</FieldLabel>
          <div
            className="relative flex min-h-8.5 w-full cursor-text flex-wrap items-center gap-1 rounded-lg border border-input bg-background px-1.5 py-1 not-dark:bg-clip-padding text-base shadow-xs/5 ring-ring/24 transition-shadow has-focus-visible:border-ring has-focus-visible:ring-[3px] has-focus-visible:ring-inset sm:min-h-7.5 sm:text-sm dark:bg-input/32"
            onClick={() => labelInputRef.current?.focus()}
          >
            {currentLabels.map((label) => (
              <Badge key={label} variant="outline" size="lg" className="max-w-full gap-0.5 pe-0.5">
                {labelColor(uiPrefs?.label_colors, label) ? <span aria-hidden className={`me-1 size-2 shrink-0 rounded-full ${labelDotClass(labelColor(uiPrefs?.label_colors, label)!)}`} /> : null}
                <span className="truncate">{label}</span>
                <button
                  type="button"
                  className="inline-flex size-5 shrink-0 cursor-pointer items-center justify-center rounded-full text-muted-foreground hover:bg-accent hover:text-foreground"
                  aria-label={`Remove ${label} from this item`}
                  title="Remove from this item"
                  onClick={(e) => {
                    e.stopPropagation();
                    removeCommittedLabel(label);
                  }}
                >
                  <XIcon className="size-3" />
                </button>
              </Badge>
            ))}
            <input
              id={`item-labels-${convId}`}
              ref={labelInputRef}
              className="h-6 min-w-18 flex-[1_1_4.5rem] bg-transparent px-1 outline-none placeholder:text-placeholder"
              type="text"
              value={labelDraft}
              placeholder={currentLabels.length ? "Add another" : "Add labels"}
              autoComplete="off"
              role="combobox"
              aria-expanded={labelMenuOpen && labelSuggestions.length > 0}
              aria-controls={`label-history-${convId}`}
              aria-autocomplete="list"
              onChange={(e) => {
                labelDraftRef.current = e.target.value;
                setLabelDraft(e.target.value);
                setLabelMenuOpen(true);
                setActiveSuggestion(0);
              }}
              onFocus={() => { setLabelMenuOpen(true); setActiveSuggestion(0); }}
              onClick={() => { setLabelMenuOpen(true); setActiveSuggestion(0); }}
              onBlur={() => {
                setTimeout(() => {
                  setLabelMenuOpen(false);
                  commitDraft();
                }, 120);
              }}
              onKeyDown={(e) => {
                if (e.key === "Backspace" && !labelDraft && currentLabels.length) {
                  e.preventDefault();
                  removeCommittedLabel(currentLabels[currentLabels.length - 1]);
                  return;
                }
                if (e.key === "," || e.key === "Enter") {
                  if (labelDraft.trim()) {
                    e.preventDefault();
                    if (e.key === "Enter" && labelMenuOpen && labelSuggestions.length) {
                      chooseLabel(labelSuggestions[activeSuggestion] || labelSuggestions[0]);
                    } else {
                      commitDraft();
                    }
                    return;
                  }
                  if (e.key === "Enter" && labelMenuOpen && labelSuggestions.length) {
                    e.preventDefault();
                    chooseLabel(labelSuggestions[activeSuggestion] || labelSuggestions[0]);
                  }
                  return;
                }
                if (!labelMenuOpen && e.key === "ArrowDown") {
                  setLabelMenuOpen(true);
                  return;
                }
                if (!labelSuggestions.length) return;
                if (e.key === "ArrowDown") {
                  e.preventDefault();
                  setLabelMenuOpen(true);
                  setActiveSuggestion((i) => (i + 1) % labelSuggestions.length);
                } else if (e.key === "ArrowUp") {
                  e.preventDefault();
                  setActiveSuggestion((i) => (i - 1 + labelSuggestions.length) % labelSuggestions.length);
                } else if (e.key === "Escape") {
                  setLabelMenuOpen(false);
                }
              }}
            />
          </div>
          {labelMenuOpen && labelSuggestions.length > 0 && (
            <div
              className="mt-1.5 flex max-h-42 flex-col gap-0.5 overflow-y-auto rounded-lg border bg-popover p-1 shadow-xs/5"
              id={`label-history-${convId}`}
              role="listbox"
            >
              {labelSuggestions.map((label, i) => (
                <div
                  key={label}
                  className={cn("group/label flex items-center rounded-sm", i === activeSuggestion && "bg-accent")}
                  onMouseEnter={() => setActiveSuggestion(i)}
                >
                  <button
                    type="button"
                    role="option"
                    aria-selected={i === activeSuggestion}
                    className="min-w-0 flex-1 cursor-pointer truncate px-2 py-1 text-left text-sm"
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => chooseLabel(label)}
                  >
                    {label}
                  </button>
                  <Button
                    variant="ghost-muted"
                    size="sm"
                    className="me-0.5 shrink-0"
                    aria-label={`Delete "${label}" from saved labels`}
                    title="Delete saved suggestion; labels on items are kept"
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => forgetLabel(label)}
                  >
                    Delete
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>
      </InspectorSection>

      <InspectorSection title="Pricing & Shipping" titleId={`item-section-pricing-${convId}`}>
        <div className="grid grid-cols-2 gap-2">
          <div className="min-w-0">
            <FieldLabel htmlFor={`item-cog-${convId}`}>COG ($)</FieldLabel>
            <Input
              nativeInput
              id={`item-cog-${convId}`}
              {...f("cog", "number")}
              step="0.01"
              placeholder={box?.cost_per_piece != null ? box.cost_per_piece.toFixed(2) : "0.00"}
              title={box?.cost_per_piece != null ? "Blank uses the box's cost per piece" : undefined}
            />
          </div>
          <div className="min-w-0">
            <FieldLabel htmlFor={`item-package-${convId}`}>Package L×W×H</FieldLabel>
            <Input nativeInput id={`item-package-${convId}`} {...f("packageDimensions")} placeholder={defaultPackageDimensions} />
          </div>
        </div>
        {boxes.length ? (
          <div>
            <FieldLabel htmlFor={`item-box-${convId}`}>Box</FieldLabel>
            <NativeSelect
              id={`item-box-${convId}`}
              value={conv?.box_id || ""}
              onChange={(event) => void chooseBox(event.target.value)}
            >
              <option value="">Not from a box</option>
              {boxes.map((row) => (
                <option key={row.id} value={row.id}>{boxStoreName(row.store)} · {row.title}</option>
              ))}
            </NativeSelect>
          </div>
        ) : null}
      </InspectorSection>

      <InspectorSection
        title="Measurements (in)"
        titleId={`item-section-measurements-${convId}`}
        actions={
          <div className="flex w-fit gap-0.5 rounded-lg bg-input/40 p-0.5" role="radiogroup" aria-label="Garment type">
            {GARMENTS.map((g) => (
              <button
                key={g.id}
                type="button"
                role="radio"
                aria-checked={g.id === garment.id}
                data-pressed={g.id === garment.id ? "" : undefined}
                className={cn(toggleVariants({ variant: "segmented", size: "segmented" }))}
                onClick={() => {
                  if (g.id === garment.id) return;
                  if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
                  void save({ ...details, garment: g.id });
                }}
              >
                {g.label}
              </button>
            ))}
          </div>
        }
      >
        <div
          className={cn(
            "grid gap-2 @max-[460px]:grid-cols-2 @max-[240px]:grid-cols-1",
            garment.fields.length >= 4 ? "grid-cols-4" : garment.fields.length === 3 ? "grid-cols-3" : "grid-cols-2",
          )}
        >
          {garment.fields.map((field) => (
            <div className="min-w-0" key={`${garment.id}-${field.key}`}>
              <FieldLabel htmlFor={`item-measure-${convId}-${field.key}`}>{field.label}</FieldLabel>
              <Input
                nativeInput
                id={`item-measure-${convId}-${field.key}`}
                type="number"
                step="0.25"
                placeholder={field.placeholder}
                value={details.measurements[garment.id][field.key] || ""}
                onChange={(e) => setMeasurement(field.key, e.target.value)}
              />
            </div>
          ))}
        </div>
        <div>
          <FieldLabel htmlFor={`item-other-measure-${convId}`}>Other measurements</FieldLabel>
          <Input
            nativeInput
            id={`item-other-measure-${convId}`}
            {...f("descriptionMeasurements")}
            placeholder={'Anything the fields above miss, e.g. Hem 21"'}
          />
        </div>
      </InspectorSection>

      {vendooOn && history.length > 0 && (
        <InspectorSection title="Vendoo History" titleId={`item-section-history-${convId}`}>
          <dl className="flex flex-col gap-0.5">
            {history.map((row) => (
              <div className="flex items-baseline justify-between gap-2 py-0.5 text-xs" key={row.key}>
                <dt className={cn("text-muted-foreground", row.nested && "ps-2.5 opacity-80")}>{row.label}</dt>
                <dd className="flex min-w-0 items-baseline gap-1.5" title={row.iso}>
                  <span className="whitespace-nowrap text-foreground tabular-nums">{row.age}</span>
                  <span className="whitespace-nowrap text-2xs text-muted-foreground">{row.when}</span>
                </dd>
              </div>
            ))}
          </dl>
        </InspectorSection>
      )}
      {saving && (
        <span className="absolute right-0 bottom-0 flex items-center gap-1 text-2xs text-muted-foreground" role="status">
          <Spinner aria-hidden="true" role={undefined} className="size-3" />
          Saving…
        </span>
      )}
      {saveError && (
        <span className="flex items-center gap-2 text-destructive-foreground text-xs" role="alert">
          {saveError}
          <Button variant="ghost" size="xs" onClick={() => void save(details)}>
            Retry
          </Button>
        </span>
      )}
    </div>
  );
}
