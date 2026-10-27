/**
 * Photo thumbnails and the enlarged photo view, shared by the photo tray and
 * the regenerate review. The lightbox follows T3 Code's ExpandedImageDialog
 * (MIT, (c) 2026 T3 Tools Inc.): a media dialog with corner close, side
 * navigation and a caption line under the image.
 */
import { ArrowLeftIcon, ArrowRightIcon, ChevronLeftIcon, ChevronRightIcon, PlusIcon, RotateCcwIcon, RotateCwIcon, SlidersHorizontalIcon, Trash2Icon, XIcon } from "lucide-react";
import { useState, type ComponentProps, type KeyboardEvent } from "react";
import { cn } from "../lib/utils";
import { Button } from "./ui/button";
import { Dialog, DialogPopup, DialogTitle } from "./ui/dialog";
import { Skeleton } from "./ui/skeleton";

const QUARTER_TURN_CLASS = ["", "rotate-90", "rotate-180", "-rotate-90"] as const;

/** An image that holds its box and shows a skeleton until it has loaded. */
export function LazyImage({ src, alt = "", className, imgClassName }: {
  src: string;
  alt?: string;
  className?: string;
  imgClassName?: string;
}) {
  const [loadedSrc, setLoadedSrc] = useState<string | null>(null);
  const loaded = loadedSrc === src;
  return (
    <span className={cn("relative block overflow-hidden", className)}>
      {!loaded ? <Skeleton className="absolute inset-0 rounded-none" aria-hidden="true" /> : null}
      <img
        src={src}
        alt={alt}
        loading="lazy"
        decoding="async"
        draggable={false}
        onLoad={() => setLoadedSrc(src)}
        onError={() => setLoadedSrc(src)}
        className={cn(
          "pointer-events-none size-full select-none object-cover transition-opacity duration-200 [-webkit-user-drag:none]",
          loaded ? "opacity-100" : "opacity-0",
          imgClassName,
        )}
      />
    </span>
  );
}

/** One 64px square thumbnail with its number, rotate and remove controls. */
export function PhotoThumb({
  url,
  index,
  reorderable,
  dragging,
  dropTarget,
  onOpen,
  onRotate,
  rotating,
  onRemove,
  removeLabel,
  removing,
  onAdd,
  addLabel,
  badge,
  numbered = true,
  controlsTabbable = true,
  controlsInViewerOnTouch = false,
  className,
  ...props
}: Omit<ComponentProps<"div">, "onClick"> & {
  url: string;
  index: number;
  reorderable?: boolean;
  dragging?: boolean;
  dropTarget?: boolean;
  onOpen: () => void;
  onRotate?: () => void;
  rotating?: boolean;
  onRemove?: () => void;
  removeLabel?: string;
  removing?: boolean;
  /** Puts a photo left off a marketplace form back on it. */
  onAdd?: () => void;
  addLabel?: string;
  /** A short note in the corner, such as a marketplace's limit leaving the photo off. */
  badge?: string;
  /** Show the photo's place in the send order; off for photos that are not sent. */
  numbered?: boolean;
  controlsTabbable?: boolean;
  /** On a touch screen, leave rotate and delete to the viewer a tap opens, where a stray tap cannot hit them. */
  controlsInViewerOnTouch?: boolean;
}) {
  const number = index + 1;
  const overlayTouch = controlsInViewerOnTouch ? "pointer-coarse:hidden" : "pointer-coarse:opacity-100";
  return (
    <div
      className={cn(
        "group/thumb relative size-16 shrink-0 select-none overflow-hidden rounded-lg border bg-muted transition-[opacity,box-shadow,border-color] hover:border-ring/60",
        reorderable && "cursor-grab",
        dragging && "cursor-grabbing opacity-40",
        dropTarget && "border-ring ring-2 ring-ring",
        className,
      )}
      {...props}
    >
      <div
        role="button"
        tabIndex={0}
        aria-label={`View photo ${number} enlarged`}
        className="size-full rounded-[inherit] outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
        onClick={onOpen}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onOpen();
          }
        }}
      >
        <LazyImage src={url} className="size-full" />
      </div>
      {numbered ? (
        <span className="pointer-events-none absolute bottom-0.5 left-0.5 rounded-sm bg-black/60 px-1 font-mono text-[9px] text-white/90 leading-3.5 tabular-nums">
          {String(number).padStart(2, "0")}
        </span>
      ) : null}
      {badge ? (
        <span className="pointer-events-none absolute right-0.5 bottom-0.5 rounded-sm bg-black/70 px-1 text-[9px] text-white leading-3.5">
          {badge}
        </span>
      ) : null}
      {onAdd ? (
        <Button
          variant="overlay"
          size="icon-micro"
          tabIndex={controlsTabbable ? undefined : -1}
          className="absolute right-0.5 bottom-0.5"
          aria-label={addLabel ?? `Add photo ${number}`}
          title={addLabel ?? "Add photo"}
          onClick={(event) => {
            event.stopPropagation();
            onAdd();
          }}
        >
          <PlusIcon />
        </Button>
      ) : null}
      {onRotate ? (
        <Button
          variant="overlay"
          size="icon-micro"
          tabIndex={controlsTabbable ? undefined : -1}
          className={cn("absolute top-0.5 left-0.5 opacity-0 transition-opacity focus-visible:opacity-100 group-hover/thumb:opacity-100", overlayTouch)}
          aria-label={`Rotate photo ${number} right`}
          title="Rotate right"
          disabled={rotating}
          onClick={(event) => {
            event.stopPropagation();
            onRotate();
          }}
        >
          <RotateCwIcon />
        </Button>
      ) : null}
      {onRemove ? (
        <Button
          variant="overlay"
          size="icon-micro"
          tabIndex={controlsTabbable ? undefined : -1}
          className={cn("absolute top-0.5 right-0.5 opacity-0 transition-opacity hover:text-white focus-visible:opacity-100 group-hover/thumb:opacity-100", overlayTouch)}
          aria-label={removeLabel ?? `Delete photo ${number}`}
          title={removeLabel ? "Remove photo" : "Delete photo"}
          disabled={removing}
          onClick={(event) => {
            event.stopPropagation();
            onRemove();
          }}
        >
          <XIcon />
        </Button>
      ) : null}
    </div>
  );
}

