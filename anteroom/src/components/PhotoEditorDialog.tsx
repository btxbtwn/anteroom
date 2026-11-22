/**
 * The photo editor: Crop, Adjust (filters, flip, border), Markup (arrows,
 * shapes, labels, redaction) and AI Studio (Photoroom), laid out like Photos
 * and Photoroom with the photo on the left and the tools beside it (below it
 * on a phone). Every edit is a recipe the server renders from the kept upload,
 * so Reset to original always brings the upload back.
 */
import { PhotoLoadFailure, StudioImage } from "./StudioImage";
import { FlipHorizontal2Icon, FlipVertical2Icon, PlusIcon, Trash2Icon, XIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Annotation, CropBox, ListingPhoto, PhotoEdits, Redaction, StudioOptions, VirtualModel } from "../api/types";
import { cn } from "../lib/utils";
import {
  BACKDROP_COLORS,
  BORDER_COLORS,
  DEFAULT_BACKDROP,
  DEFAULT_BORDER_COLOR,
  DEFAULT_MARK_COLOR,
  MARK_COLORS,
  isHexColor,
} from "../photoColors";
import {
  CROP_ASPECTS,
  MARK_TOOLS,
  PHOTO_FILTERS,
  centeredCrop,
  dragCrop,
  flipEdits,
  markFromDrag,
  withCrop,
  normalizeStudio,
  studioProblem,
  type CropHandle,
  type MarkTool,
} from "../photoEdits";
import { photoThumbUrl } from "../photoThumb";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Dialog, DialogPopup, DialogTitle } from "./ui/dialog";
import { Input } from "./ui/input";
import { Spinner } from "./ui/spinner";
import { ToggleGroup, Toggle } from "./ui/toggle-group";
import { SettingsSelect, type SettingsSelectOption } from "./settings/SettingsLayout";

const FULL_FRAME: CropBox = { x: 0, y: 0, width: 1, height: 1 };
/** The server keeps at most this many annotations, and as many redactions, per photo. */
const MAX_MARKS = 30;
const PREVIEW_DELAY_MS = 150;

type Tab = "crop" | "adjust" | "markup" | "studio";
const TABS: { id: Tab; label: string }[] = [
  { id: "crop", label: "Crop" },
  { id: "adjust", label: "Adjust" },
  { id: "markup", label: "Markup" },
  { id: "studio", label: "AI Studio" },
];

interface Props {
  convId: string;
  photo: ListingPhoto;
  index: number;
  /** The photos AI Studio may change along with this one: this listing's, or this form's. */
  photos?: ListingPhoto[];
  onSaved: (photo: ListingPhoto) => void;
  onClose: () => void;
  /** The tab to open on; the lightbox's Edit opens on Crop. */
  initialTab?: Tab;
}

/** The recipe as the server would store it, so comparing two tells whether anything changed. */
function normalized(edits: PhotoEdits | null | undefined): PhotoEdits {
  return {
    rotate: edits?.rotate ?? 0,
    straighten: edits?.straighten ?? 0,
    crop: edits?.crop ?? null,
    exposure: edits?.exposure ?? 0,
    brightness: edits?.brightness ?? 0,
    contrast: edits?.contrast ?? 0,
    background: edits?.background ?? null,
    flip_horizontal: edits?.flip_horizontal ?? false,
    flip_vertical: edits?.flip_vertical ?? false,
    filter: edits?.filter ?? null,
    redactions: edits?.redactions ?? [],
    annotations: edits?.annotations ?? [],
    border: edits?.border ?? null,
    studio: edits?.studio ?? null,
  };
}

