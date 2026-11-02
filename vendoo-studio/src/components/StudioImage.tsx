import { useEffect, useState, type ImgHTMLAttributes } from "react";
import { cachedPrivateImage, loadPrivateImage } from "../api/privateImages";
import { useWorkspaceRuntime } from "../app/runtime";

/** Private API images use the same seller session as every other cloud read. */
export function StudioImage({ src, alt, ...props }: ImgHTMLAttributes<HTMLImageElement>) {
  const runtime = useWorkspaceRuntime();
  const privateImage = runtime.hosted && typeof src === "string" && src.startsWith("/api/");
  const [image, setImage] = useState<{ source?: string; url?: string }>({});
  const kept = privateImage && image.source !== src ? cachedPrivateImage(src) : undefined;
  useEffect(() => {
    if (!privateImage || !src || cachedPrivateImage(src)) return;
    const controller = new AbortController();
    loadPrivateImage(src, controller.signal).then(
      url => { if (!controller.signal.aborted) setImage({ source: src, url }); },
      () => { if (!controller.signal.aborted) setImage({ source: src }); },
    );
    return () => controller.abort();
  }, [privateImage, src]);
  const shown = privateImage ? (kept ?? (image.source === src ? image.url : undefined)) : src;
  return <img {...props} alt={alt} src={shown}/>;
}
