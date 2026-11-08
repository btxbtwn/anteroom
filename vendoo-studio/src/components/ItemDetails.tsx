import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { labelColor, labelDotClass } from "../labelColors";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { addLabel, removeLabel, splitLabels } from "./itemLabels";
import { BOXES_QUERY_KEY, boxStoreName } from "./boxPurchases";
import { GARMENTS, type Garment } from "./garmentMeasurements";
import { applyReading, readingSuggestion } from "../measurementReading";
import { useWorkspaceRuntime } from "../app/runtime";
import type { MeasurementReadResult } from "../api/types";
import { DEFAULTS, applyDetailsPatch, changedDetails, parseNotes, type ItemDetailsData } from "./itemDetailsData";
import { itemDetailsEdits } from "../api/itemDetailsEdits";
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

type TextKey = Exclude<keyof ItemDetailsData, "garment" | "measurements">;

/** One Read measurements run; its result waits for the seller to fill it in. */
interface Reading {
  convId: string;
  garment: Garment;
  result?: MeasurementReadResult;
  error?: string;
}

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

export function ItemDetails({ convId }: Props) {
  const queryClient = useQueryClient();
  const runtime = useWorkspaceRuntime();
  const edits = useMemo(() => itemDetailsEdits(queryClient, convId), [queryClient, convId]);
  const editState = React.useSyncExternalStore(edits.subscribe, edits.getSnapshot, edits.getSnapshot);
  const saving = editState.saving;
  const [saveError, setSaveError] = useState<string | null>(null);
  const [labelMenuOpen, setLabelMenuOpen] = useState(false);
  const [activeSuggestion, setActiveSuggestion] = useState(0);
  const [labelDraft, setLabelDraft] = useState("");
  const labelDraftRef = useRef("");
  const labelInputRef = useRef<HTMLInputElement | null>(null);
  const migratedLabelsRef = useRef(false);

  const { data: conv } = useQuery({
    queryKey: ["conversation", convId],
    queryFn: ({ signal }) => api.conversations.get(convId, { signal }),
  });

  const { data: conversations } = useQuery({
    queryKey: ["conversations"],
    queryFn: ({ signal }) => api.conversations.list({ signal }),
  });

  const { data: uiPrefs } = useQuery({
    queryKey: ["settings-ui"],
    queryFn: api.settings.ui,
  });

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

  const details = useMemo(() => applyDetailsPatch(parseNotes(conv?.notes ?? null, defaultPackageDimensions), editState.patch),
    [conv?.notes, defaultPackageDimensions, editState.patch]);
  const detailsRef = useRef<ItemDetailsData>(details);
  useEffect(() => { detailsRef.current = details; }, [details]);

  useEffect(() => {
    setLabelMenuOpen(false);
    setActiveSuggestion(0);
    labelDraftRef.current = "";
    setLabelDraft("");
  }, [convId]);

  const save = useCallback((updated: ItemDetailsData) => {
    const patch = changedDetails(detailsRef.current, updated);
    detailsRef.current = updated;
    edits.change(patch, true);
  }, [edits]);

  const scheduleSave = useCallback((updated: ItemDetailsData) => {
    const patch = changedDetails(detailsRef.current, updated);
    detailsRef.current = updated;
    edits.change(patch);
  }, [edits]);

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

  // Reads the yardstick in this listing's photos. Nothing is saved until the seller fills it in.
  const [reading, setReading] = useState<Reading | null>(null);
  const currentReading = reading?.convId === convId && reading.garment === garment.id ? reading : null;
  const readingOutcome = currentReading?.result
    ? applyReading(garment.id, details.measurements[garment.id], currentReading.result)
    : null;
  const readMeasurements = async () => {
    const target: Reading = { convId, garment: garment.id };
    setReading(target);
    const settle = (patch: Partial<Reading>) => setReading((current) => (
      current?.convId === target.convId && current.garment === target.garment ? { ...current, ...patch } : current
    ));
    try {
      settle({ result: await api.measurements.readSaved(convId, garment.id) });
    } catch (err) {
      settle({ error: err instanceof Error ? err.message : "Reading failed." });
    }
  };
  const fillReading = () => {
    if (!readingOutcome) return;
    void save({ ...details, measurements: { ...details.measurements, [garment.id]: readingOutcome.measurements } });
    setReading(null);
  };

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
            {box?.cost_per_piece != null && (
              <p className="mt-1 text-xs text-muted-foreground">A blank Cost uses this box's ${box.cost_per_piece.toFixed(2)} per piece.</p>
            )}
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
        {runtime.features.has("generation") && (
          <div className="flex min-w-0 flex-col gap-2">
            {currentReading && !currentReading.result && !currentReading.error ? (
              <Button variant="outline" size="sm" className="w-fit" disabled>
                <Spinner aria-hidden="true" role={undefined} className="size-3.5" />
                Reading the yardstick…
              </Button>
            ) : (
              <Button
                variant="outline"
                size="sm"
                className="w-fit"
                title="Fill blank measurements from photos with a yardstick on the item"
                onClick={() => void readMeasurements()}
              >
                Read measurements
              </Button>
            )}
            {currentReading?.error ? (
              <p className="text-destructive-foreground text-xs" role="alert">{currentReading.error}</p>
            ) : null}
            {readingOutcome ? (
              <div className="flex min-w-0 flex-col gap-2 rounded-lg border bg-muted/40 p-2.5" role="status">
                <p className="text-xs">{readingSuggestion(garment.id, readingOutcome)}</p>
                <div className="flex gap-1">
                  {readingOutcome.suggested.length ? (
                    <Button size="xs" onClick={fillReading}>Fill in</Button>
                  ) : null}
                  <Button variant="ghost" size="xs" onClick={() => setReading(null)}>Dismiss</Button>
                </div>
              </div>
            ) : null}
          </div>
        )}
      </InspectorSection>

      {saving && (
        <span className="absolute right-0 bottom-0 flex items-center gap-1 text-2xs text-muted-foreground" role="status">
          <Spinner aria-hidden="true" role={undefined} className="size-3" />
          Saving…
        </span>
      )}
      {(saveError || editState.error) && (
        <span className="flex items-center gap-2 text-destructive-foreground text-xs" role="alert">
          {saveError || editState.error}
          {editState.error && <Button variant="ghost" size="xs" onClick={edits.retry}>
            Retry
          </Button>}
        </span>
      )}
    </div>
  );
}
