/**
 * Photo editor: crop, turn and straighten, tone, and background removal.
 *
 * Laid out like Photoroom's and Poshmark's editors: the photo fills the stage,
 * one tool set shows at a time, and nothing changes until Save. Edits are a
 * recipe the server renders from the kept original, so Reset always restores
 * the upload and the preview is the same render the marketplaces get.
 */
import { CropIcon, EraserIcon, RotateCcwIcon, RotateCwIcon, SunIcon, Undo2Icon, XIcon } from "lucide-react";
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { api } from "../api/client";
import type { CropBox, Photo, PhotoEdits } from "../api/types";
import { cn } from "../lib/utils";
import {
  CROP_ASPECTS,
  NO_EDITS,
  centeredCrop,
  dragCrop,
  sameEdits,
  turnEdits,
  withDefaults,
  type CropHandle,
} from "../photoEdits";
import { Button } from "./ui/button";
import { Dialog, DialogPopup, DialogTitle } from "./ui/dialog";
import { Spinner } from "./ui/spinner";
import { Toggle, ToggleGroup } from "./ui/toggle-group";

type Tool = "crop" | "adjust" | "background";

const PREVIEW_DELAY_MS = 180;
const FULL_FRAME: CropBox = { x: 0, y: 0, width: 1, height: 1 };
const BACKGROUNDS: { color: string; label: string }[] = [
  { color: "#ffffff", label: "White" },
  { color: "#f4f4f5", label: "Light grey" },
  { color: "#e8e0d5", label: "Linen" },
  { color: "#000000", label: "Black" },
];

interface Props {
  convId: string;
  photo: Photo;
  index: number;
  onSaved: (photo: Photo) => void;
  onClose: () => void;
}