/** Horizontal, scrollable row of thumbnails. */
export function PhotoStrip({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("flex gap-1.5 overflow-x-auto pb-1", className)} {...props} />;
}

/** One photo enlarged: arrows step, the corner button closes, rotate turns it. */
export function PhotoLightbox({
  urls,
  index,
  turns = 0,
  rotating = false,
  onRotate,
  onEdit,
  edited = false,
  onStep,
  onMove,
  onRemove,
  onClose,
}: {
  urls: string[];
  index: number;
  /** Quarter turns to show a photo by when the turn is not in the file yet. */
  turns?: number;
  rotating?: boolean;
  onRotate: (degrees: 90 | -90) => void;
  /** Opens the photo editor for the photo shown. */
  onEdit?: () => void;
  /** The photo shown is an edit of a kept original. */
  edited?: boolean;
  onStep: (index: number) => void;
  /** Touch screens cannot drag thumbnails, so the viewer moves the photo instead. */
  onMove?: (delta: -1 | 1) => void;
  onRemove?: () => void;
  onClose: () => void;
}) {
  const step = (delta: number) => onStep((index + delta + urls.length) % urls.length);
  const quarter = ((turns % 4) + 4) % 4;
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (urls.length < 2) return;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      step(-1);
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      step(1);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogPopup
        variant="media"
        showCloseButton={false}
        bottomStickOnMobile={false}
        className="row-start-1 h-full max-h-[92vh] w-[92vw] max-w-[92vw] items-center justify-center overflow-visible"
        aria-label={`Photo ${index + 1} of ${urls.length}`}
        onKeyDown={onKeyDown}
        onClick={(event) => {
          if (event.target === event.currentTarget) onClose();
        }}
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
          <img
            src={urls[index]}
            alt={`Photo ${index + 1}`}
            className={cn(
              "max-h-[80vh] w-auto max-w-full rounded-lg border border-border/70 object-contain shadow-2xl transition-transform",
              QUARTER_TURN_CLASS[quarter],
              quarter % 2 === 1 && "max-h-[calc(92vw-96px)] max-w-[80vh]",
            )}
            onClick={onClose}
          />
          <div className="mt-2 flex items-center gap-1.5 text-white/80 text-xs">
            <Button variant="overlay" size="icon-micro" onClick={() => onRotate(-90)} disabled={rotating} aria-label="Rotate left" title="Rotate left">
              <RotateCcwIcon />
            </Button>
            <span className="font-mono tabular-nums" aria-live="polite">
              {String(index + 1).padStart(2, "0")} / {String(urls.length).padStart(2, "0")}
            </span>
            <Button variant="overlay" size="icon-micro" onClick={() => onRotate(90)} disabled={rotating} aria-label="Rotate right" title="Rotate right">
              <RotateCwIcon />
            </Button>
            {onEdit ? (
              <Button variant="overlay" size="micro" className="ms-1" onClick={onEdit} disabled={rotating} title="Crop, adjust or remove the background">
                <SlidersHorizontalIcon />
                {edited ? "Edited" : "Edit"}
              </Button>
            ) : null}
          </div>
          {onMove || onRemove ? (
            // Touch only: the desktop drags thumbnails and uses their delete button.
            <div className="mt-3 hidden items-center gap-2 pointer-coarse:flex">
              {onMove && urls.length > 1 ? (
                <>
                  <Button variant="outline" size="sm" disabled={index === 0} onClick={() => onMove(-1)}>
                    <ArrowLeftIcon />
                    Earlier
                  </Button>
                  <Button variant="outline" size="sm" disabled={index === urls.length - 1} onClick={() => onMove(1)}>
                    Later
                    <ArrowRightIcon />
                  </Button>
                </>
              ) : null}
              {onRemove ? (
                <Button variant="destructive-outline" size="sm" onClick={onRemove}>
                  <Trash2Icon />
                  Delete
                </Button>
              ) : null}
            </div>
          ) : null}
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
