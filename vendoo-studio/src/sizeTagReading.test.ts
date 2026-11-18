import { describe, expect, it } from "vitest";
import type { SizeTagReadResult } from "./api/types";
import { applySizeTagReading } from "./sizeTagReading";

const read = (patch: Partial<SizeTagReadResult>): SizeTagReadResult => ({
  photos_read: 3, status: "accepted", size: "M", sizes: ["M"], ...patch,
});

describe("applySizeTagReading", () => {
  it("fills a blank Size from an agreed tag reading", () => {
    expect(applySizeTagReading(" ", read({}))).toEqual({
      size: "M", suggested: true, needsReview: false, note: "Size filled from the tag. Check it.",
    });
  });

  it("treats size 0 as a size, read or typed", () => {
    expect(applySizeTagReading("", read({ size: "0", sizes: ["0"] })).size).toBe("0");
    const typedZero = applySizeTagReading("0", read({ size: "2", sizes: ["2"] }));
    expect(typedZero).toMatchObject({ size: "0", suggested: false, needsReview: true });
    expect(typedZero.note).toBe("The tag reads 2. Check the size you typed.");
  });

  it("keeps a typed size and only notes when it matches", () => {
    expect(applySizeTagReading("m", read({}))).toEqual({ size: "m", suggested: false, needsReview: false, note: "Size matches the tag." });
    // The server counts these as one size; so does the comparison here.
    expect(applySizeTagReading("32 x 30", read({ size: "32×30", sizes: ["32×30"] }))).toMatchObject({ needsReview: false, note: "Size matches the tag." });
    expect(applySizeTagReading("32  X30", read({ size: "32 x 30", sizes: ["32 x 30"] })).needsReview).toBe(false);
    expect(applySizeTagReading("32 x 32", read({ size: "32 x 30", sizes: ["32 x 30"] })).needsReview).toBe(true);
    expect(applySizeTagReading("L", read({ status: "not_found", size: null, sizes: [] })))
      .toEqual({ size: "L", suggested: false, needsReview: false, note: "" });
    expect(applySizeTagReading("L", read({ status: "conflict", size: null, sizes: ["L", "XL"] }))).toEqual({
      size: "L", suggested: false, needsReview: true, note: "The photos show different sizes (L, XL). Check the size you typed.",
    });
  });

  it("asks the seller instead of guessing when the tag is missing, unreadable or conflicting", () => {
    const missing = applySizeTagReading("", read({ status: "not_found", size: null, sizes: [] }));
    expect(missing).toEqual({ size: "", suggested: false, needsReview: true, note: "No size tag found in the photos. Type the size from the tag." });
    expect(applySizeTagReading("", read({ status: "unreadable", size: null, sizes: ["M"] })).note)
      .toBe("The size tag could not be read. Type the size from the tag.");
    const conflict = applySizeTagReading("", read({ status: "conflict", size: null, sizes: ["8", "6"] }));
    expect(conflict).toMatchObject({ size: "", needsReview: true });
    expect(conflict.note).toBe("The photos show different sizes (8, 6). Check the tag and type the size.");
  });
});
