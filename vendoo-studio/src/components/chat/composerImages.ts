/**
 * Images attached in the composer: which files count, a small thumbnail for
 * the chip, and a re-encode that keeps a large photo under the upload limit.
 * Decoding happens off the render path, in promises cached per File.
 *
 * Adapted from T3 Code (MIT, © 2026 T3 Tools Inc.):
 * apps/web/src/lib/imageCompression.ts and components/chat/composerAttachmentFiles.ts.
 */

/** Attached to one message at most. Matches the server's MAX_ATTACHMENTS. */
export const MAX_COMPOSER_IMAGES = 6;
/** What one image may weigh once prepared; the server refuses past 10 MB. */
export const MAX_IMAGE_UPLOAD_BYTES = 4 * 1024 * 1024;
/** Longest edge sent to the model; larger photos only cost upload time. */
const MAX_IMAGE_EDGE = 2048;
/** Decoding a huge source can take the tab down; refuse past this. */
const MAX_COMPRESSIBLE_SOURCE_BYTES = 50 * 1024 * 1024;
const QUALITY_STEPS = [0.92, 0.85, 0.78, 0.68] as const;
const FALLBACK_SCALE_STEPS = [0.75, 0.55] as const;
const HEIC_MIME = /^image\/hei(?:c|f)$/i;
const HEIC_EXTENSION = /\.(?:heic|heif)$/i;
const MIME_BY_EXTENSION: Record<string, string> = {
  jpeg: "image/jpeg",
  jpg: "image/jpeg",
  png: "image/png",
  webp: "image/webp",
  heic: "image/heic",
  heif: "image/heif",
};
const SUPPORTED_MIME = new Set(["image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"]);

/** Finder and some browsers omit the MIME type when dragging HEIC photos. */
export function isHeicImageFile(file: Pick<File, "name" | "type">): boolean {
  if (HEIC_MIME.test(file.type)) return true;
  return (file.type === "" || file.type === "application/octet-stream") && HEIC_EXTENSION.test(file.name);
}

/** The image type a file really is, inferring it from the name when the browser gave none. */
export function composerImageMimeType(file: Pick<File, "name" | "type">): string | null {
  const type = file.type.toLowerCase();
  if (type && type !== "application/octet-stream") return SUPPORTED_MIME.has(type) ? type : null;
  const dot = file.name.lastIndexOf(".");
  return dot > 0 ? MIME_BY_EXTENSION[file.name.slice(dot + 1).toLowerCase()] ?? null : null;
}

/** Split dropped or pasted files into images the model can read and the rest. */
export function partitionComposerFiles(files: readonly File[]): { images: File[]; rejected: File[] } {
  const images: File[] = [];
  const rejected: File[] = [];
  for (const file of files) (composerImageMimeType(file) ? images : rejected).push(file);
  return { images, rejected };
}

export function readFileAsDataUrl(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      if (typeof reader.result === "string") resolve(reader.result);
      else reject(new Error("Could not read image data."));
    });
    reader.addEventListener("error", () => reject(reader.error ?? new Error("Failed to read image.")));
    reader.readAsDataURL(file);
  });
}

function canRecompress(): boolean {
  return typeof createImageBitmap === "function"
    && (typeof OffscreenCanvas === "function" || typeof document !== "undefined");
}

interface Canvas2D {
  canvas: OffscreenCanvas | HTMLCanvasElement;
  context: OffscreenCanvasRenderingContext2D | CanvasRenderingContext2D;
}

function createCanvas(width: number, height: number): Canvas2D | null {
  if (typeof OffscreenCanvas === "function") {
    const canvas = new OffscreenCanvas(width, height);
    const context = canvas.getContext("2d");
    return context ? { canvas, context } : null;
  }
  if (typeof document === "undefined") return null;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  return context ? { canvas, context } : null;
}

async function encodeCanvas(canvas: OffscreenCanvas | HTMLCanvasElement, quality: number, mimeType: string) {
  const blob = typeof HTMLCanvasElement !== "undefined" && canvas instanceof HTMLCanvasElement
    ? await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, mimeType, quality))
    : await (canvas as OffscreenCanvas).convertToBlob({ type: mimeType, quality });
  return blob?.type === mimeType ? blob : null;
}

const thumbnails = new WeakMap<File, Promise<string | null>>();

