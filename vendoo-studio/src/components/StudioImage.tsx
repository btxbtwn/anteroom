import { useEffect, useState, type ImgHTMLAttributes } from "react";
import { studioFetch } from "../api/transport";
import { useWorkspaceRuntime } from "../app/runtime";

/** Private API images use the same seller session as every other cloud read. */
export function StudioImage({ src, alt, ...props }: ImgHTMLAttributes<HTMLImageElement>) {
  const runtime = useWorkspaceRuntime();
  const privateImage = runtime.hosted && typeof src === "string" && src.startsWith("/api/");
  const [image, setImage] = useState<{ source?: string; url?: string }>({});
  useEffect(() => {
    if (!privateImage || !src) return;
    const controller = new AbortController(); let objectUrl: string | undefined;
    void studioFetch(src, { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error("Photo unavailable");
      const blob = await response.blob();
      if (controller.signal.aborted) return;
      objectUrl = URL.createObjectURL(blob); setImage({ source: src, url: objectUrl });
    }).catch(() => { if (!controller.signal.aborted) setImage({ source: src }); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [privateImage, src]);
  return <img {...props} alt={alt} src={privateImage ? image.source === src ? image.url : undefined : src}/>;
}
