import { PhotoLoadFailure, StudioImage } from "./StudioImage";
import { XIcon } from "lucide-react";
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { api } from "../api/client";
import type { CropBox, ListingPhoto } from "../api/types";
import { cn } from "../lib/utils";
import { CROP_ASPECTS, centeredCrop, dragCrop, type CropHandle } from "../photoEdits";
import { Button } from "./ui/button";
import { Dialog, DialogPopup, DialogTitle } from "./ui/dialog";
import { Spinner } from "./ui/spinner";

const FULL_FRAME: CropBox = { x: 0, y: 0, width: 1, height: 1 };
interface Props {
  convId: string;
  photo: ListingPhoto;
  index: number;
  onSaved: (photo: ListingPhoto) => void;
  onClose: () => void;
}

export function PhotoCropDialog({ convId, photo, index, onSaved, onClose }: Props) {
  const savedCrop = photo.edits?.crop ?? null;
  const [crop, setCrop] = useState(savedCrop);
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

  useEffect(() => {
    const controller = new AbortController();
    let url: string | null = null;
    api.conversations.previewPhotoEdits(convId, photo.id, photo.edits ?? {}, false, controller.signal)
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
    return () => {
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [convId, photo.id, photo.edits, previewAttempt]);

  const retry = () => {
    setPreviewError(null);
    setPreviewUrl(null);
    setFrameAspect(null);
    setLoading(true);
    setPreviewAttempt((count) => count + 1);
  };

  const aspect = CROP_ASPECTS.find((option) => option.id === aspectId)?.aspect ?? null;
  const chooseAspect = (id: string) => {
    setAspectId(id);
    const next = CROP_ASPECTS.find((option) => option.id === id)?.aspect;
    if (next && frameAspect) setCrop(centeredCrop(next, frameAspect));
  };
  const changed = JSON.stringify(crop) !== JSON.stringify(savedCrop);

  const save = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      onSaved(await api.conversations.editPhoto(convId, photo.id, { ...photo.edits, crop }));
      onClose();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Save failed");
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
        aria-label={`Crop photo ${index + 1}`}
      >
        <div className="flex items-center gap-3 border-b px-4 py-3">
          <DialogTitle className="text-base">Crop photo {index + 1}</DialogTitle>
          <Button className="ms-auto" size="icon-sm" variant="ghost" aria-label="Close" onClick={onClose} disabled={saving}>
            <XIcon />
          </Button>
        </div>
        <div className="grid min-h-0 flex-1 grid-rows-[1fr_auto] md:grid-cols-[1fr_300px] md:grid-rows-1">
          <div className="relative flex min-h-0 items-center justify-center overflow-hidden bg-neutral-900 p-6">
            {previewUrl && unreadablePreview === previewUrl ? (
              // The same file would fail again: Retry renders these edits anew.
              // No crop frame here, so nothing covers the button.
              <PhotoLoadFailure status={null} retry={retry} />
            ) : previewUrl ? (
              <div className="relative max-w-full leading-none">
                <StudioImage
                  src={previewUrl}
                  alt={`Photo ${index + 1} crop preview`}
                  draggable={false}
                  className="block max-h-[calc(min(92vh,860px)-170px)] max-w-full select-none object-contain"
                  onError={() => setUnreadablePreview(previewUrl)}
                  onLoad={(event) => {
                    const img = event.currentTarget;
                    if (img.naturalHeight) setFrameAspect(img.naturalWidth / img.naturalHeight);
                  }}
                />
                <CropOverlay
                  box={crop ?? FULL_FRAME}
                  aspect={aspect}
                  frameAspect={frameAspect ?? 1}
                  onChange={setCrop}
                />
              </div>
            ) : null}
            {loading ? (
              <span className="absolute top-3 right-3 inline-flex items-center gap-1.5 rounded-md bg-black/60 px-2 py-1 text-white/80 text-xs" role="status">
                <Spinner className="size-3" />
                Loading photo…
              </span>
            ) : null}
          </div>
          <div className="flex min-h-0 flex-col gap-4 overflow-y-auto border-t p-4 md:border-t-0 md:border-l">
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="Crop shape">
              {CROP_ASPECTS.map((option) => (
                <Button key={option.id} size="xs" variant={aspectId === option.id ? "default" : "outline"}
                  aria-pressed={aspectId === option.id} disabled={!frameAspect || loading}
                  onClick={() => chooseAspect(option.id)}>
                  {option.label}
                </Button>
              ))}
            </div>
            {crop && (
              <Button size="xs" variant="ghost-muted" className="self-start" onClick={() => { setAspectId("free"); setCrop(null); }}>
                Clear crop
              </Button>
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
          <Button className="ms-auto" variant="outline" size="sm" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button size="sm" onClick={() => void save()} disabled={saving || !changed || loading || Boolean(previewError) || unreadablePreview === previewUrl || !frameAspect}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </div>
      </DialogPopup>
    </Dialog>
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
