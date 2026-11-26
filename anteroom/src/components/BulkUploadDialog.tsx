import { useWorkspaceRuntime } from "../app/runtime";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeftIcon, ChevronRightIcon, RotateCcwIcon, RotateCwIcon, XIcon } from "lucide-react";
import { api } from "../api/client";
import type { SourceBox } from "../api/types";
import { rotationForTurns, type BulkUploadDefaults, type BulkUploadItem, type PhotoRotation } from "../bulkPhotoUpload";
import { cn } from "../lib/utils";
import { formatMoney } from "./analyticsFormat";
import { BOXES_QUERY_KEY, boxStoreName } from "./boxPurchases";
import { GARMENTS, garmentFields, guessGarment, type Garment } from "./garmentMeasurements";
import { dragHasFiles, listingTitleForFolder, moveItem, type PhotoFolderGroup } from "../photoDrop";
import { applyReading, readEach } from "../measurementReading";
import { applySizeTagReading } from "../sizeTagReading";
import { Button } from "./ui/button";
import {
  Dialog,
  DialogClose,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogPanel,
  DialogPopup,
  DialogTitle,
} from "./ui/dialog";
import { Input } from "./ui/input";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "./ui/select";
import { toggleVariants } from "./ui/toggle";
import { StudioImage } from "./StudioImage";
import { FormField } from "./WorkspacePage";

interface Props {
  groups: PhotoFolderGroup[];
  onCancel: () => void;
  /** Create every draft; `generate` also starts generating them from their photos. */
  onConfirm: (defaults: BulkUploadDefaults, items: BulkUploadItem[], generate: boolean) => void;
}

const EMPTY_DEFAULTS: BulkUploadDefaults = { cog: "", labels: "" };

type DragSpot = { group: number; photo: number };

const READING_SIZE_NOTE = "Reading the size tag…";
const READING_MEASUREMENTS_NOTE = "Reading the yardstick…";

function createLinkedAbortController(parent?: AbortSignal): { controller: AbortController; detach: () => void } {
  const controller = new AbortController();
  if (!parent) return { controller, detach: () => {} };
  if (parent.aborted) {
    controller.abort(parent.reason);
    return { controller, detach: () => {} };
  }
  const onParentAbort = () => controller.abort(parent.reason);
  parent.addEventListener("abort", onParentAbort, { once: true });
  return { controller, detach: () => parent.removeEventListener("abort", onParentAbort) };
}

type Row = {
  sku: string;
  /** The clothing size on the tag (not the shipping package size); "0" is a size. */
  size: string;
  /** Size filled from the tag and not yet touched by the seller. */
  sizeSuggested: boolean;
  /** The tag was missing, unreadable, conflicting or disagrees with the typed size. */
  sizeReview: boolean;
  /** What the last size-tag reading found, in plain words; also progress and errors. */
  sizeNote?: string;
  /**
   * The tag evidence against a typed size ("The tag reads M…"), kept apart from progress
   * and errors so a failed or empty re-read cannot replace it. Editing the size clears it.
   */
  sizeWarning?: string;
  /** Bumped when a photo is removed, so a reading of the earlier photos is not applied. */
  photoSet: number;
  garment: Garment;
  measurements: Record<string, string>;
  /** Fields filled from the photos and not yet touched by the seller. */
  suggested: string[];
  /** What the last photo reading found, in plain words. */
  readNote?: string;
};

/**
 * Everything a bulk upload needs in one window, before anything is created:
 * photo order, the box, cost of goods and labels every draft shares, and each
 * item's own size, SKU and measurements. Click a photo to enlarge or turn it.
 * Outside clicks are ignored so a stray click cannot throw away what was typed;
 * Escape, the close button and Cancel close it cleanly.
 */
/** The chosen box as the seller reads it. */
function boxLabel(boxes: readonly SourceBox[], boxId: string | undefined): string {
  const row = boxId ? boxes.find((box) => box.id === boxId) : undefined;
  return row ? `${boxStoreName(row.store)} · ${row.title}` : "Not from a box";
}

