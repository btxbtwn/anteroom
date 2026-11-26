import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCwIcon, XIcon } from "lucide-react";
import { api } from "../api/client";
import { BULK_READ_CONCURRENCY, mapWithConcurrency } from "../lib/concurrency";
import { cn } from "../lib/utils";
import type { ListingPhoto } from "../api/types";
import { moveItem } from "../photoDrop";
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
import { toastManager } from "./ui/toast";
import { toggleVariants } from "./ui/toggle";
import { PhotoZoom } from "./BulkUploadDialog";
import { photoThumbUrl } from "../photoThumb";
import { StudioImage } from "./StudioImage";
import { FormError, FormField } from "./WorkspacePage";
import {
  GARMENTS,
  garmentFields,
  guessGarment,
  notesWithItemDetails,
  parseItemDetails,
  type ItemDetails,
} from "./garmentMeasurements";

export interface RegenerateReviewListing {
  id: string;
  title: string;
}

interface Props {
  listings: RegenerateReviewListing[];
  onClose: () => void;
  /** Called once any edits are saved; the rewrite starts from here. */
  onRewrite: () => void;
}

const READING_SIZE_NOTE = "Reading the size tag…";
type GuardPhotoRemoval = (remove: () => Promise<void>) => Promise<void>;

interface RowState {
  details: ItemDetails;
  /** Measurement fields filled from the photos and not yet touched by the seller. */
  suggested: string[];
  /** What the last measurement reading found, in plain words. */
  readNote?: string;
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
}

/**
 * Regenerate's version of the bulk upload window: each listing's photos
 * (drag to reorder), size, SKU, cost of goods, labels and measurements,
 * checked before anything is wiped. Edits are written back to Item Details,
 * which the rewrite keeps and treats as fact. Outside clicks are ignored so a
 * stray click cannot discard edits; Escape, the close button and Cancel close
 * it whenever a save is not in progress.
 */
