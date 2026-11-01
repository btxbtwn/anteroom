import { useWorkspaceRuntime } from "../app/runtime";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { SourceBox } from "../api/types";
import { rotationForTurns, type BulkUploadDefaults, type BulkUploadItem, type PhotoRotation } from "../bulkPhotoUpload";
import { formatMoney } from "./analyticsFormat";
import { BOXES_QUERY_KEY, boxStoreName } from "./boxPurchases";
import { GARMENTS, garmentFields, guessGarment, type Garment } from "./garmentMeasurements";
import { dragHasFiles, listingTitleForFolder, moveItem, type PhotoFolderGroup } from "../photoDrop";
import { applyReading, READ_CONCURRENCY, readEach } from "../measurementReading";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "./ui/select";

interface Props {
  groups: PhotoFolderGroup[];
  onCancel: () => void;
  /** Create every draft; `generate` also starts generating them from their photos. */
  onConfirm: (defaults: BulkUploadDefaults, items: BulkUploadItem[], generate: boolean) => void;
}

const EMPTY_DEFAULTS: BulkUploadDefaults = { cog: "", labels: "" };

type DragSpot = { group: number; photo: number };

type Row = {
  sku: string;
  garment: Garment;
  measurements: Record<string, string>;
  /** Fields filled from the photos and not yet touched by the seller. */
  suggested: string[];
  /** What the last photo reading found, in plain words. */
  readNote?: string;
};