export function BulkUploadDialog({ groups: initialGroups, onCancel, onConfirm }: Props) {
  const runtime = useWorkspaceRuntime();
  const [defaults, setDefaults] = useState<BulkUploadDefaults>(EMPTY_DEFAULTS);
  const boxes = useQuery({ queryKey: BOXES_QUERY_KEY, queryFn: api.boxes.list }).data?.boxes ?? [];
  const box = boxes.find((row) => row.id === defaults.boxId);
  const [groups, setGroups] = useState(initialGroups);
  const [rows, setRows] = useState<Row[]>(() => initialGroups.map((group) => ({
    sku: "",
    size: "",
    sizeSuggested: false,
    sizeReview: false,
    photoSet: 0,
    garment: guessGarment(listingTitleForFolder(group.folder)),
    measurements: {},
    suggested: [],
  })));
  const [reading, setReading] = useState<{ done: number; total: number } | null>(null);
  const [sizeReading, setSizeReading] = useState<{ done: number; total: number } | null>(null);
  // One announcement when a size-tag read finishes, not one per item; editing a size ends it.
  const [announceSizes, setAnnounceSizes] = useState(false);
  const [dragFrom, setDragFrom] = useState<DragSpot | null>(null);
  const [dropOn, setDropOn] = useState<DragSpot | null>(null);
  const [zoom, setZoom] = useState<DragSpot | null>(null);
  // Quarter turns clockwise per file; the files are not saved yet, so the server turns them after upload.
  const [turns, setTurns] = useState<Map<File, number>>(() => new Map());
  // One preview URL per picked file; reordering only shuffles which file sits where.
  const [previewUrls, setPreviewUrls] = useState<Map<File, string>>(() => new Map());
  const formRef = useRef<HTMLFormElement>(null);
  const cogRef = useRef<HTMLInputElement>(null);
  const readAbortRef = useRef<AbortController | null>(null);
  const photoSetRef = useRef<number[]>(initialGroups.map(() => 0));
  const measureAbortsRef = useRef<Map<number, AbortController>>(new Map());
  const sizeAbortsRef = useRef<Map<number, AbortController>>(new Map());
  const count = groups.length;

  // Created and revoked in the same effect so StrictMode's remount gets fresh URLs.
  useEffect(() => {
    const urls = new Map(initialGroups.flatMap((group) => group.files).map((file) => [file, URL.createObjectURL(file)]));
    setPreviewUrls(urls);
    return () => urls.forEach((url) => URL.revokeObjectURL(url));
  }, [initialGroups]);

  // Abort in-flight bulk photo reads (and any Retry-After waits) when the dialog closes.
  useEffect(() => {
    const controller = new AbortController();
    readAbortRef.current = controller;
    const measureAborts = measureAbortsRef.current;
    const sizeAborts = sizeAbortsRef.current;
    return () => {
      controller.abort();
      measureAborts.forEach((rowController) => rowController.abort());
      measureAborts.clear();
      sizeAborts.forEach((rowController) => rowController.abort());
      sizeAborts.clear();
      if (readAbortRef.current === controller) readAbortRef.current = null;
    };
  }, []);

  const resetDrag = () => {
    setDragFrom(null);
    setDropOn(null);
  };

  // Photos only move within their own listing; the first one becomes the cover.
  // Order affects which yardstick shots the server inspects, so reordering cancels
  // any queued or in-flight reading captured against the previous order.
  const dropPhoto = (target: DragSpot) => {
    const source = dragFrom;
    resetDrag();
    if (!source || source.group !== target.group || source.photo === target.photo) return;
    photoSetRef.current[target.group] = (photoSetRef.current[target.group] ?? 0) + 1;
    measureAbortsRef.current.get(target.group)?.abort();
    measureAbortsRef.current.delete(target.group);
    sizeAbortsRef.current.get(target.group)?.abort();
    sizeAbortsRef.current.delete(target.group);
    setGroups((current) => current.map((group, index) => (
      index === target.group ? { ...group, files: moveItem(group.files, source.photo, target.photo) } : group
    )));
    setRows((current) => current.map((row, index) => {
      if (index !== target.group) return row;
      const clearedMeasure = row.readNote === READING_MEASUREMENTS_NOTE
        ? { readNote: "Photos were reordered while reading. Read measurements again or measure by hand." }
        : {};
      const sizeCancellation = "Photos were reordered while reading. Read size tags again or type the size.";
      const clearedSize = row.sizeNote === READING_SIZE_NOTE
        ? {
            sizeReview: row.size.trim() === "" || Boolean(row.sizeWarning),
            sizeNote: row.sizeWarning ? `${sizeCancellation} ${row.sizeWarning}` : sizeCancellation,
          }
        : {};
      return { ...row, ...clearedMeasure, ...clearedSize, photoSet: row.photoSet + 1 };
    }));
  };

  const setRow = (index: number, patch: Partial<Row>) => setRows((current) => current.map((row, i) => (
    i === index ? { ...row, ...patch } : row
  )));

  // Fills blank measurement fields from each item's yardstick photos; typed values stay.
  const readMeasurements = async () => {
    const dialogSignal = readAbortRef.current?.signal;
    const todo = groups.map((_, index) => index).filter((index) => groups[index]!.files.length);
    setReading({ done: 0, total: todo.length });
    await readEach(todo, async (index) => {
      const { garment, photoSet } = rows[index]!;
      if ((photoSetRef.current[index] ?? 0) !== photoSet) {
        if (!dialogSignal?.aborted) setReading((current) => current && { ...current, done: current.done + 1 });
        return;
      }
      const { controller, detach } = createLinkedAbortController(dialogSignal);
      measureAbortsRef.current.get(index)?.abort();
      measureAbortsRef.current.set(index, controller);
      setRow(index, { readNote: READING_MEASUREMENTS_NOTE });
      try {
        const result = await api.measurements.read(groups[index]!.files, garment, controller.signal);
        if (controller.signal.aborted || dialogSignal?.aborted) return;
        setRows((current) => current.map((row, i) => {
          if (i !== index || row.garment !== garment || row.photoSet !== photoSet) return row;
          const outcome = applyReading(garment, row.measurements, result);
          return {
            ...row,
            measurements: outcome.measurements,
            suggested: [...new Set([...row.suggested, ...outcome.suggested])],
            readNote: outcome.note,
          };
        }));
      } catch (error) {
        if (controller.signal.aborted || dialogSignal?.aborted) return;
        const message = error instanceof Error ? error.message : "Reading failed.";
        setRows((current) => current.map((row, i) => (
          i !== index || row.garment !== garment || row.photoSet !== photoSet
            ? row
            : { ...row, readNote: `${message} Your entered details are kept. You can enter measurements manually or try reading again.` }
        )));
      } finally {
        detach();
        if (measureAbortsRef.current.get(index) === controller) measureAbortsRef.current.delete(index);
        if (!dialogSignal?.aborted) setReading((current) => current && { ...current, done: current.done + 1 });
      }
    }, dialogSignal);
    if (!dialogSignal?.aborted) setReading(null);
  };

  // Fills a blank Size from each item's size tag; a typed size stays. A missing,
  // unreadable or conflicting tag is left for the seller, never guessed.
  const readSizeTags = async () => {
    const dialogSignal = readAbortRef.current?.signal;
    const todo = groups.map((_, index) => index).filter((index) => groups[index]!.files.length);
    setSizeReading({ done: 0, total: todo.length });
    setAnnounceSizes(false);
    await readEach(todo, async (index) => {
      const { photoSet } = rows[index]!;
      if ((photoSetRef.current[index] ?? 0) !== photoSet) {
        if (!dialogSignal?.aborted) setSizeReading((current) => current && { ...current, done: current.done + 1 });
        return;
      }
      const { controller, detach } = createLinkedAbortController(dialogSignal);
      sizeAbortsRef.current.get(index)?.abort();
      sizeAbortsRef.current.set(index, controller);
      setRow(index, { sizeNote: READING_SIZE_NOTE });
      try {
        const result = await api.sizeTags.read(groups[index]!.files, controller.signal);
        if (controller.signal.aborted || dialogSignal?.aborted) return;
        setRows((current) => current.map((row, i) => {
          if (i !== index || row.photoSet !== photoSet) return row;
          // A size an earlier reading filled is replaced, not treated as typed.
          const outcome = applySizeTagReading(row.sizeSuggested ? "" : row.size, result);
          // A re-read that saw nothing new (no tag, unreadable) keeps an earlier warning
          // on a typed size: the earlier evidence still stands. Editing the size clears it.
          if (!outcome.suggested && !outcome.needsReview && !outcome.note && row.sizeWarning) {
            return { ...row, sizeReview: true, sizeNote: row.sizeWarning };
          }
          const typed = !outcome.suggested && outcome.size.trim() !== "";
          return {
            ...row,
            size: outcome.size,
            sizeSuggested: outcome.suggested,
            sizeReview: outcome.needsReview,
            sizeNote: outcome.note || undefined,
            sizeWarning: typed && outcome.needsReview ? outcome.note : undefined,
          };
        }));
      } catch (error) {
        if (controller.signal.aborted || dialogSignal?.aborted) return;
        const message = error instanceof Error ? error.message : "Reading failed.";
        setRows((current) => current.map((row, i) => {
          if (i !== index || row.photoSet !== photoSet) return row;
          const blank = row.size.trim() === "";
          // A size an earlier reading filled was not confirmed by this one, so it is checked too.
          if (blank || row.sizeSuggested) {
            return { ...row, sizeReview: true, sizeNote: `${message} ${blank ? "Type the size from the tag." : "Check the size against the tag."}` };
          }
          // The error is shown without dropping the tag evidence against the typed size.
          return { ...row, sizeNote: row.sizeWarning ? `${message} ${row.sizeWarning}` : message };
        }));
      } finally {
        detach();
        if (sizeAbortsRef.current.get(index) === controller) sizeAbortsRef.current.delete(index);
        if (!dialogSignal?.aborted) setSizeReading((current) => current && { ...current, done: current.done + 1 });
      }
    }, dialogSignal);
    if (!dialogSignal?.aborted) {
      setSizeReading(null);
      setAnnounceSizes(true);
    }
  };

  const rotate = (file: File, degrees: 90 | -90) => setTurns((current) => (
    new Map(current).set(file, ((current.get(file) || 0) + degrees / 90 + 4) % 4)
  ));

  // A listing keeps at least one photo; there is nothing to generate from otherwise.
  // A size read from the photos may have come from the one removed: it is cleared for the seller
  // to read again or type, and any queued or running reading of the earlier photos is cancelled.
  const removePhoto = (spot: DragSpot) => {
    photoSetRef.current[spot.group] = (photoSetRef.current[spot.group] ?? 0) + 1;
    sizeAbortsRef.current.get(spot.group)?.abort();
    sizeAbortsRef.current.delete(spot.group);
    measureAbortsRef.current.get(spot.group)?.abort();
    measureAbortsRef.current.delete(spot.group);
    setGroups((current) => current.map((group, index) => (
      index === spot.group ? { ...group, files: group.files.filter((_, photo) => photo !== spot.photo) } : group
    )));
    setRows((current) => current.map((row, index) => {
      if (index !== spot.group) return row;
      const cleared = row.sizeSuggested
        ? { size: "", sizeSuggested: false, sizeReview: true,
          sizeNote: "A photo was removed, so the size read from it was cleared. Read size tags again or type the size." }
        : row.sizeWarning
          // The tag evidence against the typed size may have come from the removed photo, and
          // nothing records which photo it was: it no longer stands, so an empty re-read can't
          // restore it. The size stays flagged until it is read again or checked.
          ? { sizeWarning: undefined, sizeReview: true,
            sizeNote: "A photo was removed, so the earlier tag check no longer applies. Read size tags again or check the tag." }
          : row.sizeNote === READING_SIZE_NOTE
            // Its reading is of the earlier photos and was cancelled; say so instead of "Reading…".
            ? { sizeReview: row.size.trim() === "",
              sizeNote: "A photo was removed while reading. Read size tags again or type the size." }
            : {};
      const clearedMeasure = row.readNote === READING_MEASUREMENTS_NOTE
        ? { readNote: "A photo was removed while reading. Read measurements again or measure by hand." }
        : {};
      return { ...row, ...cleared, ...clearedMeasure, photoSet: row.photoSet + 1 };
    }));
  };

  const cogNumber = defaults.cog.trim() ? Number(defaults.cog) : null;
  const invalidCog = cogNumber !== null && (!Number.isFinite(cogNumber) || cogNumber < 0);

  const confirm = (generate: boolean) => {
    if (invalidCog) return;
    onConfirm(defaults, groups.map((group, index) => {
      const rotations = new Map<File, PhotoRotation>();
      for (const file of group.files) {
        const degrees = rotationForTurns(turns.get(file) || 0);
        if (degrees) rotations.set(file, degrees);
      }
      const { sku, size, garment, measurements } = rows[index]!;
      return { ...group, sku, size, garment, measurements, rotations };
    }), generate && runtime.features.has("generation"));
  };

  // Enter moves on like a spreadsheet instead of submitting half a box.
  const onInputKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const inputs = Array.from(formRef.current?.querySelectorAll<HTMLInputElement>("input") || []);
    inputs[inputs.indexOf(event.currentTarget) + 1]?.focus();
  };

  const zoomFiles = zoom ? groups[zoom.group]!.files : [];

  return (
    <Dialog
      open
      disablePointerDismissal
      onOpenChange={(open) => {
        if (!open && !zoom) onCancel();
      }}
    >
      <DialogPopup showCloseButton={false} initialFocus={cogRef} className="max-w-3xl">
        <form
          ref={formRef}
          className="flex min-h-0 flex-1 flex-col"
          onSubmit={(event) => {
            event.preventDefault();
            confirm(true);
          }}
        >
          <DialogClose
            aria-label="Cancel bulk upload"
            className="absolute end-2 top-2"
            render={<Button size="icon" variant="ghost" />}
          >
            <XIcon />
          </DialogClose>
          <DialogHeader className="pe-12 max-sm:px-4 max-sm:pt-4 max-sm:pb-2">
            <DialogTitle>Set details for {count} listings</DialogTitle>
            <DialogDescription>
              Box, cost of goods and labels go on every draft. Add each item's size, SKU and measurements
              below; check anything read from photos before creating.
            </DialogDescription>
          </DialogHeader>
          <DialogPanel className="space-y-4 max-sm:px-4">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-[repeat(auto-fit,minmax(10rem,1fr))]">
              {boxes.length ? (
                <FormField label="Box">
                  <Select
                    value={defaults.boxId || ""}
                    onValueChange={(value) => setDefaults((current) => ({ ...current, boxId: value || undefined }))}
                  >
                    <SelectTrigger aria-label="Box">
                      <SelectValue>{boxLabel(boxes, defaults.boxId)}</SelectValue>
                    </SelectTrigger>
                    <SelectPopup>
                      <SelectItem value="">Not from a box</SelectItem>
                      {boxes.map((row) => (
                        <SelectItem key={row.id} value={row.id}>{boxStoreName(row.store)} · {row.title}</SelectItem>
                      ))}
                    </SelectPopup>
                  </Select>
                </FormField>
              ) : null}
              <FormField label="Cost of goods">
                <Input
                  ref={cogRef}
                  type="number"
                  min="0"
                  step="0.01"
                  inputMode="decimal"
                  aria-invalid={invalidCog || undefined}
                  placeholder={box?.cost_per_piece != null ? box.cost_per_piece.toFixed(2) : "0.00"}
                  value={defaults.cog}
                  onChange={(event) => setDefaults((current) => ({ ...current, cog: event.target.value }))}
                  onKeyDown={onInputKeyDown}
                />
                {box?.cost_per_piece != null ? (
                  <span className="font-normal text-2xs text-muted-foreground">
                    Leave blank to use the box's {formatMoney(box.cost_per_piece)} a piece.
                  </span>
                ) : null}
              </FormField>
              <FormField label="Labels">
                <Input
                  type="text"
                  placeholder="To List, Bin 4"
                  value={defaults.labels}
                  onChange={(event) => setDefaults((current) => ({ ...current, labels: event.target.value }))}
                  onKeyDown={onInputKeyDown}
                />
                <span className="font-normal text-2xs text-muted-foreground">
                  Separate multiple labels with commas.
                </span>
              </FormField>
            </div>
            <ul
              className="m-0 flex list-none flex-col gap-2.5 p-0"
              aria-label="Each listing's photos, size, SKU and measurements"
            >
              {groups.map((group, groupIndex) => {
                const title = listingTitleForFolder(group.folder);
                const row = rows[groupIndex]!;
                return (
                  <li
                    key={group.folder ?? groupIndex}
                    className="bulk-upload-group flex min-w-0 flex-col gap-2.5 rounded-xl border border-border/60 bg-card/40 p-3"
                  >
                    <div className="flex flex-wrap items-end justify-between gap-2.5 sm:flex-nowrap">
                      <span
                        className="min-w-0 flex-1 basis-full truncate font-medium text-foreground text-sm sm:basis-0"
                        title={title}
                      >
                        {title}
                        <span className="font-normal text-2xs text-muted-foreground"> · {group.files.length} photos</span>
                      </span>
                      <FormField label="Size" className="min-w-[7.5rem] flex-1 sm:w-32 sm:min-w-0 sm:flex-none">
                        <Input
                          size="sm"
                          className={cn(
                            row.sizeSuggested && "border-dashed border-info",
                            row.sizeReview && "border-warning",
                          )}
                          inputClassName={cn(
                            row.sizeSuggested && "is-suggested",
                            row.sizeReview && "needs-review",
                          )}
                          type="text"
                          autoComplete="off"
                          placeholder="From the tag"
                          aria-label={`Size for ${title}`}
                          aria-describedby={row.sizeNote ? `bulk-size-note-${groupIndex}` : undefined}
                          title={row.sizeSuggested ? "Read from the size tag. Check it before creating." : undefined}
                          value={row.size}
                          onChange={(event) => {
                            setAnnounceSizes(false);
                            // An edit settles the old warning; a running read's note stays so a photo removal can still void it.
                            setRow(groupIndex, {
                              size: event.target.value, sizeSuggested: false, sizeReview: false, sizeWarning: undefined,
                              ...(row.sizeNote === READING_SIZE_NOTE ? {} : { sizeNote: undefined }),
                            });
                          }}
                          onKeyDown={onInputKeyDown}
                        />
                      </FormField>
                      <FormField label="SKU" className="min-w-[7.5rem] flex-1 sm:w-32 sm:min-w-0 sm:flex-none">
                        <Input
                          size="sm"
                          font="mono"
                          type="text"
                          autoComplete="off"
                          spellCheck={false}
                          aria-label={`SKU for ${title}`}
                          value={row.sku}
                          onChange={(event) => setRow(groupIndex, { sku: event.target.value })}
                          onKeyDown={onInputKeyDown}
                        />
                      </FormField>
                      <div
                        className="flex w-fit shrink-0 gap-0.5 rounded-lg bg-input/40 p-0.5"
                        role="radiogroup"
                        aria-label={`Garment type for ${title}`}
                      >
                        {GARMENTS.map((g) => (
                          <button
                            key={g.id}
                            type="button"
                            role="radio"
                            tabIndex={-1}
                            aria-checked={g.id === row.garment}
                            data-pressed={g.id === row.garment ? "" : undefined}
                            className={cn(toggleVariants({ variant: "segmented", size: "segmented" }))}
                            onClick={() => {
                              if (g.id === row.garment) return;
                              measureAbortsRef.current.get(groupIndex)?.abort();
                              measureAbortsRef.current.delete(groupIndex);
                              setRow(groupIndex, {
                                garment: g.id,
                                ...(row.readNote === READING_MEASUREMENTS_NOTE ? { readNote: undefined } : {}),
                              });
                            }}
                          >
                            {g.label}
                          </button>
                        ))}
                      </div>
                    </div>
                    <div className="photo-strip">
                      {group.files.map((file, photoIndex) => {
                        const spot = { group: groupIndex, photo: photoIndex };
                        const canReorder = group.files.length > 1;
                        const isDragging = dragFrom?.group === groupIndex && dragFrom.photo === photoIndex;
                        const isDropTarget = dropOn?.group === groupIndex && dropOn.photo === photoIndex;
                        return (
                          <div
                            key={`${file.name}-${file.lastModified}-${file.size}`}
                            className={[
                              "photo-thumb",
                              canReorder ? "photo-thumb-reorderable" : "",
                              isDragging ? "is-dragging" : "",
                              isDropTarget ? "is-drop-target" : "",
                            ].filter(Boolean).join(" ")}
                            draggable={canReorder}
                            title={canReorder ? `${file.name} · drag to reorder, click to enlarge` : file.name}
                            onClick={() => setZoom(spot)}
                            onDragStart={(event) => {
                              event.dataTransfer.effectAllowed = "move";
                              event.dataTransfer.setData("text/plain", file.name);
                              setDragFrom(spot);
                            }}
                            onDragOver={(event) => {
                              if (!dragFrom || dragFrom.group !== groupIndex || isDragging) return;
                              event.preventDefault();
                              event.stopPropagation();
                              event.dataTransfer.dropEffect = "move";
                              if (!isDropTarget) setDropOn(spot);
                            }}
                            onDrop={(event) => {
                              if (dragHasFiles(event.dataTransfer)) return;
                              event.preventDefault();
                              event.stopPropagation();
                              dropPhoto(spot);
                            }}
                            onDragEnd={resetDrag}
                          >
                            {previewUrls.has(file) ? (
                              <img
                                src={previewUrls.get(file)}
                                alt=""
                                loading="lazy"
                                decoding="async"
                                draggable={false}
                                style={turns.get(file) ? { transform: `rotate(${turns.get(file)! * 90}deg)` } : undefined}
                              />
                            ) : null}
                            <div className="photo-thumb-num">{String(photoIndex + 1).padStart(2, "0")}</div>
                            <button
                              type="button"
                              tabIndex={-1}
                              className="photo-thumb-rotate"
                              aria-label={`Rotate photo ${photoIndex + 1} right`}
                              title="Rotate right"
                              onClick={(event) => {
                                event.stopPropagation();
                                rotate(file, 90);
                              }}
                            >
                              <RotateCwIcon className="size-2.5" aria-hidden="true" />
                            </button>
                            {canReorder ? (
                              <button
                                type="button"
                                tabIndex={-1}
                                className="photo-thumb-delete"
                                aria-label={`Remove photo ${photoIndex + 1}`}
                                title="Remove photo"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  removePhoto(spot);
                                }}
                              >
                                <XIcon className="size-2.5" aria-hidden="true" />
                              </button>
                            ) : null}
                          </div>
                        );
                      })}
                    </div>
                    <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
                      {garmentFields(row.garment).map((field) => {
                        const isSuggested = row.suggested.includes(field.key);
                        return (
                          <FormField label={field.label} key={`${row.garment}-${field.key}`}>
                            <Input
                              size="sm"
                              className={cn(isSuggested && "border-dashed border-info")}
                              inputClassName={isSuggested ? "is-suggested" : undefined}
                              type="number"
                              step="0.25"
                              min="0"
                              inputMode="decimal"
                              title={isSuggested ? "Read from the photos. Check it before creating." : undefined}
                              value={row.measurements[field.key] || ""}
                              onChange={(event) => setRow(groupIndex, {
                                measurements: { ...row.measurements, [field.key]: event.target.value },
                                suggested: row.suggested.filter((key) => key !== field.key),
                              })}
                              onKeyDown={onInputKeyDown}
                            />
                          </FormField>
                        );
                      })}
                    </div>
                    {row.sizeNote ? (
                      <p
                        id={`bulk-size-note-${groupIndex}`}
                        className={cn(
                          "m-0 text-2xs leading-snug",
                          row.sizeReview ? "text-warning-foreground" : "text-muted-foreground",
                        )}
                      >
                        {row.sizeNote}
                      </p>
                    ) : null}
                    {row.readNote ? (
                      <p className="m-0 text-2xs leading-snug text-muted-foreground">{row.readNote}</p>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </DialogPanel>
          <p className="sr-only" role="status">
            {announceSizes
              ? `Size tags read. ${rows.filter((row) => row.sizeSuggested).length} filled, ${rows.filter((row) => row.sizeReview).length} to check.`
              : ""}
          </p>
          <DialogFooter className="grid grid-cols-2 gap-2 max-sm:px-4 max-sm:py-3 sm:flex sm:flex-row sm:flex-wrap sm:items-center sm:justify-end">
            <Button type="button" variant="ghost" className="max-sm:order-4 sm:me-auto" onClick={onCancel}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="outline"
              className="max-sm:order-2"
              disabled={sizeReading !== null || !runtime.features.has("generation")}
              title="Fill blank sizes from the size tag in each item's photos"
              onClick={() => void readSizeTags()}
            >
              {sizeReading ? `Reading tags ${sizeReading.done} of ${sizeReading.total}…` : "Read size tags"}
            </Button>
            <Button
              type="button"
              variant="outline"
              className="max-sm:order-3"
              disabled={reading !== null || !runtime.features.has("generation")}
              title="Fill blank measurements from photos with a yardstick on the item"
              onClick={() => void readMeasurements()}
            >
              {reading ? `Reading ${reading.done} of ${reading.total}…` : "Read measurements"}
            </Button>
            <Button
              type="button"
              variant="outline"
              className={cn(
                "max-sm:order-5",
                !runtime.features.has("generation") && "max-sm:col-span-2 max-sm:order-1",
              )}
              disabled={invalidCog || reading !== null || sizeReading !== null}
              onClick={() => confirm(false)}
            >
              Create drafts only
            </Button>
            {runtime.features.has("generation") && (
              <Button
                type="submit"
                className="max-sm:col-span-2 max-sm:order-1"
                disabled={invalidCog || reading !== null || sizeReading !== null}
              >
                Create and generate {count}
              </Button>
            )}
          </DialogFooter>
        </form>
      </DialogPopup>
      {zoom ? (
        <PhotoZoom
          urls={zoomFiles.map((file) => previewUrls.get(file) || "")}
          index={zoom.photo}
          turns={turns.get(zoomFiles[zoom.photo]!) || 0}
          onRotate={(degrees) => rotate(zoomFiles[zoom.photo]!, degrees)}
          onStep={(photo) => setZoom({ ...zoom, photo })}
          onClose={() => setZoom(null)}
        />
      ) : null}
    </Dialog>
  );
}

export function PhotoZoom({
  urls,
  index,
  turns = 0,
  rotating = false,
  onRotate,
  onStep,
  onClose,
}: {
  urls: string[];
  index: number;
  /** Quarter turns to show a photo by when the turn is not in the file yet. */
  turns?: number;
  rotating?: boolean;
  onRotate: (degrees: 90 | -90) => void;
  onStep: (index: number) => void;
  onClose: () => void;
}) {
  const step = (delta: number) => onStep((index + delta + urls.length) % urls.length);

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogPopup
        variant="media"
        showCloseButton={false}
        bottomStickOnMobile={false}
        className="row-start-1 h-full max-h-[92vh] w-[92vw] max-w-[92vw] items-center justify-center overflow-visible"
        aria-label={`Photo ${index + 1} of ${urls.length}`}
        onKeyDown={(event) => {
          if (urls.length < 2) return;
          if (event.key === "ArrowLeft") {
            event.preventDefault();
            step(-1);
          }
          if (event.key === "ArrowRight") {
            event.preventDefault();
            step(1);
          }
        }}
        onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}
      >
        <DialogTitle className="sr-only">Photo {index + 1} of {urls.length}</DialogTitle>
        <Button
          size="icon-sm"
          variant="media-close"
          className="absolute top-0 right-0 z-20"
          aria-label="Close"
          onClick={onClose}
        >
          <XIcon />
        </Button>
        {urls.length > 1 && (
          <Button
            size="icon"
            variant="media-navigation"
            className="left-0"
            aria-label="Previous photo"
            onClick={() => step(-1)}
          >
            <ChevronLeftIcon className="size-5" />
          </Button>
        )}
        <div className="relative isolate z-10 flex max-h-full max-w-[calc(92vw-96px)] flex-col items-center">
          <StudioImage
            className={cn(
              "photo-lightbox-image max-h-[80vh] w-auto max-w-full rounded-lg border border-border/70 object-contain shadow-2xl transition-transform",
              turns % 2 ? "is-quarter-turned" : undefined,
            )}
            style={turns ? { transform: `rotate(${turns * 90}deg)` } : undefined}
            src={urls[index]}
            alt={`Photo ${index + 1}`}
            onClick={onClose}
          />
          <div className="mt-2 flex items-center gap-1.5 text-white/80 text-xs">
            <Button
              variant="overlay"
              size="icon-micro"
              onClick={() => onRotate(-90)}
              disabled={rotating}
              aria-label="Rotate left"
              title="Rotate left"
            >
              <RotateCcwIcon />
            </Button>
            <span className="font-mono tabular-nums" aria-live="polite">
              {String(index + 1).padStart(2, "0")} / {String(urls.length).padStart(2, "0")}
            </span>
            <Button
              variant="overlay"
              size="icon-micro"
              onClick={() => onRotate(90)}
              disabled={rotating}
              aria-label="Rotate right"
              title="Rotate right"
            >
              <RotateCwIcon />
            </Button>
          </div>
        </div>
        {urls.length > 1 && (
          <Button
            size="icon"
            variant="media-navigation"
            className="right-0"
            aria-label="Next photo"
            onClick={() => step(1)}
          >
            <ChevronRightIcon className="size-5" />
          </Button>
        )}
      </DialogPopup>
    </Dialog>
  );
}
