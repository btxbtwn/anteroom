import type { Annotation, CropBox, PhotoEdits, Redaction, StudioOptions } from "./api/types";

/** Width over height; null leaves the crop free. */
export type CropAspect = number | null;

/** Shapes marketplaces show best: square for Poshmark, Mercari and Depop grids, 4:5 for Depop's feed, 4:3 for Etsy. */
export const CROP_ASPECTS: { id: string; label: string; aspect: CropAspect }[] = [
  { id: "free", label: "Free", aspect: null },
  { id: "1:1", label: "Square", aspect: 1 },
  { id: "4:5", label: "4:5", aspect: 4 / 5 },
  { id: "3:4", label: "3:4", aspect: 3 / 4 },
  { id: "4:3", label: "4:3", aspect: 4 / 3 },
];

const MIN_SIDE = 0.05;
const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));

/** The largest centred crop of ``aspect`` in a frame ``frameAspect`` wide per unit of height. */
export function centeredCrop(aspect: number, frameAspect: number): CropBox {
  if (frameAspect > aspect) {
    const width = aspect / frameAspect;
    return { x: (1 - width) / 2, y: 0, width, height: 1 };
  }
  const height = frameAspect / aspect;
  return { x: 0, y: (1 - height) / 2, width: 1, height };
}

export type CropHandle = "move" | "nw" | "ne" | "sw" | "se";

/**
 * Drag the crop by ``dx``/``dy`` (fractions of the frame). Corners resize
 * against the opposite corner and keep ``aspect`` when one is chosen; the box
 * always stays inside the frame.
 */
export function dragCrop(
  box: CropBox,
  handle: CropHandle,
  dx: number,
  dy: number,
  aspect: CropAspect,
  frameAspect: number,
): CropBox {
  if (handle === "move") {
    return { ...box, x: clamp(box.x + dx, 0, 1 - box.width), y: clamp(box.y + dy, 0, 1 - box.height) };
  }
  const west = handle === "nw" || handle === "sw";
  const north = handle === "nw" || handle === "ne";
  const anchorX = west ? box.x + box.width : box.x;
  const anchorY = north ? box.y + box.height : box.y;
  const roomX = west ? anchorX : 1 - anchorX;
  const roomY = north ? anchorY : 1 - anchorY;
  let width = clamp(box.width + (west ? -dx : dx), MIN_SIDE, roomX);
  let height = clamp(box.height + (north ? -dy : dy), MIN_SIDE, roomY);
  if (aspect) {
    // In frame fractions, a crop of shape ``aspect`` has height = width * frameAspect / aspect.
    const ratio = frameAspect / aspect;
    height = width * ratio;
    if (height > roomY) {
      height = roomY;
      width = height / ratio;
    }
  }
  return {
    x: west ? anchorX - width : anchorX,
    y: north ? anchorY - height : anchorY,
    width,
    height,
  };
}

/** One-tap looks, in the order the editor shows them. */
export const PHOTO_FILTERS: { id: NonNullable<PhotoEdits["filter"]>; label: string }[] = [
  { id: "vivid", label: "Vivid" },
  { id: "crisp", label: "Crisp" },
  { id: "warm", label: "Warm" },
  { id: "cool", label: "Cool" },
  { id: "bw", label: "B&W" },
  { id: "studio", label: "Studio clean" },
];

/**
 * The edits after mirroring the frame. The crop is taken after the flip and
 * marks sit on the finished frame, so both mirror too and still cover the same
 * part of the item.
 */
export function flipEdits(edits: PhotoEdits, axis: "horizontal" | "vertical"): PhotoEdits {
  const horizontal = axis === "horizontal";
  const box = <T extends CropBox>(value: T): T => (horizontal
    ? { ...value, x: Math.max(0, 1 - value.x - value.width) }
    : { ...value, y: Math.max(0, 1 - value.y - value.height) });
  const point = (value: number) => 1 - value;
  return {
    ...edits,
    ...(horizontal ? { flip_horizontal: !edits.flip_horizontal } : { flip_vertical: !edits.flip_vertical }),
    // The frame is straightened after the flip, so a mirrored tilt leans the other way.
    straighten: edits.straighten ? -edits.straighten : edits.straighten,
    crop: edits.crop ? box(edits.crop) : edits.crop,
    redactions: (edits.redactions ?? []).map(box),
    // A text label keeps reading left to right; only where it starts moves.
    annotations: (edits.annotations ?? []).map((mark) => (horizontal
      ? { ...mark, x1: point(mark.x1), x2: point(mark.x2) }
      : { ...mark, y1: point(mark.y1), y2: point(mark.y2) })),
  };
}

export type MarkTool = Annotation["kind"] | "pixelate" | "blur";

export const MARK_TOOLS: { id: MarkTool; label: string }[] = [
  { id: "arrow", label: "Arrow" },
  { id: "rectangle", label: "Box" },
  { id: "ellipse", label: "Circle" },
  { id: "text", label: "Text" },
  { id: "pixelate", label: "Pixelate" },
  { id: "blur", label: "Blur" },
];

