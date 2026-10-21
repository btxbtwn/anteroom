/**
 * Images attached in the composer: the state, the thumbnail chips, and the
 * paperclip. Chips use T3 Code's markup (ComposerImageThumbnail and the
 * attachment row in components/chat/ChatComposer.tsx, MIT, © 2026 T3 Tools Inc.).
 */
import { memo, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { PaperclipIcon, XIcon } from "lucide-react";
import type { ChatAttachmentUpload } from "../../api/types";
import { Button } from "../ui/button";
import { Tooltip, TooltipPopup, TooltipTrigger } from "../ui/tooltip";
import {
  MAX_COMPOSER_IMAGES,
  createComposerImageThumbnail,
  partitionComposerFiles,
  prepareComposerImage,
} from "./composerImages";

export interface ComposerImage {
  id: string;
  file: File;
}

let imageSeq = 0;

/** What is attached, and how to add, remove and turn it into the message's uploads. */
export function useComposerImages() {
  const [images, setImages] = useState<ComposerImage[]>([]);
  const [error, setError] = useState("");
  const imagesRef = useRef<ComposerImage[]>([]);

  const addFiles = useCallback((files: readonly File[]): boolean => {
    const { images: accepted, rejected } = partitionComposerFiles(files);
    if (!accepted.length && !rejected.length) return false;
    const room = Math.max(0, MAX_COMPOSER_IMAGES - imagesRef.current.length);
    const dropped = Math.max(0, accepted.length - room);
    const added = accepted.slice(0, room).map((file) => ({ id: `img-${++imageSeq}`, file }));
    imagesRef.current = [...imagesRef.current, ...added];
    setImages(imagesRef.current);
    setError(
      rejected.length
        ? `Only images can be attached (JPEG, PNG, WebP, HEIC). Skipped ${rejected.map((file) => file.name).join(", ")}.`
        : dropped
          ? `Attach at most ${MAX_COMPOSER_IMAGES} images to one message.`
          : "",
    );
    return accepted.length > 0;
  }, []);

  const remove = useCallback((id: string) => {
    imagesRef.current = imagesRef.current.filter((image) => image.id !== id);
    setImages(imagesRef.current);
    setError("");
  }, []);

  const clear = useCallback(() => {
    imagesRef.current = [];
    setImages([]);
    setError("");
  }, []);

  /** Encode every image for the request; a file that cannot be read stops the send. */
  const prepare = useCallback(async (list: readonly ComposerImage[]): Promise<ChatAttachmentUpload[] | null> => {
    const prepared = await Promise.all(list.map((image) => prepareComposerImage(image.file)));
    const failed = list.filter((_, index) => !prepared[index].ok);
    if (failed.length) {
      setError(`Could not attach ${failed.map((image) => image.file.name).join(", ")}: too large or unreadable.`);
      return null;
    }
    return prepared.flatMap((item) => (item.ok ? [{ name: item.name, data_url: item.dataUrl }] : []));
  }, []);

  return { images, error, setError, addFiles, remove, clear, prepare };
}

/** Keeps full-resolution decoding out of composer renders. */
const ComposerImageThumbnail = memo(function ComposerImageThumbnail({
  file,
  alt,
  className,
  fallback,
}: {
  file: File;
  alt: string;
  className: string;
  fallback: ReactNode;
}) {
  const [preview, setPreview] = useState<{ file: File; src: string | null } | null>(null);
  useEffect(() => {
    let active = true;
    void createComposerImageThumbnail(file).then((src) => {
      if (active) setPreview({ file, src });
    });
    return () => {
      active = false;
    };
  }, [file]);
  const src = preview?.file === file ? preview.src : null;
  return src ? <img src={src} alt={alt} className={className} /> : <>{fallback}</>;
});

export function ComposerImageChips({ images, onRemove }: { images: ComposerImage[]; onRemove: (id: string) => void }) {
  if (!images.length) return null;
  return (
    <div className="mb-3 flex max-w-full flex-wrap gap-2">
      {images.map((image) => (
        <div
          key={image.id}
          className="group/attachment relative h-16 w-16 shrink-0 overflow-hidden rounded-lg border border-border/80 bg-background"
        >
          <ComposerImageThumbnail
            file={image.file}
            alt={image.file.name}
            className="h-full w-full object-cover"
            fallback={
              <span className="flex h-full items-center justify-center px-1 text-center text-3xs text-secondary-label">
                {image.file.name}
              </span>
            }
          />
          <Button
            variant="media-close"
            size="icon-xs"
            className="absolute right-1 top-1"
            onClick={() => onRemove(image.id)}
            aria-label={`Remove ${image.file.name}`}
          >
            <XIcon />
          </Button>
        </div>
      ))}
    </div>
  );
}

export function ComposerAttachButton({ onFiles, disabled = false }: { onFiles: (files: File[]) => void; disabled?: boolean }) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept="image/jpeg,image/png,image/webp,image/heic,image/heif,.heic,.heif"
        className="hidden"
        onChange={(event) => {
          const files = Array.from(event.currentTarget.files ?? []);
          event.currentTarget.value = "";
          onFiles(files);
        }}
      />
      <Tooltip>
        <TooltipTrigger
          render={
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              disabled={disabled}
              onPointerDown={(event) => event.preventDefault()}
              onClick={() => inputRef.current?.click()}
              aria-label="Attach images"
            />
          }
        >
          <PaperclipIcon />
        </TooltipTrigger>
        <TooltipPopup>Attach images</TooltipPopup>
      </Tooltip>
    </>
  );
}