export function PhotoEditorDialog({ convId, photo, index, photos, onSaved, onClose, initialTab = "crop" }: Props) {
  const saved = useMemo(() => normalized(photo.edits), [photo.edits]);
  const [edits, setEdits] = useState<PhotoEdits>(saved);
  const [tab, setTab] = useState<Tab>(initialTab);
  const [aspectId, setAspectId] = useState("free");
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [frameAspect, setFrameAspect] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [previewError, setPreviewError] = useState<string | null>(null);
  // A preview that arrived but cannot be shown; Retry asks for a new one.
  const [unreadablePreview, setUnreadablePreview] = useState<string | null>(null);
  const [previewAttempt, setPreviewAttempt] = useState(0);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [pen, setPen] = useState<MarkPen>({ tool: "arrow", color: DEFAULT_MARK_COLOR, text: "" });
  // AI Studio keeps the editor open (and on its tab) until Photoroom answers.
  const [studioRunning, setStudioRunning] = useState(false);

  // The crop is drawn on the whole frame; marks are drawn live over the finished frame without them.
  const cropping = tab === "crop";
  const previewEdits = useMemo<PhotoEdits>(
    () => (tab === "markup" ? { ...edits, annotations: [], redactions: [], border: null } : edits),
    [edits, tab],
  );
  const previewKey = JSON.stringify([previewEdits, !cropping]);

  useEffect(() => {
    const controller = new AbortController();
    let url: string | null = null;
    setLoading(true);
    const timer = setTimeout(() => {
      api.conversations.previewPhotoEdits(convId, photo.id, previewEdits, !cropping, controller.signal)
        .then((blob) => {
          if (controller.signal.aborted) return;
          url = URL.createObjectURL(blob);
          setPreviewUrl(url);
          setPreviewError(null);
          setLoading(false);
        })
        .catch((err: Error) => {
          if (controller.signal.aborted) return;
          setPreviewError(err.message || "Preview failed");
          setLoading(false);
        });
    }, PREVIEW_DELAY_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
    // previewKey stands for previewEdits and cropping.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [convId, photo.id, previewKey, previewAttempt]);

  const retry = () => {
    setPreviewError(null);
    setPreviewUrl(null);
    setFrameAspect(null);
    setPreviewAttempt((count) => count + 1);
  };

  const aspect = CROP_ASPECTS.find((option) => option.id === aspectId)?.aspect ?? null;
  const chooseAspect = (id: string) => {
    setAspectId(id);
    const next = CROP_ASPECTS.find((option) => option.id === id)?.aspect;
    if (next && frameAspect) setEdits((current) => withCrop(current, centeredCrop(next, frameAspect)));
  };
  const changed = JSON.stringify(edits) !== JSON.stringify(saved);
  const unreadable = previewUrl !== null && unreadablePreview === previewUrl;

  const save = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      onSaved(await api.conversations.editPhoto(convId, photo.id, edits));
      onClose();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const busy = saving || studioRunning;
  return (
    <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
      <DialogPopup
        showCloseButton={false}
        bottomStickOnMobile={false}
        className="row-start-1 flex h-[min(92vh,860px)] w-[min(96vw,1180px)] max-w-none flex-col overflow-hidden"
        aria-label={`Edit photo ${index + 1}`}
      >
        <div className="flex items-center gap-3 border-b px-4 py-3">
          <DialogTitle className="text-base">Edit photo {index + 1}</DialogTitle>
          <Button className="ms-auto" size="icon-sm" variant="ghost" aria-label="Close" onClick={onClose} disabled={busy}>
            <XIcon />
          </Button>
        </div>
        <div className="grid min-h-0 flex-1 grid-rows-[minmax(0,1fr)_minmax(0,1fr)] md:grid-cols-[1fr_320px] md:grid-rows-1">
          <div className="relative flex min-h-0 items-center justify-center overflow-hidden bg-neutral-900 p-6">
            <div className="flex size-full items-center justify-center [container-type:size]">
              {unreadable ? (
                // The same file would fail again: Retry renders these edits anew.
                // No overlay here, so nothing covers the button.
                <PhotoLoadFailure status={null} retry={retry} />
              ) : previewUrl ? (
                <div className="relative max-w-full leading-none">
                  <StudioImage
                    src={previewUrl}
                    alt={cropping ? `Photo ${index + 1} crop preview` : `Photo ${index + 1} preview`}
                    draggable={false}
                    className="block max-h-[100cqh] max-w-[100cqw] select-none object-contain"
                    onError={() => setUnreadablePreview(previewUrl)}
                    onLoad={(event) => {
                      const img = event.currentTarget;
                      if (img.naturalHeight && cropping) setFrameAspect(img.naturalWidth / img.naturalHeight);
                    }}
                  />
                  {cropping ? (
                    <CropOverlay
                      box={edits.crop ?? FULL_FRAME}
                      aspect={aspect}
                      frameAspect={frameAspect ?? 1}
                      onChange={(crop) => setEdits((current) => withCrop(current, crop))}
                    />
                  ) : tab === "markup" ? (
                    <MarkupOverlay edits={edits} pen={pen} onChange={setEdits} />
                  ) : null}
                </div>
              ) : null}
            </div>
            {loading ? (
              <span className="absolute top-3 right-3 inline-flex items-center gap-1.5 rounded-md bg-black/60 px-2 py-1 text-white/80 text-xs" role="status">
                <Spinner className="size-3" />
                {previewUrl ? "Updating…" : "Loading photo…"}
              </span>
            ) : null}
          </div>
          <div className="flex min-h-0 flex-col gap-4 overflow-y-auto border-t p-4 md:border-t-0 md:border-l">
            <ToggleGroup
              aria-label="Editing tools"
              className="w-full"
              value={[tab]}
              onValueChange={(next) => {
                const value = next[0] as Tab | undefined;
                if (value && !studioRunning) setTab(value);
              }}
            >
              {TABS.map((option) => (
                <Toggle key={option.id} value={option.id} className="flex-1" aria-label={option.label}>
                  {option.label}
                </Toggle>
              ))}
            </ToggleGroup>
            {tab === "crop" ? (
              <CropTools
                aspectId={aspectId}
                hasCrop={Boolean(edits.crop)}
                disabled={!frameAspect || loading}
                onAspect={chooseAspect}
                onClear={() => { setAspectId("free"); setEdits((current) => withCrop(current, null)); }}
              />
            ) : tab === "adjust" ? (
              <AdjustTools edits={edits} onChange={setEdits} />
            ) : tab === "markup" ? (
              <MarkupList edits={edits} pen={pen} onPen={setPen} onChange={setEdits} />
            ) : (
              <StudioTools
                convId={convId}
                photo={photo}
                photos={photos ?? [photo]}
                unsaved={changed}
                onSaved={onSaved}
                onRunning={setStudioRunning}
                onRebase={(next) => setEdits(normalized(next))}
                onDone={onClose}
              />
            )}
            {previewError && (
              <div className="flex flex-col items-start gap-1.5">
                <p className="text-destructive-foreground text-xs" role="alert">{previewError}</p>
                <Button size="xs" variant="outline" onClick={retry}>Retry</Button>
              </div>
            )}
            {saveError ? <p className="text-destructive-foreground text-xs" role="alert">{saveError}</p> : null}
          </div>
        </div>
        <div className="flex items-center gap-2 border-t bg-muted/72 px-4 py-3">
          {changed ? (
            <Button variant="ghost-muted" size="sm" onClick={() => setEdits(saved)} disabled={busy}>
              Discard changes
            </Button>
          ) : null}
          <Button className="ms-auto" variant="outline" size="sm" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button size="sm" onClick={() => void save()}
            disabled={busy || !changed || loading || Boolean(previewError) || unreadable || (cropping && !frameAspect)}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </div>
      </DialogPopup>
    </Dialog>
  );
}

function CropTools({ aspectId, hasCrop, disabled, onAspect, onClear }: {
  aspectId: string;
  hasCrop: boolean;
  disabled: boolean;
  onAspect: (id: string) => void;
  onClear: () => void;
}) {
  return (
    <>
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Crop shape">
        {CROP_ASPECTS.map((option) => (
          <Button key={option.id} size="xs" variant={aspectId === option.id ? "default" : "outline"}
            aria-pressed={aspectId === option.id} disabled={disabled}
            onClick={() => onAspect(option.id)}>
            {option.label}
          </Button>
        ))}
      </div>
      {hasCrop && (
        <Button size="xs" variant="ghost-muted" className="self-start" onClick={onClear}>
          Clear crop
        </Button>
      )}
    </>
  );
}

function ToolGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <fieldset className="flex min-w-0 flex-col gap-2">
      <legend className="mb-2 font-medium text-muted-foreground text-xs">{label}</legend>
      {children}
    </fieldset>
  );
}

/** Colour swatches, plus the system picker for any other colour. */
function Swatches({ label, colors, value, onChange }: {
  label: string;
  colors: readonly string[];
  value: string;
  onChange: (color: string) => void;
}) {
  const custom = !colors.some((color) => color.toUpperCase() === value.toUpperCase());
  return (
    <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={label}>
      {colors.map((color) => {
        const selected = color.toUpperCase() === value.toUpperCase();
        return (
          <button
            key={color}
            type="button"
            aria-label={`${label} ${color}`}
            aria-pressed={selected}
            title={color}
            className={cn("size-6 rounded-md border border-border focus-visible:outline-2 focus-visible:outline-ring",
              selected && "ring-2 ring-foreground ring-offset-1 ring-offset-background")}
            style={{ backgroundColor: color }}
            onClick={() => onChange(color)}
          />
        );
      })}
      <label className={cn("relative size-6 overflow-hidden rounded-md border border-border", custom && "ring-2 ring-foreground ring-offset-1 ring-offset-background")}
        title="Any colour">
        <span className="sr-only">{`${label}: any colour`}</span>
        <PlusIcon aria-hidden className="pointer-events-none absolute inset-0 z-10 m-auto size-3.5 rounded-sm bg-background/80 text-foreground" />
        <input type="color" className="absolute -inset-2 size-10 cursor-pointer" value={isHexColor(value) ? value.toLowerCase() : undefined}
          onChange={(event) => onChange(event.target.value.toUpperCase())} />
      </label>
    </div>
  );
}

function AdjustTools({ edits, onChange }: { edits: PhotoEdits; onChange: (update: (current: PhotoEdits) => PhotoEdits) => void }) {
  const border = edits.border ?? null;
  return (
    <div className="flex flex-col gap-5">
      <ToolGroup label="Filter">
        <div className="grid grid-cols-3 gap-1.5">
          <Button size="xs" variant={!edits.filter ? "default" : "outline"} aria-pressed={!edits.filter}
            onClick={() => onChange((current) => ({ ...current, filter: null }))}>
            None
          </Button>
          {PHOTO_FILTERS.map((option) => (
            <Button key={option.id} size="xs" variant={edits.filter === option.id ? "default" : "outline"}
              aria-pressed={edits.filter === option.id}
              onClick={() => onChange((current) => ({ ...current, filter: option.id }))}>
              {option.label}
            </Button>
          ))}
        </div>
      </ToolGroup>
      <ToolGroup label="Flip">
        <div className="flex gap-1.5">
          <Button size="xs" variant={edits.flip_horizontal ? "default" : "outline"} aria-pressed={Boolean(edits.flip_horizontal)}
            onClick={() => onChange((current) => flipEdits(current, "horizontal"))}>
            <FlipHorizontal2Icon />
            Horizontal
          </Button>
          <Button size="xs" variant={edits.flip_vertical ? "default" : "outline"} aria-pressed={Boolean(edits.flip_vertical)}
            onClick={() => onChange((current) => flipEdits(current, "vertical"))}>
            <FlipVertical2Icon />
            Vertical
          </Button>
        </div>
      </ToolGroup>
      <ToolGroup label="Border">
        <div className="flex items-center gap-3">
          <input
            type="range"
            aria-label="Border width"
            min={0}
            max={20}
            step={1}
            value={Math.round((border?.width ?? 0) * 100)}
            className="min-w-0 flex-1 accent-foreground"
            onChange={(event) => {
              const percent = Number(event.target.value);
              onChange((current) => ({
                ...current,
                border: percent ? { width: percent / 100, color: current.border?.color ?? DEFAULT_BORDER_COLOR } : null,
              }));
            }}
          />
          <span className="w-10 text-right text-muted-foreground text-xs tabular-nums">{Math.round((border?.width ?? 0) * 100)}%</span>
        </div>
        {border ? (
          <Swatches label="Border colour" colors={BORDER_COLORS} value={border.color ?? DEFAULT_BORDER_COLOR}
            onChange={(color) => onChange((current) => ({ ...current, border: current.border ? { ...current.border, color } : null }))} />
        ) : null}
      </ToolGroup>
    </div>
  );
}

const MARK_LABELS: Record<MarkTool, string> = Object.fromEntries(MARK_TOOLS.map((tool) => [tool.id, tool.label])) as Record<MarkTool, string>;

/** What the next mark is drawn with. */
interface MarkPen {
  tool: MarkTool;
  color: string;
  text: string;
}

function MarkupList({ edits, pen, onPen, onChange }: {
  edits: PhotoEdits;
  pen: MarkPen;
  onPen: (pen: MarkPen) => void;
  onChange: (update: (current: PhotoEdits) => PhotoEdits) => void;
}) {
  const { tool, color, text } = pen;
  const setTool = (next: MarkTool) => onPen({ ...pen, tool: next });
  const setColor = (next: string) => onPen({ ...pen, color: next });
  const setText = (next: string) => onPen({ ...pen, text: next });
  const annotations = edits.annotations ?? [];
  const redactions = edits.redactions ?? [];
  const redacting = tool === "pixelate" || tool === "blur";
  return (
    <div className="flex flex-col gap-5">
      <ToolGroup label="Tool">
        <div className="grid grid-cols-3 gap-1.5">
          {MARK_TOOLS.map((option) => (
            <Button key={option.id} size="xs" variant={tool === option.id ? "default" : "outline"} aria-pressed={tool === option.id}
              onClick={() => setTool(option.id)}>
              {option.label}
            </Button>
          ))}
        </div>
        <p className="text-muted-foreground text-xs">
          {redacting ? "Drag over a name, address or barcode to hide it."
            : tool === "text" ? "Type a label, then tap where it goes."
              : "Drag on the photo to draw."}
        </p>
      </ToolGroup>
      {tool === "text" ? (
        <label className="flex flex-col gap-1.5 font-medium text-muted-foreground text-xs">
          Label
          <Input size="sm" value={text} maxLength={60} placeholder="Small stain" onChange={(event) => setText(event.target.value)} />
        </label>
      ) : null}
      {!redacting ? (
        <ToolGroup label="Colour">
          <Swatches label="Mark colour" colors={MARK_COLORS} value={color} onChange={setColor} />
        </ToolGroup>
      ) : null}
      {annotations.length || redactions.length ? (
        <ToolGroup label="On this photo">
          <ul className="flex flex-col divide-y divide-border/60 rounded-lg border border-border/60">
            {annotations.map((mark, position) => (
              <MarkRow key={`a${position}`} label={mark.kind === "text" ? `Text: ${mark.text ?? ""}` : `${MARK_LABELS[mark.kind]} ${position + 1}`}
                onRemove={() => onChange((current) => ({ ...current, annotations: (current.annotations ?? []).filter((_, i) => i !== position) }))} />
            ))}
            {redactions.map((box, position) => (
              <MarkRow key={`r${position}`} label={`${MARK_LABELS[box.style ?? "pixelate"]} ${position + 1}`}
                onRemove={() => onChange((current) => ({ ...current, redactions: (current.redactions ?? []).filter((_, i) => i !== position) }))} />
            ))}
          </ul>
          <Button size="xs" variant="ghost-muted" className="self-start"
            onClick={() => onChange((current) => ({ ...current, annotations: [], redactions: [] }))}>
            Clear all marks
          </Button>
        </ToolGroup>
      ) : null}
    </div>
  );
}

function MarkRow({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <li className="flex items-center gap-2 px-3 py-1.5 text-sm">
      <span className="min-w-0 flex-1 truncate">{label}</span>
      <Button size="icon-xs" variant="ghost" aria-label={`Remove ${label}`} onClick={onRemove}>
        <Trash2Icon />
      </Button>
    </li>
  );
}

/** Marks drawn live over the finished frame; a drag (or a tap, for text) adds one. */
function MarkupOverlay({ edits, pen, onChange }: {
  edits: PhotoEdits;
  pen: MarkPen;
  onChange: (update: (current: PhotoEdits) => PhotoEdits) => void;
}) {
  const frameRef = useRef<HTMLDivElement>(null);
  const [draft, setDraft] = useState<{ x1: number; y1: number; x2: number; y2: number } | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const at = (event: ReactPointerEvent<HTMLElement>) => {
    const rect = frameRef.current?.getBoundingClientRect();
    if (!rect?.width || !rect.height) return null;
    return { x: (event.clientX - rect.left) / rect.width, y: (event.clientY - rect.top) / rect.height };
  };
  const finish = () => {
    if (!draft) return;
    const mark = markFromDrag(pen.tool, draft.x1, draft.y1, draft.x2, draft.y2, pen.color, pen.text);
    setDraft(null);
    if (!mark) {
      setHint(pen.tool === "text" ? "Type a label first." : null);
      return;
    }
    const list = "annotation" in mark ? edits.annotations ?? [] : edits.redactions ?? [];
    if (list.length >= MAX_MARKS) {
      setHint("annotation" in mark ? `A photo takes ${MAX_MARKS} marks. Remove one to add another.`
        : `A photo takes ${MAX_MARKS} hidden areas. Remove one to hide another.`);
      return;
    }
    setHint(null);
    if ("annotation" in mark) {
      onChange((current) => ({ ...current, annotations: [...(current.annotations ?? []), mark.annotation] }));
    } else {
      onChange((current) => ({ ...current, redactions: [...(current.redactions ?? []), mark.redaction] }));
    }
  };
  const preview = draft ? markFromDrag(pen.tool, draft.x1, draft.y1, draft.x2, draft.y2, pen.color, pen.text || " ") : null;
  return (
    <div
      ref={frameRef}
      className="absolute inset-0 cursor-crosshair touch-none"
      data-markup-frame=""
      onPointerDown={(event) => {
        const point = at(event);
        if (!point) return;
        event.currentTarget.setPointerCapture(event.pointerId);
        setDraft({ x1: point.x, y1: point.y, x2: point.x, y2: point.y });
      }}
      onPointerMove={(event) => {
        const point = at(event);
        if (draft && point) setDraft({ ...draft, x2: point.x, y2: point.y });
      }}
      onPointerUp={finish}
      onPointerCancel={() => setDraft(null)}
    >
      {(edits.redactions ?? []).map((box, position) => <RedactionBox key={`r${position}`} box={box} />)}
      {preview && "redaction" in preview ? <RedactionBox box={preview.redaction} /> : null}
      <svg className="pointer-events-none absolute inset-0 size-full overflow-visible" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden>
        {[...(edits.annotations ?? []), ...(preview && "annotation" in preview ? [preview.annotation] : [])]
          .map((mark, position) => <AnnotationShape key={position} mark={mark} />)}
      </svg>
      {(edits.annotations ?? []).filter((mark) => mark.kind === "text").map((mark, position) => (
        <span key={`t${position}`} className="pointer-events-none absolute whitespace-nowrap font-medium text-sm leading-none"
          style={{ left: `${mark.x1 * 100}%`, top: `${mark.y1 * 100}%`, color: mark.color }}>
          {mark.text}
        </span>
      ))}
      {hint ? <span role="status" className="absolute bottom-2 left-2 rounded-md bg-black/60 px-2 py-1 text-white/80 text-xs">{hint}</span> : null}
    </div>
  );
}

function RedactionBox({ box }: { box: Redaction }) {
  return (
    <span
      className="pointer-events-none absolute border border-white/70 border-dashed backdrop-blur-md"
      style={{ left: `${box.x * 100}%`, top: `${box.y * 100}%`, width: `${box.width * 100}%`, height: `${box.height * 100}%` }}
    />
  );
}

function AnnotationShape({ mark }: { mark: Annotation }) {
  const [x1, y1, x2, y2] = [mark.x1 * 100, mark.y1 * 100, mark.x2 * 100, mark.y2 * 100];
  const stroke = { stroke: mark.color, strokeWidth: 2, vectorEffect: "non-scaling-stroke" as const, fill: "none" };
  if (mark.kind === "arrow") {
    const angle = Math.atan2(y2 - y1, x2 - x1);
    const head = 4;
    const points = [0.45, -0.45].map((turn) => `${x2 - head * Math.cos(angle + turn)},${y2 - head * Math.sin(angle + turn)}`);
    return (
      <g>
        <line x1={x1} y1={y1} x2={x2} y2={y2} {...stroke} />
        <polygon points={`${x2},${y2} ${points.join(" ")}`} fill={mark.color} />
      </g>
    );
  }
  if (mark.kind === "rectangle") {
    return <rect x={Math.min(x1, x2)} y={Math.min(y1, y2)} width={Math.abs(x2 - x1)} height={Math.abs(y2 - y1)} {...stroke} />;
  }
  if (mark.kind === "ellipse") {
    return <ellipse cx={(x1 + x2) / 2} cy={(y1 + y2) / 2} rx={Math.abs(x2 - x1) / 2} ry={Math.abs(y2 - y1) / 2} {...stroke} />;
  }
  return null;
}

type Effect = "none" | "ghost_mannequin" | "flat_lay" | "virtual_model";
const AUTO = { value: "auto", label: "Auto" } as const;
const titleCase = (value: string) => value.charAt(0).toUpperCase() + value.slice(1);
const MODEL_OPTIONS: SettingsSelectOption<NonNullable<VirtualModel["model"]>>[] = [AUTO,
  ...(["avery", "sam", "taylor", "kendall", "jordan", "casey", "maya", "reece", "lena", "julia", "jackson", "sophia", "emma", "ava", "zoe", "fiona"] as const)
    .map((value) => ({ value, label: titleCase(value) }))];
const SCENE_OPTIONS: SettingsSelectOption<NonNullable<VirtualModel["scene"]>>[] = [AUTO,
  { value: "studio", label: "Studio" }, { value: "coloredstudio", label: "Coloured studio" },
  { value: "concretestudio", label: "Concrete studio" }, { value: "street", label: "Street" },
  { value: "cafe", label: "Café" }, { value: "bedroom", label: "Bedroom" }, { value: "library", label: "Library" },
  { value: "businessdistrict", label: "Business district" }, { value: "factory", label: "Factory" },
  { value: "beach", label: "Beach" }, { value: "pool", label: "Pool" }, { value: "tropical", label: "Tropical" },
  { value: "sunset", label: "Sunset" }, { value: "goldenlight", label: "Golden light" }, { value: "nightlights", label: "Night lights" },
  { value: "forest", label: "Forest" }, { value: "flowers", label: "Flowers" }, { value: "countryside", label: "Countryside" },
  { value: "mountain", label: "Mountain" }, { value: "desert", label: "Desert" }, { value: "latincity", label: "Latin city" },
  { value: "asiancity", label: "Asian city" }];
const POSE_OPTIONS: SettingsSelectOption<NonNullable<VirtualModel["pose"]>>[] = [AUTO,
  { value: "standing", label: "Standing" }, { value: "34turn", label: "Three-quarter turn" },
  { value: "powerstance", label: "Power stance" }, { value: "walkingforward", label: "Walking forward" },
  { value: "handinpocket", label: "Hand in pocket" }, { value: "crossedarms", label: "Crossed arms" },
  { value: "back", label: "Back" }, { value: "overtheshoulder", label: "Over the shoulder" },
  { value: "seated", label: "Seated" }, { value: "adjustingclothing", label: "Adjusting clothing" },
  { value: "playfulspin", label: "Playful spin" }];

/** The AI Studio choices, as the seller picks them; ``normalizeStudio`` turns them into a valid request. */
interface StudioChoice {
  removeBackground: boolean;
  background: string;
  effect: Effect;
  shadow: boolean;
  model: VirtualModel;
}

export function studioRequest(choice: StudioChoice): StudioOptions {
  if (choice.effect === "virtual_model") return { virtual_model: choice.model };
  return normalizeStudio({
    remove_background: choice.removeBackground,
    background: choice.background,
    ghost_mannequin: choice.effect === "ghost_mannequin",
    flat_lay: choice.effect === "flat_lay",
    shadow: choice.shadow,
  });
}

const MAX_STUDIO_PHOTOS = 20;

function StudioTools({ convId, photo, photos, unsaved, onSaved, onRunning, onRebase, onDone }: {
  convId: string;
  photo: ListingPhoto;
  photos: ListingPhoto[];
  unsaved: boolean;
  onSaved: (photo: ListingPhoto) => void;
  onRunning: (running: boolean) => void;
  /** The open photo changed under the editor; its recipe starts from the result. */
  onRebase: (edits: PhotoEdits | null | undefined) => void;
  onDone: () => void;
}) {
  const mounted = useRef(true);
  useEffect(() => {
    // Set on every mount: StrictMode mounts, unmounts and mounts again in development.
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const { data: key } = useQuery({ queryKey: ["settings-photoroom"], queryFn: api.settings.photoroom, staleTime: 60_000 });
  const [choice, setChoice] = useState<StudioChoice>({
    removeBackground: true, background: DEFAULT_BACKDROP, effect: "none", shadow: false,
    model: { model: "auto", scene: "auto", pose: "auto" },
  });
  const [selected, setSelected] = useState<Set<string>>(() => new Set([photo.id]));
  const [running, setRunning] = useState(false);
  const [failures, setFailures] = useState<{ photo_id: string; detail: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const options = studioRequest(choice);
  const problem = studioProblem(options);
  const modelScene = choice.effect === "virtual_model";
  const cutsOut = choice.effect === "ghost_mannequin" || choice.effect === "flat_lay" || choice.shadow;
  const update = (change: Partial<StudioChoice>) => setChoice((current) => ({ ...current, ...change }));
  const ids = photos.filter((item) => selected.has(item.id)).map((item) => item.id);

  const apply = async () => {
    setRunning(true);
    onRunning(true);
    setError(null);
    setFailures([]);
    try {
      const result = await api.conversations.photoStudio(convId, ids, options);
      // The listing's photos update even if this editor has gone.
      result.photos.forEach(onSaved);
      if (!mounted.current) return;
      const open = result.photos.find((item) => item.id === photo.id);
      if (open) onRebase(open.edits);
      if (result.errors.length) {
        setFailures(result.errors);
        // Trying again sends only the photos that failed, so none is charged twice.
        setSelected(new Set(result.errors.map((failure) => failure.photo_id)));
      } else onDone();
    } catch (err) {
      if (mounted.current) setError(err instanceof Error ? err.message : "AI Studio failed");
    } finally {
      if (mounted.current) setRunning(false);
      onRunning(false);
    }
  };

  const unavailable = key && !key.available;
  return (
    <div className="flex flex-col gap-5">
      {unavailable ? (
        <p className="text-muted-foreground text-xs">Add a Photoroom API key in Settings → Providers to use AI Studio.</p>
      ) : null}
      <ToolGroup label="Background">
        <label className="flex items-center gap-2 text-sm">
          <Checkbox checked={choice.removeBackground || cutsOut} disabled={modelScene || cutsOut}
            onCheckedChange={(checked) => update({ removeBackground: checked === true })} />
          Remove background
        </label>
        {(choice.removeBackground || cutsOut) && !modelScene ? (
          <Swatches label="Background colour" colors={BACKDROP_COLORS} value={choice.background}
            onChange={(background) => update({ background })} />
        ) : null}
      </ToolGroup>
      <ToolGroup label="AI restyle">
        {([
          ["ghost_mannequin", "Ghost mannequin", "Shows clothing as if worn, with no mannequin."],
          ["flat_lay", "Flat lay", "A styled overhead layout."],
          ["virtual_model", "Virtual model", "A model wears the item in a scene of its own."],
        ] as const).map(([effect, label, description]) => (
          <label key={effect} className="flex items-start gap-2 text-sm">
            <Checkbox className="mt-0.5" checked={choice.effect === effect}
              onCheckedChange={(checked) => update(checked === true
                ? { effect, ...(effect === "virtual_model" ? { shadow: false, removeBackground: false } : {}) }
                : { effect: "none" })} />
            <span className="flex flex-col">
              {label}
              <span className="text-muted-foreground text-xs">{description}</span>
            </span>
          </label>
        ))}
        <label className="flex items-start gap-2 text-sm">
          <Checkbox className="mt-0.5" checked={choice.shadow} disabled={modelScene}
            onCheckedChange={(checked) => update({ shadow: checked === true })} />
          <span className="flex flex-col">
            Natural shadow
            <span className="text-muted-foreground text-xs">A soft shadow under the item.</span>
          </span>
        </label>
        {modelScene ? (
          <div className="grid gap-2">
            <label className="flex items-center justify-between gap-2 text-xs">
              Model
              <SettingsSelect aria-label="Virtual model" className="w-40" value={choice.model.model ?? "auto"} options={MODEL_OPTIONS}
                onChange={(model) => update({ model: { ...choice.model, model } })} />
            </label>
            <label className="flex items-center justify-between gap-2 text-xs">
              Scene
              <SettingsSelect aria-label="Scene" className="w-40" value={choice.model.scene ?? "auto"} options={SCENE_OPTIONS}
                onChange={(scene) => update({ model: { ...choice.model, scene } })} />
            </label>
            <label className="flex items-center justify-between gap-2 text-xs">
              Pose
              <SettingsSelect aria-label="Pose" className="w-40" value={choice.model.pose ?? "auto"} options={POSE_OPTIONS}
                onChange={(pose) => update({ model: { ...choice.model, pose } })} />
            </label>
          </div>
        ) : null}
      </ToolGroup>
      {photos.length > 1 ? (
        <ToolGroup label="Apply to">
          <div className="grid grid-cols-4 gap-1.5">
            {photos.map((item, position) => {
              const on = selected.has(item.id);
              return (
                <label key={item.id} className={cn("relative aspect-square overflow-hidden rounded-md border", on ? "border-foreground" : "border-border")}>
                  <StudioImage src={photoThumbUrl(item)} alt={`Photo ${position + 1}`} className="size-full object-cover" />
                  <Checkbox className="absolute top-1 left-1 bg-background" checked={on} aria-label={`Apply to photo ${position + 1}`}
                    disabled={running || (!on && selected.size >= MAX_STUDIO_PHOTOS)}
                    onCheckedChange={(checked) => setSelected((current) => {
                      const next = new Set(current);
                      if (checked === true) next.add(item.id);
                      else next.delete(item.id);
                      return next;
                    })} />
                </label>
              );
            })}
          </div>
          <Button size="xs" variant="ghost-muted" className="self-start"
            disabled={running}
            onClick={() => setSelected(selected.size === Math.min(photos.length, MAX_STUDIO_PHOTOS) ? new Set([photo.id])
              : new Set([photo.id, ...photos.map((item) => item.id).filter((id) => id !== photo.id)].slice(0, MAX_STUDIO_PHOTOS)))}>
            {selected.size === Math.min(photos.length, MAX_STUDIO_PHOTOS) ? "Only this photo"
              : photos.length > MAX_STUDIO_PHOTOS ? `Select ${MAX_STUDIO_PHOTOS} photos` : "Select all photos"}
          </Button>
          {photos.length > MAX_STUDIO_PHOTOS ? (
            <p className="text-muted-foreground text-xs">AI Studio edits up to {MAX_STUDIO_PHOTOS} photos at a time.</p>
          ) : null}
        </ToolGroup>
      ) : null}
      {problem && choice.effect !== "none" ? <p className="text-muted-foreground text-xs">{problem}</p> : null}
      {unsaved ? <p className="text-muted-foreground text-xs">Save or discard your changes first.</p> : null}
      <Button size="sm" onClick={() => void apply()}
        disabled={running || unsaved || Boolean(problem) || !ids.length || ids.length > MAX_STUDIO_PHOTOS || Boolean(unavailable)}>
        {running ? <Spinner className="size-3" /> : null}
        {running ? "Photoroom is working…" : ids.length > 1 ? `Apply to ${ids.length} photos` : "Apply"}
      </Button>
      <p className="text-muted-foreground text-xs">Revert to original brings back your upload.</p>
      {error ? <p className="text-destructive-foreground text-xs" role="alert">{error}</p> : null}
      {failures.length ? (
        <ul className="flex flex-col gap-1 text-destructive-foreground text-xs" role="alert">
          {failures.map((failure) => (
            <li key={failure.photo_id}>Photo {photos.findIndex((item) => item.id === failure.photo_id) + 1}: {failure.detail}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

const HANDLES: { id: Exclude<CropHandle, "move">; label: string; className: string }[] = [
  { id: "nw", label: "Resize from top left", className: "-top-1.5 -left-1.5 cursor-nwse-resize" },
  { id: "ne", label: "Resize from top right", className: "-top-1.5 -right-1.5 cursor-nesw-resize" },
  { id: "sw", label: "Resize from bottom left", className: "-bottom-1.5 -left-1.5 cursor-nesw-resize" },
  { id: "se", label: "Resize from bottom right", className: "-bottom-1.5 -right-1.5 cursor-nwse-resize" },
];

const ARROW_OFFSETS = new Map<string, [number, number]>([
  ["ArrowLeft", [-1, 0]], ["ArrowRight", [1, 0]], ["ArrowUp", [0, -1]], ["ArrowDown", [0, 1]],
]);

/** The crop box over the whole frame. Arrow keys work on the box and its handles. */
function CropOverlay({ box, aspect, frameAspect, onChange }: {
  box: CropBox;
  aspect: number | null;
  frameAspect: number;
  onChange: (box: CropBox) => void;
}) {
  const frameRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{ handle: CropHandle; x: number; y: number; box: CropBox } | null>(null);

  const start = (handle: CropHandle) => (event: ReactPointerEvent<HTMLElement>) => {
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { handle, x: event.clientX, y: event.clientY, box };
  };
  const move = (event: ReactPointerEvent<HTMLElement>) => {
    const current = drag.current;
    const rect = frameRef.current?.getBoundingClientRect();
    if (!current || !rect?.width || !rect.height) return;
    const dx = (event.clientX - current.x) / rect.width;
    const dy = (event.clientY - current.y) / rect.height;
    onChange(dragCrop(current.box, current.handle, dx, dy, aspect, frameAspect));
  };
  const end = () => {
    drag.current = null;
  };

  const nudge = (handle: CropHandle) => (event: KeyboardEvent<HTMLButtonElement>) => {
    const offset = ARROW_OFFSETS.get(event.key);
    if (!offset) return;
    event.preventDefault();
    const step = event.shiftKey ? 0.05 : 0.01;
    const [horizontal, dy] = offset;
    let dx = horizontal;
    if (aspect && handle !== "move" && dx === 0) {
      // Preset resizing uses horizontal movement to derive both dimensions.
      const sameDirection = handle === "nw" || handle === "se";
      dx = dy * (sameDirection ? 1 : -1) * aspect / frameAspect;
    }
    onChange(dragCrop(box, handle, dx * step, dy * step, aspect, frameAspect));
  };

  return (
    <div ref={frameRef} className="absolute inset-0" data-crop-frame="">
      <div
        className="absolute border border-white shadow-[0_0_0_9999px_rgba(0,0,0,0.55)]"
        style={{
          left: `${box.x * 100}%`,
          top: `${box.y * 100}%`,
          width: `${box.width * 100}%`,
          height: `${box.height * 100}%`,
        }}
      >
        <button
          type="button"
          aria-label="Move crop with arrow keys"
          className="absolute inset-0 touch-none cursor-move focus-visible:outline-2 focus-visible:outline-ring"
          onKeyDown={nudge("move")}
          onPointerDown={start("move")}
          onPointerMove={move}
          onPointerUp={end}
          onPointerCancel={end}
        />
        {/* Rule-of-thirds guides, as in Photos and Photoroom. */}
        <span className="pointer-events-none absolute inset-y-0 left-1/3 border-white/40 border-l" />
        <span className="pointer-events-none absolute inset-y-0 left-2/3 border-white/40 border-l" />
        <span className="pointer-events-none absolute inset-x-0 top-1/3 border-white/40 border-t" />
        <span className="pointer-events-none absolute inset-x-0 top-2/3 border-white/40 border-t" />
        {HANDLES.map((handle) => (
          <button
            key={handle.id}
            type="button"
            aria-label={handle.label}
            className={cn("absolute size-3 touch-none rounded-sm border border-black/30 bg-white focus-visible:outline-2 focus-visible:outline-ring", handle.className)}
            onKeyDown={nudge(handle.id)}
            onPointerDown={start(handle.id)}
            onPointerMove={move}
            onPointerUp={end}
            onPointerCancel={end}
          />
        ))}
      </div>
    </div>
  );
}