export function RegenerateReviewDialog({ listings, onClose, onRewrite }: Props) {
  const [rows, setRows] = useState<Record<string, RowState> | null>(null);
  const savedRef = useRef<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);
  const [pendingRemovals, setPendingRemovals] = useState(0);
  const pendingRemovalsRef = useRef(0);
  const [error, setError] = useState<string | null>(null);
  const [zoom, setZoom] = useState<{ convId: string; index: number } | null>(null);
  const [reading, setReading] = useState<{ done: number; total: number } | null>(null);
  const [sizeReading, setSizeReading] = useState<{ done: number; total: number } | null>(null);
  // One announcement when a size-tag read finishes, not one per item; editing a size ends it.
  const [announceSizes, setAnnounceSizes] = useState(false);
  const rowsRef = useRef(rows);
  useEffect(() => {
    rowsRef.current = rows;
  }, [rows]);
  const formRef = useRef<HTMLFormElement>(null);
  const count = listings.length;

  useEffect(() => {
    let cancelled = false;
    void mapWithConcurrency(listings, BULK_READ_CONCURRENCY, async (listing) => {
      const guess = guessGarment(listing.title);
      try {
        // What the rewrite will keep, including tag size, SKU, COG, labels and
        // measurements that so far live only on the listing.
        const { notes } = await api.conversations.regenerateDetails(listing.id);
        return [listing.id, parseItemDetails(notes, guess)] as const;
      } catch {
        return [listing.id, parseItemDetails(null, guess)] as const;
      }
    }).then((entries) => {
      if (cancelled) return;
      savedRef.current = Object.fromEntries(entries.map(([id, details]) => [id, JSON.stringify(details)]));
      setRows(Object.fromEntries(entries.map(([id, details]) => [id, {
        details,
        suggested: [],
        sizeSuggested: false,
        sizeReview: false,
        photoSet: 0,
      }])));
      window.setTimeout(() => formRef.current?.querySelector<HTMLInputElement>("input")?.focus(), 0);
    });
    return () => { cancelled = true; };
    // The parent mounts this per run; the listings do not change underneath it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const updateRow = (id: string, update: (row: RowState) => RowState) => setRows((current) => (
    current && current[id] ? { ...current, [id]: update(current[id]!) } : current
  ));

  const setDetails = (id: string, patch: Partial<ItemDetails>) => updateRow(id, (row) => {
    const suggested = patch.measurements
      ? row.suggested.filter((key) => patch.measurements![key] === row.details.measurements[key])
      : row.suggested;
    return { ...row, details: { ...row.details, ...patch }, suggested };
  });

  // Fills blank measurement fields from each listing's yardstick photos; typed values stay.
  const readMeasurements = async () => {
    if (!rows) return;
    setReading({ done: 0, total: listings.length });
    await readEach(listings, async (listing) => {
      const garment = rowsRef.current?.[listing.id]?.details.garment ?? rows[listing.id]!.details.garment;
      updateRow(listing.id, (row) => ({ ...row, readNote: "Reading the yardstick…" }));
      try {
        const result = await api.measurements.readSaved(listing.id, garment);
        // Fold into what is there now: the seller may have typed while it read.
        updateRow(listing.id, (row) => {
          if (row.details.garment !== garment) {
            // The garment changed while it read; the reading was for the old fields.
            return { ...row, readNote: "" };
          }
          const outcome = applyReading(garment, row.details.measurements, result);
          return {
            ...row,
            details: { ...row.details, measurements: outcome.measurements },
            suggested: [...new Set([...row.suggested, ...outcome.suggested])],
            readNote: outcome.note,
          };
        });
      } catch (err) {
        const message = err instanceof Error ? err.message : "Reading failed.";
        updateRow(listing.id, (row) => ({ ...row, readNote: message }));
      }
      setReading((current) => current && { ...current, done: current.done + 1 });
    });
    setReading(null);
  };

  // Fills a blank Size from each listing's size tag; a typed size stays. A missing,
  // unreadable or conflicting tag is left for the seller, never guessed.
  const readSizeTags = async () => {
    if (!rows) return;
    setSizeReading({ done: 0, total: listings.length });
    setAnnounceSizes(false);
    await readEach(listings, async (listing) => {
      const photoSet = rowsRef.current?.[listing.id]?.photoSet ?? 0;
      updateRow(listing.id, (row) => ({ ...row, sizeNote: READING_SIZE_NOTE }));
      try {
        const result = await api.sizeTags.readSaved(listing.id);
        updateRow(listing.id, (row) => {
          if (row.photoSet !== photoSet) return row;
          // A size an earlier reading filled is replaced, not treated as typed.
          const outcome = applySizeTagReading(row.sizeSuggested ? "" : row.details.size, result);
          // A re-read that saw nothing new (no tag, unreadable) keeps an earlier warning
          // on a typed size: the earlier evidence still stands. Editing the size clears it.
          if (!outcome.suggested && !outcome.needsReview && !outcome.note && row.sizeWarning) {
            return { ...row, sizeReview: true, sizeNote: row.sizeWarning };
          }
          const typed = !outcome.suggested && outcome.size.trim() !== "";
          return {
            ...row,
            details: { ...row.details, size: outcome.size },
            sizeSuggested: outcome.suggested,
            sizeReview: outcome.needsReview,
            sizeNote: outcome.note || undefined,
            sizeWarning: typed && outcome.needsReview ? outcome.note : undefined,
          };
        });
      } catch (err) {
        const message = err instanceof Error ? err.message : "Reading failed.";
        updateRow(listing.id, (row) => {
          if (row.photoSet !== photoSet) return row;
          const blank = row.details.size.trim() === "";
          // A size an earlier reading filled was not confirmed by this one, so it is checked too.
          if (blank || row.sizeSuggested) {
            return { ...row, sizeReview: true, sizeNote: `${message} ${blank ? "Type the size from the tag." : "Check the size against the tag."}` };
          }
          // The error is shown without dropping the tag evidence against the typed size.
          return { ...row, sizeNote: row.sizeWarning ? `${message} ${row.sizeWarning}` : message };
        });
      }
      setSizeReading((current) => current && { ...current, done: current.done + 1 });
    });
    setSizeReading(null);
    setAnnounceSizes(true);
  };

  // A size read from the photos may have come from the one removed: clear it for the seller
  // to read again or type, and ignore any reading of the earlier photos still in flight.
  const handlePhotoRemoved = (id: string) => {
    setAnnounceSizes(false);
    updateRow(id, (row) => {
      const cleared: Partial<RowState> = row.sizeSuggested
        ? { details: { ...row.details, size: "" }, sizeSuggested: false, sizeReview: true,
          sizeNote: "A photo was removed, so the size read from it was cleared. Read size tags again or type the size." }
        : row.sizeWarning
          ? { sizeWarning: undefined, sizeReview: true,
            sizeNote: "A photo was removed, so the earlier tag check no longer applies. Read size tags again or check the tag." }
          : row.sizeNote === READING_SIZE_NOTE
            ? { sizeReview: row.details.size.trim() === "",
              sizeNote: "A photo was removed while reading. Read size tags again or type the size." }
            : {};
      return { ...row, ...cleared, photoSet: row.photoSet + 1 };
    });
  };

  // The refs also guard actions fired before React renders the disabled buttons.
  const guardPhotoRemoval: GuardPhotoRemoval = async (remove) => {
    if (savingRef.current) return;
    setPendingRemovals(++pendingRemovalsRef.current);
    try {
      await remove();
    } finally {
      setPendingRemovals(--pendingRemovalsRef.current);
    }
  };

  const rewrite = async () => {
    if (!rows || savingRef.current || pendingRemovalsRef.current > 0 || pendingRemovals > 0
      || reading !== null || sizeReading !== null) return;
    savingRef.current = true;
    setSaving(true);
    setError(null);
    try {
      for (const listing of listings) {
        const details = rows[listing.id]?.details;
        if (!details || JSON.stringify(details) === savedRef.current[listing.id]) continue;
        const conv = await api.conversations.get(listing.id);
        await api.conversations.update(listing.id, { notes: notesWithItemDetails(conv.notes, details) });
      }
    } catch (err) {
      savingRef.current = false;
      setSaving(false);
      setError(err instanceof Error ? err.message : "Could not save details");
      return;
    }
    onRewrite();
  };

  // Enter moves on like a spreadsheet instead of submitting half a box.
  const onInputKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const inputs = Array.from(formRef.current?.querySelectorAll<HTMLInputElement>("input") || []);
    inputs[inputs.indexOf(event.currentTarget) + 1]?.focus();
  };

  const noun = count === 1 ? "listing" : "listings";

  return (
    <Dialog
      open
      disablePointerDismissal
      onOpenChange={(open) => {
        if (!open && !zoom && !savingRef.current) onClose();
      }}
    >
      <DialogPopup showCloseButton={false} className="max-w-3xl">
        <form
          ref={formRef}
          className="flex min-h-0 flex-1 flex-col"
          onSubmit={(event) => {
            event.preventDefault();
            void rewrite();
          }}
        >
          <DialogClose
            aria-label="Close without rewriting"
            disabled={saving}
            className="absolute end-2 top-2"
            render={<Button size="icon" variant="ghost" />}
          >
            <XIcon />
          </DialogClose>
          <DialogHeader className="pe-12 max-sm:px-4 max-sm:pt-4 max-sm:pb-2">
            <DialogTitle>Review {count} {noun} before rewriting</DialogTitle>
            <DialogDescription>
              Drag photos to set their order; the first one is the cover, and a click enlarges one. Rotate or remove a photo from its corners.
              Chat and every generated field are discarded, then each listing is written again from its
              photos and these details concurrently. Flaws and notes are kept.
              {" "}Read size tags and Read measurements fill blank sizes and measurements from the photos; check them before rewriting.
            </DialogDescription>
          </DialogHeader>
          <DialogPanel className="space-y-4 max-sm:px-4">
            <ul
              className="m-0 flex list-none flex-col gap-2.5 p-0"
              aria-label="Each listing's photos and details"
            >
              {rows
                ? listings.map((listing) => (
                  <ReviewRow
                    key={listing.id}
                    listing={listing}
                    row={rows[listing.id]!}
                    onChange={(patch) => setDetails(listing.id, patch)}
                    onSizeChange={(value) => {
                      setAnnounceSizes(false);
                      // An edit settles the old warning; a running read's note stays so a photo removal can still void it.
                      updateRow(listing.id, (row) => ({
                        ...row,
                        details: { ...row.details, size: value },
                        sizeSuggested: false,
                        sizeReview: false,
                        sizeWarning: undefined,
                        ...(row.sizeNote === READING_SIZE_NOTE ? {} : { sizeNote: undefined }),
                      }));
                    }}
                    onPhotoRemoved={() => handlePhotoRemoved(listing.id)}
                    removalDisabled={saving}
                    guardPhotoRemoval={guardPhotoRemoval}
                    onZoom={(index) => setZoom({ convId: listing.id, index })}
                    onInputKeyDown={onInputKeyDown}
                  />
                ))
                : (
                  <li className="bulk-upload-group flex min-w-0 flex-col gap-2.5 rounded-xl border border-border/60 bg-card/40 p-3">
                    <span className="text-2xs text-muted-foreground">Loading…</span>
                  </li>
                )}
            </ul>
            {error ? <FormError>{error}</FormError> : null}
          </DialogPanel>
          <p className="sr-only" role="status">
            {announceSizes && rows
              ? `Size tags read. ${Object.values(rows).filter((row) => row.sizeSuggested).length} filled, ${Object.values(rows).filter((row) => row.sizeReview).length} to check.`
              : ""}
          </p>
          <DialogFooter className="flex-col-reverse gap-2 max-sm:px-4 max-sm:py-3 sm:flex-row sm:flex-wrap sm:items-center sm:justify-end">
            <Button type="button" variant="ghost" className="sm:me-auto" disabled={saving} onClick={onClose}>
              Cancel
            </Button>
            <div className="grid grid-cols-2 gap-2 sm:contents">
              <Button
                type="button"
                variant="outline"
                disabled={saving || !rows || sizeReading !== null}
                title="Fill blank sizes from the size tag in each listing's photos"
                onClick={() => void readSizeTags()}
              >
                {sizeReading ? `Reading tags ${sizeReading.done} of ${sizeReading.total}…` : "Read size tags"}
              </Button>
              <Button
                type="button"
                variant="outline"
                disabled={saving || !rows || reading !== null}
                title="Fill blank measurements from photos with a yardstick on the item"
                onClick={() => void readMeasurements()}
              >
                {reading ? `Reading ${reading.done} of ${reading.total}…` : "Read measurements"}
              </Button>
            </div>
            <Button
              type="submit"
              disabled={saving || !rows || pendingRemovals > 0 || reading !== null || sizeReading !== null}
            >
              {saving ? "Saving…" : `Rewrite ${count}`}
            </Button>
          </DialogFooter>
        </form>
      </DialogPopup>
      {zoom ? (
        <ReviewZoom
          convId={zoom.convId}
          index={zoom.index}
          onStep={(index) => setZoom({ ...zoom, index })}
          onClose={() => setZoom(null)}
        />
      ) : null}
    </Dialog>
  );
}

