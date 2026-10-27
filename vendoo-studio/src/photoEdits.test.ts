import { describe, expect, it } from "vitest";
import { NO_EDITS, centeredCrop, describeEdits, dragCrop, sameEdits, turnEdits, withDefaults } from "./photoEdits";

describe("photo edit geometry", () => {
  it("turns the crop with the photo, as the server does", () => {
    const edits = { ...NO_EDITS, crop: { x: 0, y: 0, width: 0.5, height: 0.25 } };
    const right = turnEdits(edits, 90);
    expect(right.rotate).toBe(90);
    expect(right.crop).toEqual({ x: 0.75, y: 0, width: 0.25, height: 0.5 });
    expect(turnEdits(right, -90)).toEqual(edits);
    expect(turnEdits(edits, -90).rotate).toBe(270);
  });

  it("centres the largest crop of a shape", () => {
    // A 4:3 photo cropped square keeps the full height.
    const square = centeredCrop(1, 4 / 3);
    expect(square.height).toBe(1);
    expect(square.width).toBeCloseTo(0.75);
    expect(square.x).toBeCloseTo(0.125);
    // A portrait 3:4 photo cropped 4:3 keeps the full width.
    const wide = centeredCrop(4 / 3, 3 / 4);
    expect(wide.width).toBe(1);
    expect(wide.height).toBeCloseTo(0.5625);
  });

  it("moves the crop without leaving the frame", () => {
    const box = { x: 0.2, y: 0.2, width: 0.5, height: 0.5 };
    expect(dragCrop(box, "move", 0.9, -0.9, null, 1)).toEqual({ x: 0.5, y: 0, width: 0.5, height: 0.5 });
  });

  it("resizes from a corner against the opposite one and keeps a chosen shape", () => {
    const box = { x: 0.25, y: 0.25, width: 0.5, height: 0.5 };
    const free = dragCrop(box, "se", 0.1, -0.2, null, 1);
    expect(free).toEqual({ x: 0.25, y: 0.25, width: 0.6, height: 0.3 });
    const nw = dragCrop(box, "nw", -0.1, 0, null, 1);
    expect(nw.x).toBeCloseTo(0.15);
    expect(nw.x + nw.width).toBeCloseTo(0.75);
    // Square on a 4:3 frame: height fraction = width fraction * 4/3.
    const square = dragCrop({ x: 0, y: 0, width: 0.3, height: 0.4 }, "se", 0.3, 0, 1, 4 / 3);
    expect(square.height / square.width).toBeCloseTo(4 / 3);
    expect(square.y + square.height).toBeLessThanOrEqual(1);
    const capped = dragCrop({ x: 0, y: 0, width: 0.3, height: 0.4 }, "se", 0.7, 0, 1, 4 / 3);
    expect(capped.height).toBeCloseTo(1);
    expect(capped.width).toBeCloseTo(0.75);
  });

  it("compares and describes edits in the seller's words", () => {
    expect(sameEdits(withDefaults(null), NO_EDITS)).toBe(true);
    expect(sameEdits({ ...NO_EDITS, exposure: 0.2 }, NO_EDITS)).toBe(false);
    expect(describeEdits({ ...NO_EDITS, background: "#ffffff", straighten: 2 })).toEqual(["rotated", "background removed"]);
    expect(describeEdits(null)).toEqual([]);
  });
});