const unit = (value: number) => clamp(value, 0, 1);

/**
 * A mark from a drag on the finished frame, from (x1, y1) to (x2, y2) in
 * fractions of it. Returns null for a drag too small to mean anything (a text
 * label needs only a tap).
 */
export function markFromDrag(tool: MarkTool, x1: number, y1: number, x2: number, y2: number, color: string, text: string):
  { annotation: Annotation } | { redaction: Redaction } | null {
  const [ax, ay, bx, by] = [unit(x1), unit(y1), unit(x2), unit(y2)];
  if (tool === "text") {
    const label = text.trim();
    return label ? { annotation: { kind: "text", x1: ax, y1: ay, x2: ax, y2: ay, color, text: label.slice(0, 60) } } : null;
  }
  if (Math.abs(bx - ax) < 0.02 && Math.abs(by - ay) < 0.02) return null;
  if (tool === "pixelate" || tool === "blur") {
    // At least 1% each way, and always inside the frame, which the server requires.
    const width = Math.max(0.01, Math.abs(bx - ax));
    const height = Math.max(0.01, Math.abs(by - ay));
    const x = Math.min(Math.min(ax, bx), 1 - width);
    const y = Math.min(Math.min(ay, by), 1 - height);
    return { redaction: { x, y, width, height, style: tool } };
  }
  return { annotation: { kind: tool, x1: ax, y1: ay, x2: bx, y2: by, color } };
}

/** Trim and collapse whitespace the way the server does for Photoroom prompts. */
function studioWords(value: string | undefined): string {
  return (value ?? "").trim().replace(/\s+/g, " ");
}

/** Match the maximum number of photos accepted by the AI Studio route. */
export const MAX_STUDIO_PHOTOS = 20;

/** The AI Studio choices the server accepts, made consistent the way it makes them. */
export function normalizeStudio(options: StudioOptions): StudioOptions {
  if (options.virtual_model) {
    return { virtual_model: options.virtual_model };
  }
  const flatLay = Boolean(options.flat_lay) && !options.ghost_mannequin;
  const generative = Boolean(options.ghost_mannequin || flatLay || options.shadow);
  const keep = studioWords(options.keep);
  const exclude = studioWords(options.exclude);
  return {
    remove_background: Boolean(options.remove_background) || generative,
    background: options.background,
    ghost_mannequin: Boolean(options.ghost_mannequin),
    flat_lay: flatLay,
    shadow: Boolean(options.shadow),
    ...(keep ? { keep } : {}),
    ...(exclude ? { exclude } : {}),
  };
}

/** Why these AI Studio options cannot run, or null; mirrors `StudioOptions` on the server. */
export function studioProblem(options: StudioOptions): string | null {
  if (options.ghost_mannequin && options.flat_lay) return "Choose Ghost mannequin or Flat lay, not both.";
  if (options.virtual_model && (options.remove_background || options.ghost_mannequin || options.flat_lay || options.shadow
    || studioWords(options.keep) || studioWords(options.exclude))) {
    return "A virtual model makes its own scene, so it cannot be combined with other AI Studio edits.";
  }
  if (!options.remove_background && !options.ghost_mannequin && !options.flat_lay && !options.shadow && !options.virtual_model) {
    return "Choose an AI Studio edit.";
  }
  return null;
}

/**
 * The edits with a new crop, marks moved so they stay on the same part of the
 * photo. Marks sit on the cropped frame, so without this a new crop would slide
 * a redaction off the name it hides. A box is trimmed to the new frame and
 * dropped once it falls wholly outside (that part is cropped away anyway).
 */
export function withCrop(edits: PhotoEdits, crop: CropBox | null): PhotoEdits {
  const before = edits.crop ?? { x: 0, y: 0, width: 1, height: 1 };
  const after = crop ?? { x: 0, y: 0, width: 1, height: 1 };
  const mapX = (value: number) => (before.x + value * before.width - after.x) / after.width;
  const mapY = (value: number) => (before.y + value * before.height - after.y) / after.height;
  const redactions = (edits.redactions ?? []).flatMap((box) => {
    const left = Math.max(0, mapX(box.x));
    const top = Math.max(0, mapY(box.y));
    const right = Math.min(1, mapX(box.x + box.width));
    const bottom = Math.min(1, mapY(box.y + box.height));
    return right > left && bottom > top ? [{ ...box, x: left, y: top, width: right - left, height: bottom - top }] : [];
  });
  const annotations = (edits.annotations ?? []).map((mark) => ({
    ...mark, x1: unit(mapX(mark.x1)), y1: unit(mapY(mark.y1)), x2: unit(mapX(mark.x2)), y2: unit(mapY(mark.y2)),
  }));
  return { ...edits, crop, redactions, annotations };
}