function ReviewRow({
  listing,
  row,
  onChange,
  onSizeChange,
  onPhotoRemoved,
  removalDisabled,
  guardPhotoRemoval,
  onZoom,
  onInputKeyDown,
}: {
  listing: RegenerateReviewListing;
  row: RowState;
  onChange: (patch: Partial<ItemDetails>) => void;
  onSizeChange: (value: string) => void;
  onPhotoRemoved: () => void;
  removalDisabled: boolean;
  guardPhotoRemoval: GuardPhotoRemoval;
  onZoom: (index: number) => void;
  onInputKeyDown: (event: KeyboardEvent<HTMLInputElement>) => void;
}) {
  const { details, suggested, readNote, sizeSuggested, sizeReview, sizeNote } = row;
  const title = listing.title || "Untitled";
  return (
    <li className="bulk-upload-group flex min-w-0 flex-col gap-2.5 rounded-xl border border-border/60 bg-card/40 p-3">
      <div className="flex flex-wrap items-end justify-between gap-2.5 sm:flex-nowrap">
        <span
          className="min-w-0 flex-1 basis-full truncate font-medium text-foreground text-sm sm:basis-0"
          title={title}
        >
          {title}
        </span>
        <FormField label="Size" className="min-w-[7.5rem] flex-1 sm:w-32 sm:min-w-0 sm:flex-none">
          <Input
            size="sm"
            className={cn(
              sizeSuggested && "border-dashed border-info",
              sizeReview && "border-warning",
            )}
            inputClassName={cn(
              sizeSuggested && "is-suggested",
              sizeReview && "needs-review",
            )}
            type="text"
            autoComplete="off"
            placeholder="From the tag"
            aria-label={`Size for ${title}`}
            aria-describedby={sizeNote ? `regenerate-size-note-${listing.id}` : undefined}
            title={sizeSuggested ? "Read from the size tag. Check it before rewriting." : undefined}
            value={details.size}
            onChange={(event) => onSizeChange(event.target.value)}
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
            value={details.sku}
            onChange={(event) => onChange({ sku: event.target.value })}
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
              aria-checked={g.id === details.garment}
              data-pressed={g.id === details.garment ? "" : undefined}
              className={cn(toggleVariants({ variant: "segmented", size: "segmented" }))}
              onClick={() => onChange({ garment: g.id })}
            >
              {g.label}
            </button>
          ))}
        </div>
      </div>
      <ReviewPhotos
        convId={listing.id}
        onZoom={onZoom}
        onPhotoRemoved={onPhotoRemoved}
        removalDisabled={removalDisabled}
        guardPhotoRemoval={guardPhotoRemoval}
      />
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
        {garmentFields(details.garment).map((field) => {
          const isSuggested = suggested.includes(field.key);
          return (
            <FormField label={field.label} key={`${details.garment}-${field.key}`}>
              <Input
                size="sm"
                className={cn(isSuggested && "border-dashed border-info")}
                inputClassName={isSuggested ? "is-suggested" : undefined}
                type="number"
                step="0.25"
                min="0"
                inputMode="decimal"
                title={isSuggested ? "Read from the photos. Check it before rewriting." : undefined}
                value={details.measurements[field.key] || ""}
                onChange={(event) => onChange({
                  measurements: { ...details.measurements, [field.key]: event.target.value },
                })}
                onKeyDown={onInputKeyDown}
              />
            </FormField>
          );
        })}
      </div>
      {sizeNote ? (
        <p
          id={`regenerate-size-note-${listing.id}`}
          className={cn(
            "m-0 text-2xs leading-snug",
            sizeReview ? "text-warning-foreground" : "text-muted-foreground",
          )}
        >
          {sizeNote}
        </p>
      ) : null}
      {readNote ? (
        <p className="m-0 text-2xs leading-snug text-muted-foreground">{readNote}</p>
      ) : null}
      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-4">
        <FormField label="Cost of goods">
          <Input
            size="sm"
            type="number"
            min="0"
            step="0.01"
            inputMode="decimal"
            placeholder="0.00"
            value={details.cog}
            onChange={(event) => onChange({ cog: event.target.value })}
            onKeyDown={onInputKeyDown}
          />
        </FormField>
        <FormField label="Labels" className="sm:col-span-3">
          <Input
            size="sm"
            type="text"
            placeholder="To List, Bin 4"
            value={details.labels}
            onChange={(event) => onChange({ labels: event.target.value })}
            onKeyDown={onInputKeyDown}
          />
        </FormField>
      </div>
    </li>
  );
}