export function PhotoEditor({ convId, photo, index, onSaved, onClose }: Props) {
  const saved = withDefaults(photo.edits);
  const [edits, setEdits] = useState<PhotoEdits>(saved);
  const [tool, setTool] = useState<Tool>("crop");
  const [aspectId, setAspectId] = useState("free");
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [frameAspect, setFrameAspect] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const showWholeFrame = tool === "crop";

  useEffect(() => {
    const controller = new AbortController();
    let url: string | null = null;
    const timer = window.setTimeout(() => {
      setLoading(true);
      api.conversations
        .previewPhotoEdits(convId, photo.id, edits, !showWholeFrame, controller.signal)
        .then((blob) => {
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
      window.clearTimeout(timer);
      controller.abort();
      // Revoked once the next preview has replaced it.
      if (url) window.setTimeout(() => URL.revokeObjectURL(url as string), 1000);
    };
  }, [convId, photo.id, edits, showWholeFrame]);

  const update = (patch: Partial<PhotoEdits>) => setEdits((current) => ({ ...current, ...patch }));
  const aspect = CROP_ASPECTS.find((option) => option.id === aspectId)?.aspect ?? null;

  const chooseAspect = (id: string) => {
    setAspectId(id);
    const next = CROP_ASPECTS.find((option) => option.id === id)?.aspect ?? null;
    if (next && frameAspect) update({ crop: centeredCrop(next, frameAspect) });
  };

  const turn = (degrees: 90 | -90) => {
    setEdits((current) => {
      const turned = turnEdits(current, degrees);
      // A chosen shape stays that shape in the turned frame.
      if (aspect && frameAspect) return { ...turned, crop: centeredCrop(aspect, 1 / frameAspect) };
      return turned;
    });
  };

  const changed = !sameEdits(edits, saved);
  const edited = Boolean(photo.edits);

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

  const reset = async () => {
    if (!edited) {
      setEdits(NO_EDITS);
      setAspectId("free");
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      const restored = await api.conversations.resetPhoto(convId, photo.id);
      onSaved(restored);
      setEdits(NO_EDITS);
      setAspectId("free");
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Reset failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => { if (!open && !saving) onClose(); }}>
      <DialogPopup
        showCloseButton={false}
        bottomStickOnMobile={false}
        className="row-start-1 flex h-[min(92vh,860px)] w-[min(96vw,1180px)] max-w-none flex-col overflow-hidden"
        aria-label={`Edit photo ${index + 1}`}
      >
        <div className="flex items-center gap-3 border-b px-4 py-3">
          <DialogTitle className="text-base">Edit photo {index + 1}</DialogTitle>
          <span className="text-muted-foreground text-xs">The original is kept. Reset brings it back.</span>
          <Button className="ms-auto" size="icon-sm" variant="ghost" aria-label="Close" onClick={onClose} disabled={saving}>
            <XIcon />
          </Button>
        </div>
        <div className="grid min-h-0 flex-1 grid-rows-[1fr_auto] md:grid-cols-[1fr_300px] md:grid-rows-1">
          <div className="relative flex min-h-0 items-center justify-center overflow-hidden bg-neutral-900 p-6">
            {previewUrl ? (
              <div className="relative max-w-full leading-none">
                <img
                  src={previewUrl}
                  alt={`Photo ${index + 1} with edits`}
                  draggable={false}
                  className="block max-h-[calc(min(92vh,860px)-170px)] max-w-full select-none object-contain"
                  onLoad={(event) => {
                    if (showWholeFrame) {
                      const img = event.currentTarget;
                      if (img.naturalHeight) setFrameAspect(img.naturalWidth / img.naturalHeight);
                    }
                  }}
                />
                {showWholeFrame ? (
                  <CropOverlay
                    box={edits.crop ?? FULL_FRAME}
                    aspect={aspect}
                    frameAspect={frameAspect ?? 1}
                    onChange={(crop) => update({ crop })}
                  />
                ) : null}
              </div>
            ) : null}
            {loading ? (
              <span className="absolute top-3 right-3 inline-flex items-center gap-1.5 rounded-md bg-black/60 px-2 py-1 text-white/80 text-xs" role="status">
                <Spinner className="size-3" />
                {edits.background && tool === "background" ? "Cutting out the item…" : "Updating…"}
              </span>
            ) : null}
          </div>
          <div className="flex min-h-0 flex-col gap-4 overflow-y-auto border-t p-4 md:border-t-0 md:border-l">
            <ToggleGroup
              aria-label="Editing tool"
              className="w-full"
              value={[tool]}
              onValueChange={(next) => {
                const value = next[0] as Tool | undefined;
                if (value) setTool(value);
              }}
            >
              <Toggle className="flex-1" value="crop"><CropIcon />Crop</Toggle>
              <Toggle className="flex-1" value="adjust"><SunIcon />Adjust</Toggle>
              <Toggle className="flex-1" value="background"><EraserIcon />Background</Toggle>
            </ToggleGroup>

            {tool === "crop" ? (
              <div className="flex flex-col gap-4">
                <ToolSection title="Shape">
                  <div className="flex flex-wrap gap-1.5">
                    {CROP_ASPECTS.map((option) => (
                      <Button
                        key={option.id}
                        size="xs"
                        variant={aspectId === option.id ? "default" : "outline"}
                        aria-pressed={aspectId === option.id}
                        disabled={option.aspect !== null && !frameAspect}
                        onClick={() => chooseAspect(option.id)}
                      >
                        {option.label}
                      </Button>
                    ))}
                  </div>
                </ToolSection>
                <ToolSection title="Rotate">
                  <div className="flex gap-1.5">
                    <Button size="xs" variant="outline" onClick={() => turn(-90)}><RotateCcwIcon />Left</Button>
                    <Button size="xs" variant="outline" onClick={() => turn(90)}><RotateCwIcon />Right</Button>
                  </div>
                </ToolSection>
                <EditSlider
                  label="Straighten"
                  value={edits.straighten}
                  min={-45}
                  max={45}
                  step={0.5}
                  format={(value) => `${value > 0 ? "+" : ""}${value.toFixed(1)}°`}
                  onChange={(straighten) => update({ straighten })}
                />
                {edits.crop ? (
                  <Button size="xs" variant="ghost-muted" className="self-start" onClick={() => { setAspectId("free"); update({ crop: null }); }}>
                    Clear crop
                  </Button>
                ) : null}
              </div>
            ) : null}

            {tool === "adjust" ? (
              <div className="flex flex-col gap-4">
                <EditSlider
                  label="Exposure"
                  value={edits.exposure}
                  min={-2}
                  max={2}
                  step={0.05}
                  format={(value) => `${value > 0 ? "+" : ""}${value.toFixed(2)}`}
                  onChange={(exposure) => update({ exposure })}
                />
                <EditSlider
                  label="Brightness"
                  value={edits.brightness}
                  min={-100}
                  max={100}
                  step={1}
                  format={(value) => `${value > 0 ? "+" : ""}${value}`}
                  onChange={(brightness) => update({ brightness })}
                />
                <EditSlider
                  label="Contrast"
                  value={edits.contrast}
                  min={-100}
                  max={100}
                  step={1}
                  format={(value) => `${value > 0 ? "+" : ""}${value}`}
                  onChange={(contrast) => update({ contrast })}
                />
              </div>
            ) : null}

            {tool === "background" ? (
              <div className="flex flex-col gap-3">
                <p className="text-muted-foreground text-xs">
                  Cuts out the item and puts a plain colour behind it. It runs on this Mac; the photo is not uploaded anywhere.
                </p>
                <ToolSection title="Behind the item">
                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      size="xs"
                      variant={edits.background === null ? "default" : "outline"}
                      aria-pressed={edits.background === null}
                      onClick={() => update({ background: null })}
                    >
                      Original
                    </Button>
                    {BACKGROUNDS.map((option) => (
                      <button
                        key={option.color}
                        type="button"
                        title={option.label}
                        aria-label={`${option.label} background`}
                        aria-pressed={edits.background === option.color}
                        className={cn(
                          "size-7 rounded-full border border-border shadow-xs outline-none focus-visible:ring-2 focus-visible:ring-ring",
                          edits.background === option.color && "ring-2 ring-primary ring-offset-2 ring-offset-background",
                        )}
                        style={{ backgroundColor: option.color }}
                        onClick={() => update({ background: option.color })}
                      />
                    ))}
                    <label
                      className={cn(
                        "relative size-7 cursor-pointer overflow-hidden rounded-full border border-border shadow-xs",
                        edits.background && !BACKGROUNDS.some((option) => option.color === edits.background) &&
                          "ring-2 ring-primary ring-offset-2 ring-offset-background",
                      )}
                      title="Choose a colour"
                      style={{ background: "conic-gradient(red, yellow, lime, aqua, blue, magenta, red)" }}
                    >
                      <span className="sr-only">Choose a background colour</span>
                      <input
                        type="color"
                        className="absolute inset-0 cursor-pointer opacity-0"
                        value={edits.background ?? "#ffffff"}
                        onChange={(event) => update({ background: event.target.value.toLowerCase() })}
                      />
                    </label>
                  </div>
                </ToolSection>
              </div>
            ) : null}

            {previewError ? <p className="text-destructive-foreground text-xs" role="alert">{previewError}</p> : null}
            {saveError ? <p className="text-destructive-foreground text-xs" role="alert">{saveError}</p> : null}
          </div>
        </div>
        <div className="flex items-center gap-2 border-t bg-muted/72 px-4 py-3">
          <Button
            variant="ghost"
            size="sm"
            disabled={saving || (!edited && sameEdits(edits, NO_EDITS))}
            onClick={() => void reset()}
            title={edited ? "Serve the original photo again" : "Clear these changes"}
          >
            <Undo2Icon />
            Reset to original
          </Button>
          <Button className="ms-auto" variant="outline" size="sm" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button size="sm" onClick={() => void save()} disabled={saving || !changed || Boolean(previewError)}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </div>
      </DialogPopup>
    </Dialog>
  );
}

function ToolSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <span className="font-medium text-muted-foreground text-xs">{title}</span>
      {children}
    </div>
  );
}

function EditSlider({ label, value, min, max, step, format, onChange }: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  format: (value: number) => string;
  onChange: (value: number) => void;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="flex items-center justify-between text-xs">
        <span className="font-medium text-muted-foreground">{label}</span>
        <button
          type="button"
          className="font-mono text-muted-foreground tabular-nums hover:text-foreground"
          title={`Reset ${label.toLowerCase()}`}
          onClick={(event) => {
            event.preventDefault();
            onChange(0);
          }}
        >
          {format(value)}
        </button>
      </span>
      <input
        type="range"
        className="w-full accent-primary"
        min={min}
        max={max}
        step={step}
        value={value}
        aria-label={label}
        onChange={(event) => onChange(Number(event.target.value))}
        onDoubleClick={() => onChange(0)}
      />
    </label>
  );
}

