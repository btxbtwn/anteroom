import type { CropBox } from "./api/types";

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