/** Turning a saved photo rewrites its stored file straight away, like the photo tray. */
function usePhotoRotate(convId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ photoId, degrees }: { photoId: string; degrees: 90 | -90 }) =>
      api.conversations.rotatePhoto(convId, photoId, degrees),
    onSuccess: (rotated) => {
      queryClient.setQueryData<ListingPhoto[]>(["photos", convId], (current) =>
        current?.map((photo) => (photo.id === rotated.id ? rotated : photo)),
      );
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
    onError: (err: Error) => toastManager.add({ type: "error", title: "Could not rotate photo", description: err.message }),
  });
}

/** The listing's saved photos in order; a drop, turn or removal is saved straight away, like the photo tray. */
export function ReviewPhotos({
  convId,
  onZoom,
  onPhotoRemoved,
  removalDisabled,
  guardPhotoRemoval,
}: {
  convId: string;
  onZoom: (index: number) => void;
  onPhotoRemoved?: () => void;
  removalDisabled?: boolean;
  guardPhotoRemoval?: GuardPhotoRemoval;
}) {
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ["photos", convId],
    queryFn: ({ signal }) => api.conversations.photos(convId, undefined, { signal }),
  });
  const photos = data || [];
  const [dragId, setDragId] = useState<string | null>(null);
  const [dropId, setDropId] = useState<string | null>(null);
  const canReorder = photos.length > 1;

  const reorder = useMutation({
    mutationFn: (orderedIds: string[]) => api.conversations.reorderPhotos(convId, orderedIds),
    onMutate: async (orderedIds) => {
      await queryClient.cancelQueries({ queryKey: ["photos", convId] });
      const previous = queryClient.getQueryData<ListingPhoto[]>(["photos", convId]);
      if (previous) {
        const byId = new Map(previous.map((photo) => [photo.id, photo]));
        queryClient.setQueryData(
          ["photos", convId],
          orderedIds.map((id) => byId.get(id)).filter((photo): photo is ListingPhoto => Boolean(photo)),
        );
      }
      return { previous };
    },
    onError: (_err, _ids, context) => {
      if (context?.previous) queryClient.setQueryData(["photos", convId], context.previous);
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["photos", convId] });
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });

  const rotate = usePhotoRotate(convId);
  const remove = useMutation({
    mutationFn: (photoId: string) => api.conversations.deletePhoto(convId, photoId),
    onSuccess: () => {
      onPhotoRemoved?.();
      void queryClient.invalidateQueries({ queryKey: ["photos", convId] });
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
    onError: (err: Error) => toastManager.add({ type: "error", title: "Could not remove photo", description: err.message }),
  });

  const deletePhoto = async (photoId: string) => {
    try {
      await remove.mutateAsync(photoId);
    } catch {
      // The mutation shows the error. Let the dialog release its pending count.
    }
  };

  const resetDrag = () => {
    setDragId(null);
    setDropId(null);
  };

  const drop = (targetId: string) => {
    const sourceId = dragId;
    resetDrag();
    if (!sourceId || sourceId === targetId) return;
    const next = moveItem(
      photos,
      photos.findIndex((photo) => photo.id === sourceId),
      photos.findIndex((photo) => photo.id === targetId),
    );
    if (next !== photos) reorder.mutate(next.map((photo) => photo.id));
  };

  return (
    <div className="photo-strip">
      {photos.map((photo, index) => (
        <div
          key={photo.id}
          className={[
            "photo-thumb",
            canReorder ? "photo-thumb-reorderable" : "",
            dragId === photo.id ? "is-dragging" : "",
            dropId === photo.id ? "is-drop-target" : "",
          ].filter(Boolean).join(" ")}
          draggable={canReorder}
          title={canReorder ? "Drag to reorder, click to enlarge" : undefined}
          onClick={() => onZoom(index)}
          onDragStart={(event) => {
            event.dataTransfer.effectAllowed = "move";
            event.dataTransfer.setData("text/plain", photo.id);
            setDragId(photo.id);
          }}
          onDragOver={(event) => {
            if (!dragId || dragId === photo.id) return;
            event.preventDefault();
            event.stopPropagation();
            event.dataTransfer.dropEffect = "move";
            if (dropId !== photo.id) setDropId(photo.id);
          }}
          onDrop={(event) => {
            event.preventDefault();
            event.stopPropagation();
            drop(photo.id);
          }}
          onDragEnd={resetDrag}
        >
          <StudioImage src={photoThumbUrl(photo)} alt="" draggable={false} loading="lazy" decoding="async" />
          <div className="photo-thumb-num">{String(index + 1).padStart(2, "0")}</div>
          <button
            type="button"
            tabIndex={-1}
            className="photo-thumb-rotate"
            aria-label={`Rotate photo ${index + 1} right`}
            title="Rotate right"
            disabled={rotate.isPending && rotate.variables.photoId === photo.id}
            onClick={(event) => {
              event.stopPropagation();
              if (!rotate.isPending) rotate.mutate({ photoId: photo.id, degrees: 90 });
            }}
          >
            <RotateCwIcon className="size-2.5" aria-hidden="true" />
          </button>
          {/* A listing keeps at least one photo; there is nothing to rewrite from otherwise. */}
          {canReorder ? (
            <button
              type="button"
              tabIndex={-1}
              className="photo-thumb-delete"
              aria-label={`Remove photo ${index + 1}`}
              title="Remove photo"
              disabled={removalDisabled || remove.isPending}
              onClick={(event) => {
                event.stopPropagation();
                if (removalDisabled || remove.isPending) return;
                if (guardPhotoRemoval) void guardPhotoRemoval(() => deletePhoto(photo.id));
                else void deletePhoto(photo.id);
              }}
            >
              <XIcon className="size-2.5" aria-hidden="true" />
            </button>
          ) : null}
        </div>
      ))}
    </div>
  );
}

/** One saved photo enlarged, with the same turn buttons as the photo tray's. */
function ReviewZoom({
  convId,
  index,
  onStep,
  onClose,
}: {
  convId: string;
  index: number;
  onStep: (index: number) => void;
  onClose: () => void;
}) {
  const { data } = useQuery({
    queryKey: ["photos", convId],
    queryFn: ({ signal }) => api.conversations.photos(convId, undefined, { signal }),
  });
  const photos = data || [];
  const photo = photos[index];
  const rotate = usePhotoRotate(convId);

  if (!photo) return null;
  return (
    <PhotoZoom
      urls={photos.map((item) => item.url)}
      index={index}
      rotating={rotate.isPending}
      onRotate={(degrees) => rotate.mutate({ photoId: photo.id, degrees })}
      onStep={onStep}
      onClose={onClose}
    />
  );
}