/**
 * Everything a bulk upload needs in one window, before anything is created:
 * photo order, the box, COG and labels every draft shares, and each item's
 * own SKU and measurements. Click a photo to read the tape in it, or to turn
 * it the right way up. Only the x and Cancel close it, so a stray click
 * outside cannot throw away what was typed.
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
    garment: guessGarment(listingTitleForFolder(group.folder)),
    measurements: {},
    suggested: [],
  })));
  const [reading, setReading] = useState<{ done: number; total: number } | null>(null);
  const [dragFrom, setDragFrom] = useState<DragSpot | null>(null);
  const [dropOn, setDropOn] = useState<DragSpot | null>(null);
  const [zoom, setZoom] = useState<DragSpot | null>(null);
  // Quarter turns clockwise per file; the files are not saved yet, so the server turns them after upload.
  const [turns, setTurns] = useState<Map<File, number>>(() => new Map());
  // One preview URL per picked file; reordering only shuffles which file sits where.
  const [previewUrls, setPreviewUrls] = useState<Map<File, string>>(() => new Map());
  const formRef = useRef<HTMLFormElement>(null);
  const cogRef = useRef<HTMLInputElement>(null);
  const count = groups.length;

  // Created and revoked in the same effect so StrictMode's remount gets fresh URLs.
  useEffect(() => {
    const urls = new Map(initialGroups.flatMap((group) => group.files).map((file) => [file, URL.createObjectURL(file)]));
    setPreviewUrls(urls);
    return () => urls.forEach((url) => URL.revokeObjectURL(url));
  }, [initialGroups]);

  const resetDrag = () => {
    setDragFrom(null);
    setDropOn(null);
  };

  // Photos only move within their own listing; the first one becomes the cover.
  const dropPhoto = (target: DragSpot) => {
    const source = dragFrom;
    resetDrag();
    if (!source || source.group !== target.group) return;
    setGroups((current) => current.map((group, index) => (
      index === target.group ? { ...group, files: moveItem(group.files, source.photo, target.photo) } : group
    )));
  };

  const setRow = (index: number, patch: Partial<Row>) => setRows((current) => current.map((row, i) => (
    i === index ? { ...row, ...patch } : row
  )));

  // Fills blank measurement fields from each item's yardstick photos; typed values stay.
  const readFromPhotos = async () => {
    const todo = groups.map((_, index) => index).filter((index) => groups[index]!.files.length);
    setReading({ done: 0, total: todo.length });
    await readEach(todo, READ_CONCURRENCY, async (index) => {
      const garment = rows[index]!.garment;
      setRow(index, { readNote: "Reading the yardstick…" });
      try {
        const result = await api.measurements.read(groups[index]!.files, garment);
        setRows((current) => current.map((row, i) => {
          if (i !== index || row.garment !== garment) return row;
          const outcome = applyReading(garment, row.measurements, result);
          return {
            ...row,
            measurements: outcome.measurements,
            suggested: [...new Set([...row.suggested, ...outcome.suggested])],
            readNote: outcome.note,
          };
        }));
      } catch (error) {
        setRow(index, { readNote: error instanceof Error ? error.message : "Reading failed." });
      }
      setReading((current) => current && { ...current, done: current.done + 1 });
    });
    setReading(null);
  };

  const rotate = (file: File, degrees: 90 | -90) => setTurns((current) => (
    new Map(current).set(file, ((current.get(file) || 0) + degrees / 90 + 4) % 4)
  ));

  // A listing keeps at least one photo; there is nothing to generate from otherwise.
  const removePhoto = (spot: DragSpot) => setGroups((current) => current.map((group, index) => (
    index === spot.group ? { ...group, files: group.files.filter((_, photo) => photo !== spot.photo) } : group
  )));

  useEffect(() => {
    const focusTimer = window.setTimeout(() => cogRef.current?.focus(), 0);
    // Escape only puts an enlarged photo away; it never closes the window.
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setZoom(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.clearTimeout(focusTimer);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, []);

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
      const { sku, garment, measurements } = rows[index]!;
      return { ...group, sku, garment, measurements, rotations };
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

  return createPortal(
    <div className="confirm-dialog bulk-upload-dialog" role="presentation">
      <div className="confirm-dialog-backdrop" />
      <form
        ref={formRef}
        className="confirm-dialog-popup bulk-upload-popup"
        role="dialog"
        aria-modal="true"
        aria-labelledby="bulk-upload-title"
        onSubmit={(event) => {
          event.preventDefault();
          confirm(true);
        }}
      >
        <button type="button" className="confirm-dialog-close" onClick={onCancel} aria-label="Cancel bulk upload">
          x
        </button>
        <div className="confirm-dialog-header">
          <h2 id="bulk-upload-title" className="confirm-dialog-title">
            Set details for {count} listings
          </h2>
          <p className="confirm-dialog-description">
            Box, COG and labels go on every draft. Drag photos to set their order; the first one is
            the cover, and a click enlarges one. Rotate or remove a photo from its corners. Type each item's SKU and measurements in inches, or
            leave them blank: a typed SKU is kept as is, a blank one is made up from the brand and size.
            Read from photos fills blank measurements from yardstick photos; check them before creating.
          </p>
        </div>
        <div className="bulk-upload-fields">
          {boxes.length ? (
            <div className="bulk-upload-field">
              <span className="label">Box</span>
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
            </div>
          ) : null}
          <label className="bulk-upload-field">
            <span className="label">Cost of goods (COG)</span>
            <input
              ref={cogRef}
              className="input"
              type="number"
              min="0"
              step="0.01"
              inputMode="decimal"
              placeholder={box?.cost_per_piece != null ? box.cost_per_piece.toFixed(2) : "0.00"}
              value={defaults.cog}
              onChange={(event) => setDefaults((current) => ({ ...current, cog: event.target.value }))}
              onKeyDown={onInputKeyDown}
            />
            {box?.cost_per_piece != null ? (
              <span className="bulk-upload-help">Leave blank to use the box's {formatMoney(box.cost_per_piece)} a piece.</span>
            ) : null}
          </label>
          <label className="bulk-upload-field">
            <span className="label">Labels</span>
            <input
              className="input"
              type="text"
              placeholder="To List, Bin 4"
              value={defaults.labels}
              onChange={(event) => setDefaults((current) => ({ ...current, labels: event.target.value }))}
              onKeyDown={onInputKeyDown}
            />
            <span className="bulk-upload-help">Separate multiple labels with commas.</span>
          </label>
        </div>
        <ul className="bulk-upload-groups" aria-label="Each listing's photos, SKU and measurements">
          {groups.map((group, groupIndex) => {
            const title = listingTitleForFolder(group.folder);
            const row = rows[groupIndex]!;
            return (
              <li key={group.folder ?? groupIndex} className="bulk-upload-group">
                <div className="bulk-measure-head">
                  <span className="bulk-measure-title" title={title}>
                    {title}
                    <span className="photo-tray-meta"> · {group.files.length} photos</span>
                  </span>
                  <label className="bulk-measure-sku">
                    <span className="label">SKU</span>
                    <input
                      className="input"
                      type="text"
                      autoComplete="off"
                      spellCheck={false}
                      aria-label={`SKU for ${title}`}
                      value={row.sku}
                      onChange={(event) => setRow(groupIndex, { sku: event.target.value })}
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
                        aria-checked={g.id === row.garment}
                        className={`pr-pill${g.id === row.garment ? " is-active" : ""}`}
                        onClick={() => setRow(groupIndex, { garment: g.id })}
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
                          ↻
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
                            x
                          </button>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
                <div className="bulk-measure-fields">
                  {garmentFields(row.garment).map((field) => (
                    <label className="bulk-measure-field" key={`${row.garment}-${field.key}`}>
                      <span className="label">{field.label}</span>
                      <input
                        className={`input${row.suggested.includes(field.key) ? " is-suggested" : ""}`}
                        type="number"
                        step="0.25"
                        min="0"
                        inputMode="decimal"
                        title={row.suggested.includes(field.key) ? "Read from the photos. Check it before creating." : undefined}
                        value={row.measurements[field.key] || ""}
                        onChange={(event) => setRow(groupIndex, {
                          measurements: { ...row.measurements, [field.key]: event.target.value },
                          suggested: row.suggested.filter((key) => key !== field.key),
                        })}
                        onKeyDown={onInputKeyDown}
                      />
                    </label>
                  ))}
                </div>
                {row.readNote ? <p className="bulk-upload-help bulk-measure-note">{row.readNote}</p> : null}
              </li>
            );
          })}
        </ul>
        <div className="confirm-dialog-footer">
          <button type="button" className="btn btn-ghost" onClick={onCancel}>
            Cancel
          </button>
          <span className="bulk-measure-spacer" />
          <button
            type="button"
            className="btn btn-secondary"
            disabled={reading !== null || !runtime.features.has("generation")}
            title="Fill blank measurements from photos with a yardstick on the item"
            onClick={() => void readFromPhotos()}
          >
            {reading ? `Reading ${reading.done} of ${reading.total}…` : "Read from photos"}
          </button>
          <button type="button" className="btn btn-secondary" disabled={invalidCog || reading !== null} onClick={() => confirm(false)}>
            Create drafts only
          </button>
          {runtime.features.has("generation") && <button type="submit" className="btn btn-primary" disabled={invalidCog || reading !== null}>
            Create and generate {count}
          </button>}
        </div>
      </form>
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
    </div>,
    document.body,
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
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    ref.current?.focus();
  }, []);

  return (
    <div
      ref={ref}
      className="photo-lightbox"
      role="dialog"
      aria-modal="true"
      tabIndex={-1}
      aria-label={`Photo ${index + 1} of ${urls.length}`}
      onKeyDown={(event) => {
        if (event.key === "ArrowLeft") step(-1);
        if (event.key === "ArrowRight") step(1);
      }}
      onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}
    >
      <button type="button" className="photo-lightbox-close" onClick={onClose} aria-label="Close">
        x
      </button>
      <div className="photo-lightbox-rotate">
        <button type="button" onClick={() => onRotate(-90)} disabled={rotating} aria-label="Rotate left" title="Rotate left">
          ↺
        </button>
        <button type="button" onClick={() => onRotate(90)} disabled={rotating} aria-label="Rotate right" title="Rotate right">
          ↻
        </button>
      </div>
      {urls.length > 1 && (
        <button type="button" className="photo-lightbox-nav photo-lightbox-prev" onClick={() => step(-1)} aria-label="Previous photo">
          ‹
        </button>
      )}
      <img
        className={`photo-lightbox-image${turns % 2 ? " is-quarter-turned" : ""}`}
        style={turns ? { transform: `rotate(${turns * 90}deg)` } : undefined}
        src={urls[index]}
        alt={`Photo ${index + 1}`}
        onClick={onClose}
      />
      {urls.length > 1 && (
        <button type="button" className="photo-lightbox-nav photo-lightbox-next" onClick={() => step(1)} aria-label="Next photo">
          ›
        </button>
      )}
      <div className="photo-lightbox-meta">{String(index + 1).padStart(2, "0")} / {String(urls.length).padStart(2, "0")}</div>
    </div>
  );
}
