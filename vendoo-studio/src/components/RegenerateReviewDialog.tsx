import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Photo } from "../api/types";
import { moveItem } from "../photoDrop";
import { addToast } from "../ui/toast";
import { PhotoZoom } from "./BulkUploadDialog";
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

/**
 * Regenerate's version of the bulk upload window: each listing's photos
 * (drag to reorder), SKU, COG, labels and measurements, checked before
 * anything is wiped. Edits are written back to Item Details, which the
 * rewrite keeps and treats as fact. Only the x and Cancel close it.
 */
export function RegenerateReviewDialog({ listings, onClose, onRewrite }: Props) {
  const [rows, setRows] = useState<Record<string, ItemDetails> | null>(null);
  const savedRef = useRef<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [zoom, setZoom] = useState<{ convId: string; index: number } | null>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const count = listings.length;

  useEffect(() => {
    // Escape only puts an enlarged photo away; it never closes the window.
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setZoom(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void Promise.all(listings.map(async (listing) => {
      const guess = guessGarment(listing.title);
      try {
        const conv = await api.conversations.get(listing.id);
        return [listing.id, parseItemDetails(conv.notes, guess)] as const;
      } catch {
        return [listing.id, parseItemDetails(null, guess)] as const;
      }
    })).then((entries) => {
      if (cancelled) return;
      savedRef.current = Object.fromEntries(entries.map(([id, details]) => [id, JSON.stringify(details)]));
      setRows(Object.fromEntries(entries));
      window.setTimeout(() => formRef.current?.querySelector<HTMLInputElement>("input")?.focus(), 0);
    });
    return () => { cancelled = true; };
    // The parent mounts this per run; the listings do not change underneath it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const setRow = (id: string, patch: Partial<ItemDetails>) => setRows((current) => (
    current ? { ...current, [id]: { ...current[id]!, ...patch } } : current
  ));

  const rewrite = async () => {
    if (!rows) return;
    setSaving(true);
    setError(null);
    try {
      for (const listing of listings) {
        const details = rows[listing.id];
        if (!details || JSON.stringify(details) === savedRef.current[listing.id]) continue;
        const conv = await api.conversations.get(listing.id);
        await api.conversations.update(listing.id, { notes: notesWithItemDetails(conv.notes, details) });
      }
    } catch (err) {
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

  return createPortal(
    <div className="confirm-dialog bulk-upload-dialog" role="presentation">
      <div className="confirm-dialog-backdrop" />
      <form
        ref={formRef}
        className="confirm-dialog-popup bulk-upload-popup"
        role="dialog"
        aria-modal="true"
        aria-labelledby="regenerate-review-title"
        onSubmit={(event) => {
          event.preventDefault();
          if (!saving && rows) void rewrite();
        }}
      >
        <button
          type="button"
          className="confirm-dialog-close"
          disabled={saving}
          onClick={onClose}
          aria-label="Close without rewriting"
        >
          x
        </button>
        <div className="confirm-dialog-header">
          <h2 id="regenerate-review-title" className="confirm-dialog-title">
            Review {count} {noun} before rewriting
          </h2>
          <p className="confirm-dialog-description">
            Drag photos to set their order; the first one is the cover, and a click enlarges one. Rotate or remove a photo from its corners.
            Chat and every generated field are discarded, then each listing is written again from its
            photos and these details, one at a time. Flaws and notes are kept. Vendoo stays as it is
            until you send again.
          </p>
        </div>
        <ul className="bulk-upload-groups" aria-label="Each listing's photos and details">
          {rows
            ? listings.map((listing) => (
              <ReviewRow
                key={listing.id}
                listing={listing}
                details={rows[listing.id]!}
                onChange={(patch) => setRow(listing.id, patch)}
                onZoom={(index) => setZoom({ convId: listing.id, index })}
                onInputKeyDown={onInputKeyDown}
              />
            ))
            : <li className="bulk-upload-group"><span className="photo-tray-meta">Loading…</span></li>}
        </ul>
        {error ? <p className="bulk-measure-error" role="alert">{error}</p> : null}
        <div className="confirm-dialog-footer">
          <button type="button" className="btn btn-ghost" disabled={saving} onClick={onClose}>
            Cancel
          </button>
          <span className="bulk-measure-spacer" />
          <button type="submit" className="btn btn-primary" disabled={saving || !rows}>
            {saving ? "Saving…" : `Rewrite ${count}`}
          </button>
        </div>
      </form>
      {zoom ? (
        <ReviewZoom
          convId={zoom.convId}
          index={zoom.index}
          onStep={(index) => setZoom({ ...zoom, index })}
          onClose={() => setZoom(null)}
        />
      ) : null}
    </div>,
    document.body,
  );
}

function ReviewRow({
  listing,
  details,
  onChange,
  onZoom,
  onInputKeyDown,
}: {
  listing: RegenerateReviewListing;
  details: ItemDetails;
  onChange: (patch: Partial<ItemDetails>) => void;
  onZoom: (index: number) => void;
  onInputKeyDown: (event: KeyboardEvent<HTMLInputElement>) => void;
}) {
  const title = listing.title || "Untitled";
  return (
    <li className="bulk-upload-group">
      <div className="bulk-measure-head">
        <span className="bulk-measure-title" title={title}>{title}</span>
        <label className="bulk-measure-sku">
          <span className="label">SKU</span>
          <input
            className="input"
            type="text"
            autoComplete="off"
            spellCheck={false}
            aria-label={`SKU for ${title}`}
            value={details.sku}
            onChange={(event) => onChange({ sku: event.target.value })}
            onKeyDown={onInputKeyDown}
          />
        </label>
        <div className="pr-pills item-garment-pills" role="radiogroup" aria-label={`Garment type for ${title}`}>
          {GARMENTS.map((g) => (
            <button
              key={g.id}
              type="button"
              role="radio"
              tabIndex={-1}
              aria-checked={g.id === details.garment}
              className={`pr-pill${g.id === details.garment ? " is-active" : ""}`}
              onClick={() => onChange({ garment: g.id })}
            >
              {g.label}
            </button>
          ))}
        </div>
      </div>
      <ReviewPhotos convId={listing.id} onZoom={onZoom} />
      <div className="bulk-measure-fields">
        {garmentFields(details.garment).map((field) => (
          <label className="bulk-measure-field" key={`${details.garment}-${field.key}`}>
            <span className="label">{field.label}</span>
            <input
              className="input"
              type="number"
              step="0.25"
              min="0"
              inputMode="decimal"
              value={details.measurements[field.key] || ""}
              onChange={(event) => onChange({
                measurements: { ...details.measurements, [field.key]: event.target.value },
              })}
              onKeyDown={onInputKeyDown}
            />
          </label>
        ))}
      </div>
      <div className="bulk-measure-fields">
        <label className="bulk-measure-field">
          <span className="label">Cost of goods (COG)</span>
          <input
            className="input"
            type="number"
            min="0"
            step="0.01"
            inputMode="decimal"
            placeholder="0.00"
            value={details.cog}
            onChange={(event) => onChange({ cog: event.target.value })}
            onKeyDown={onInputKeyDown}
          />
        </label>
        <label className="bulk-measure-field bulk-measure-labels">
          <span className="label">Labels</span>
          <input
            className="input"
            type="text"
            placeholder="To List, Bin 4"
            value={details.labels}
            onChange={(event) => onChange({ labels: event.target.value })}
            onKeyDown={onInputKeyDown}
          />
        </label>
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
      queryClient.setQueryData<Photo[]>(["photos", convId], (current) =>
        current?.map((photo) => (photo.id === rotated.id ? rotated : photo)),
      );
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
    onError: (err: Error) => addToast({ type: "error", title: "Could not rotate photo", description: err.message }),
  });
}

/** The listing's saved photos in order; a drop, turn or removal is saved straight away, like the photo tray. */
function ReviewPhotos({ convId, onZoom }: { convId: string; onZoom: (index: number) => void }) {
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ["photos", convId],
    queryFn: () => api.conversations.photos(convId),
  });
  const photos = data || [];
  const [dragId, setDragId] = useState<string | null>(null);
  const [dropId, setDropId] = useState<string | null>(null);
  const canReorder = photos.length > 1;

  const reorder = useMutation({
    mutationFn: (orderedIds: string[]) => api.conversations.reorderPhotos(convId, orderedIds),
    onMutate: async (orderedIds) => {
      await queryClient.cancelQueries({ queryKey: ["photos", convId] });
      const previous = queryClient.getQueryData<Photo[]>(["photos", convId]);
      if (previous) {
        const byId = new Map(previous.map((photo) => [photo.id, photo]));
        queryClient.setQueryData(
          ["photos", convId],
          orderedIds.map((id) => byId.get(id)).filter((photo): photo is Photo => Boolean(photo)),
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
      void queryClient.invalidateQueries({ queryKey: ["photos", convId] });
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
    onError: (err: Error) => addToast({ type: "error", title: "Could not remove photo", description: err.message }),
  });

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
          <img src={photo.url} alt="" draggable={false} />
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
            ↻
          </button>
          {/* A listing keeps at least one photo; there is nothing to rewrite from otherwise. */}
          {canReorder ? (
            <button
              type="button"
              tabIndex={-1}
              className="photo-thumb-delete"
              aria-label={`Remove photo ${index + 1}`}
              title="Remove photo"
              disabled={remove.isPending}
              onClick={(event) => {
                event.stopPropagation();
                remove.mutate(photo.id);
              }}
            >
              x
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
    queryFn: () => api.conversations.photos(convId),
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