/** A 256px square thumbnail for the composer chip; full-resolution decoding stays out of renders. */
export function createComposerImageThumbnail(file: File): Promise<string | null> {
  const cached = thumbnails.get(file);
  if (cached) return cached;
  const thumbnail = (async () => {
    if (!canRecompress()) return null;
    let bitmap: ImageBitmap | undefined;
    try {
      bitmap = await createImageBitmap(file);
      const side = Math.min(bitmap.width, bitmap.height);
      if (side <= 0) return null;
      const dimension = Math.min(256, side);
      const surface = createCanvas(dimension, dimension);
      if (!surface) return null;
      surface.context.drawImage(
        bitmap,
        (bitmap.width - side) / 2,
        (bitmap.height - side) / 2,
        side,
        side,
        0,
        0,
        dimension,
        dimension,
      );
      const blob = await encodeCanvas(surface.canvas, 1, "image/png");
      return blob ? await readFileAsDataUrl(blob) : null;
    } catch {
      return null;
    } finally {
      bitmap?.close();
    }
  })();
  thumbnails.set(file, thumbnail);
  return thumbnail;
}

async function encodeWithin(bitmap: ImageBitmap, maxEdge: number, maxBytes: number): Promise<Blob | null> {
  const scale = Math.min(1, maxEdge / Math.max(bitmap.width, bitmap.height));
  const width = Math.max(1, Math.round(bitmap.width * scale));
  const height = Math.max(1, Math.round(bitmap.height * scale));
  const target = createCanvas(width, height);
  if (!target) return null;
  // WebP when the browser can write it, else JPEG on a white matte (no alpha).
  const mimeType = (await encodeCanvas(target.canvas, QUALITY_STEPS[0], "image/webp")) ? "image/webp" : "image/jpeg";
  if (mimeType === "image/jpeg") {
    target.context.fillStyle = "#ffffff";
    target.context.fillRect(0, 0, width, height);
  }
  target.context.imageSmoothingQuality = "high";
  target.context.drawImage(bitmap, 0, 0, width, height);
  for (const quality of QUALITY_STEPS) {
    const encoded = await encodeCanvas(target.canvas, quality, mimeType);
    if (!encoded) return null;
    if (encoded.size <= maxBytes) return encoded;
  }
  return null;
}

export type PreparedImage =
  | { ok: true; name: string; dataUrl: string }
  | { ok: false; reason: "too-large" | "unreadable" };

/**
 * The data URL the message carries. Small images go as they are; larger ones
 * are scaled to MAX_IMAGE_EDGE and re-encoded until they fit. HEIC goes as is
 * when it fits, since the server converts it to JPEG.
 */
export async function prepareComposerImage(file: File, maxBytes = MAX_IMAGE_UPLOAD_BYTES): Promise<PreparedImage> {
  try {
    const heic = isHeicImageFile(file);
    if (file.size <= maxBytes && (heic || !canRecompress())) {
      return { ok: true, name: file.name, dataUrl: await readFileAsDataUrl(file) };
    }
    if (file.size > MAX_COMPRESSIBLE_SOURCE_BYTES || !canRecompress()) return { ok: false, reason: "too-large" };
    let bitmap: ImageBitmap;
    try {
      bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
    } catch {
      return heic ? { ok: false, reason: "too-large" } : { ok: false, reason: "unreadable" };
    }
    try {
      const fits = Math.max(bitmap.width, bitmap.height) <= MAX_IMAGE_EDGE && file.size <= maxBytes;
      if (fits) return { ok: true, name: file.name, dataUrl: await readFileAsDataUrl(file) };
      const base = Math.min(MAX_IMAGE_EDGE, Math.max(bitmap.width, bitmap.height));
      for (const scale of [1, ...FALLBACK_SCALE_STEPS]) {
        const blob = await encodeWithin(bitmap, Math.max(1, Math.round(base * scale)), maxBytes);
        if (blob) {
          const stem = file.name.replace(/\.[^.]+$/, "") || "image";
          const name = `${stem}${blob.type === "image/webp" ? ".webp" : ".jpg"}`;
          return { ok: true, name, dataUrl: await readFileAsDataUrl(blob) };
        }
      }
      return { ok: false, reason: "too-large" };
    } finally {
      bitmap.close();
    }
  } catch {
    return { ok: false, reason: "unreadable" };
  }
}