const HANDLES: { id: Exclude<CropHandle, "move">; className: string }[] = [
  { id: "nw", className: "-top-1.5 -left-1.5 cursor-nwse-resize" },
  { id: "ne", className: "-top-1.5 -right-1.5 cursor-nesw-resize" },
  { id: "sw", className: "-bottom-1.5 -left-1.5 cursor-nesw-resize" },
  { id: "se", className: "-bottom-1.5 -right-1.5 cursor-nwse-resize" },
];

/** The crop box over the whole frame: drag inside to move it, drag a corner to resize. */
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

  return (
    <div ref={frameRef} className="absolute inset-0" data-crop-frame="">
      <div
        role="presentation"
        className="absolute cursor-move border border-white shadow-[0_0_0_9999px_rgba(0,0,0,0.55)] touch-none"
        style={{
          left: `${box.x * 100}%`,
          top: `${box.y * 100}%`,
          width: `${box.width * 100}%`,
          height: `${box.height * 100}%`,
        }}
        onPointerDown={start("move")}
        onPointerMove={move}
        onPointerUp={end}
        onPointerCancel={end}
      >
        {/* Rule-of-thirds guides, as in Photos and Photoroom. */}
        <span className="pointer-events-none absolute inset-y-0 left-1/3 border-white/40 border-l" />
        <span className="pointer-events-none absolute inset-y-0 left-2/3 border-white/40 border-l" />
        <span className="pointer-events-none absolute inset-x-0 top-1/3 border-white/40 border-t" />
        <span className="pointer-events-none absolute inset-x-0 top-2/3 border-white/40 border-t" />
        {HANDLES.map((handle) => (
          <span
            key={handle.id}
            aria-hidden="true"
            className={cn("absolute size-3 touch-none rounded-sm border border-black/30 bg-white", handle.className)}
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
