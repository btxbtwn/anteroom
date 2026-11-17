import { useEffect, useState, type ImgHTMLAttributes, type ReactNode } from "react";
import { cachedPrivateImage, loadPrivateImage, PhotoLoadError } from "../api/privateImages";
import { useWorkspaceRuntime } from "../app/runtime";

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
    return renderError({ status: failed.status, retry: () => { setImage({}); setAttempt((count) => count + 1); } });
  }
  const shown = privateImage ? (kept ?? (image.source === src ? image.url : undefined)) : src;
  return <img {...props} alt={alt} src={shown} onError={(event) => {
    // A file that downloaded but cannot be shown is a failure too.
    if (renderError && typeof src === "string") setImage({ source: src, failed: { status: null } });
    onError?.(event);
  }}/>;
}
