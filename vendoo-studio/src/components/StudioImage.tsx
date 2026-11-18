import { useEffect, useState, type ImgHTMLAttributes, type ReactNode } from "react";
import { cachedPrivateImage, forgetPrivateImage, loadPrivateImage, PhotoLoadError } from "../api/privateImages";
import { useWorkspaceRuntime } from "../app/runtime";
import { cn } from "../lib/utils";
import { Button } from "./ui/button";

/** Why an image did not show: the server's status, or null for no answer or an unreadable file. */
export interface ImageFailure { status: number | null; retry: () => void }

/**
 * Private API images use the same seller session as every other cloud read.
 * With `renderError`, a photo that did not load says so and can be tried
 * again, instead of leaving an empty picture with only its alt text.
 */
export function StudioImage({ src, alt, renderError, onError, ...props }: ImgHTMLAttributes<HTMLImageElement> & {
  renderError?: (failure: ImageFailure) => ReactNode;
}) {
  const runtime = useWorkspaceRuntime();
  const privateImage = runtime.hosted && typeof src === "string" && src.startsWith("/api/");
  const [image, setImage] = useState<{ source?: string; url?: string; failed?: { status: number | null } }>({});
  const [attempt, setAttempt] = useState(0);
  const kept = privateImage && image.source !== src ? cachedPrivateImage(src) : undefined;
  useEffect(() => {
    if (!privateImage || !src || cachedPrivateImage(src)) return;
    const controller = new AbortController();
    loadPrivateImage(src, controller.signal).then(
      url => { if (!controller.signal.aborted) setImage({ source: src, url }); },
      (error: unknown) => {
        if (!controller.signal.aborted) setImage({ source: src, failed: { status: error instanceof PhotoLoadError ? error.status : null } });
      },
    );
    return () => controller.abort();
  }, [privateImage, src, attempt]);
  const failed = image.source === src ? image.failed : undefined;
  if (failed && renderError) {
    return renderError({ status: failed.status, retry: () => {
      // A copy that arrived but cannot be shown must not be reused.
      if (privateImage && typeof src === "string") forgetPrivateImage(src);
      setImage({});
      setAttempt((count) => count + 1);
    } });
  }
  const shown = privateImage ? (kept ?? (image.source === src ? image.url : undefined)) : src;
  return <img {...props} alt={alt} src={shown} onError={(event) => {
    // A file that downloaded but cannot be shown is a failure too.
    if (renderError && typeof src === "string") setImage({ source: src, failed: { status: null } });
    onError?.(event);
  }}/>;
}

/**
 * What a full-size photo says when it did not load. The status stays on the
 * element for diagnosis; the words name no IDs or addresses.
 */
export function PhotoLoadFailure({ status, retry, className }: ImageFailure & { className?: string }) {
  return (
    <div role="alert" data-photo-load-status={status ?? "none"}
      className={cn("flex min-h-40 min-w-60 flex-col items-center justify-center gap-2 rounded-lg border border-border/70 bg-background p-6 text-center text-sm shadow-2xl", className)}>
      <p>{status === 401 || status === 403
        ? "Couldn't load this photo. Sign in again, then retry."
        : "Couldn't load this photo."}</p>
      <Button size="xs" variant="outline" onClick={(event) => { event.stopPropagation(); retry(); }}>Retry</Button>
    </div>
  );
}
