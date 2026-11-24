import { describe, expect, it } from "vitest";
import { centeredCrop, dragCrop, flipEdits, markFromDrag, normalizeStudio, studioProblem, withCrop } from "./photoEdits";

describe("photo crop geometry", () => {
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
});

describe("flip, marks and AI Studio rules", () => {
  it("mirrors the crop and marks with the frame, and flips back to where it started", () => {
    const edits = {
      crop: { x: 0.1, y: 0.2, width: 0.3, height: 0.4 },
      redactions: [{ x: 0, y: 0, width: 0.25, height: 0.5, style: "blur" as const }],
      annotations: [{ kind: "arrow" as const, x1: 0.1, y1: 0.2, x2: 0.6, y2: 0.9 }],
    };
    const flipped = flipEdits(edits, "horizontal");
    expect(flipped.flip_horizontal).toBe(true);
    expect(flipped.crop?.x).toBeCloseTo(0.6);
    expect(flipped.redactions?.[0].x).toBeCloseTo(0.75);
    expect(flipped.annotations?.[0].x1).toBeCloseTo(0.9);
    const back = flipEdits(flipped, "horizontal");
    expect(back.flip_horizontal).toBe(false);
    expect(back.crop?.x).toBeCloseTo(0.1);
    expect(flipEdits(edits, "vertical").crop?.y).toBeCloseTo(0.4);
  });

  it("turns a drag into a mark, and ignores a stray tap", () => {
    expect(markFromDrag("arrow", 0.1, 0.1, 0.11, 0.11, "#E5484D", "")).toBeNull();
    expect(markFromDrag("rectangle", 0.6, 0.6, 0.2, 0.3, "#E5484D", "")).toEqual(
      { annotation: { kind: "rectangle", x1: 0.6, y1: 0.6, x2: 0.2, y2: 0.3, color: "#E5484D" } });
    const hidden = markFromDrag("pixelate", 0.6, 0.6, 0.2, 0.3, "#E5484D", "");
    expect(hidden && "redaction" in hidden && hidden.redaction.x).toBeCloseTo(0.2);
    expect(markFromDrag("text", 0.3, 0.4, 0.3, 0.4, "#E5484D", "  ")).toBeNull();
    expect(markFromDrag("text", 1.2, 0.4, 0.3, 0.4, "#E5484D", "Stain")).toEqual(
      { annotation: { kind: "text", x1: 1, y1: 0.4, x2: 1, y2: 0.4, color: "#E5484D", text: "Stain" } });
  });

  it("applies the server's AI Studio combinations", () => {
    expect(normalizeStudio({ ghost_mannequin: true }).remove_background).toBe(true);
    expect(normalizeStudio({ ghost_mannequin: true, flat_lay: true }).flat_lay).toBe(false);
    expect(normalizeStudio({ shadow: true, virtual_model: { model: "auto" } })).toEqual({ virtual_model: { model: "auto" } });
    expect(normalizeStudio({ remove_background: true, keep: "  shirt  ", exclude: "ruler" })).toMatchObject({
      keep: "shirt", exclude: "ruler",
    });
    expect(normalizeStudio({ remove_background: true, keep: "  ", exclude: "" }).keep).toBeUndefined();
    expect(studioProblem({ ghost_mannequin: true, flat_lay: true })).toMatch(/not both/);
    expect(studioProblem({ virtual_model: {}, shadow: true })).toMatch(/own scene/);
    expect(studioProblem({ virtual_model: {}, keep: "shirt" })).toMatch(/own scene/);
    expect(studioProblem({})).toMatch(/Choose an AI Studio edit/);
    expect(studioProblem({ remove_background: true })).toBeNull();
  });


  it("keeps a redaction on the same part of the photo when the crop changes", () => {
    // A name at 70–80% of the full width is hidden; cropping to the right half moves the box with it.
    const edits = { redactions: [{ x: 0.7, y: 0.4, width: 0.1, height: 0.2 }], annotations: [{ kind: "arrow" as const, x1: 0.5, y1: 0, x2: 1, y2: 1 }] };
    const cropped = withCrop(edits, { x: 0.5, y: 0, width: 0.5, height: 1 });
    expect(cropped.redactions?.[0].x).toBeCloseTo(0.4);
    expect(cropped.redactions?.[0].width).toBeCloseTo(0.2);
    expect(cropped.annotations?.[0].x1).toBeCloseTo(0);
    // Uncropping puts it back.
    const back = withCrop(cropped, null);
    expect(back.redactions?.[0].x).toBeCloseTo(0.7);
    // A box cropped away entirely goes, one partly outside is trimmed.
    expect(withCrop(edits, { x: 0, y: 0, width: 0.5, height: 1 }).redactions).toEqual([]);
    const trimmed = withCrop(edits, { x: 0, y: 0, width: 0.75, height: 1 }).redactions?.[0];
    expect((trimmed?.x ?? 0) + (trimmed?.width ?? 0)).toBeCloseTo(1);
  });

  it("keeps a redaction drawn at the edge inside the frame, and mirrors the tilt with a flip", () => {
    const edge = markFromDrag("blur", 1, 0.995, 0.97, 1, "#E5484D", "");
    expect(edge && "redaction" in edge && edge.redaction.x + edge.redaction.width).toBeLessThanOrEqual(1);
    expect(edge && "redaction" in edge && edge.redaction.y + edge.redaction.height).toBeLessThanOrEqual(1);
    expect(flipEdits({ straighten: 5 }, "horizontal").straighten).toBe(-5);
  });
});
