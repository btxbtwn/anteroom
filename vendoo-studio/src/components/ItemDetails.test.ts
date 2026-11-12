import { describe, expect, it } from "vitest";
import { changedDetails, detailNoteChanges, parseNotes, savedGarment } from "./itemDetailsData";
describe("saved seller notes", () => {
  it("keeps measurements that arrive after typing into a different measurement", () => {
    const before = parseNotes(null);
    const patch = changedDetails(before, { ...before, measurements: { top: { pitToPit: "22" }, pants: {}, skirt: {} } });
    const notes = JSON.stringify({ measurements: { top: { length: "30" }, pants: { waist: "16" } } });
    // The server merges these per field into the stored measurements.
    expect(detailNoteChanges(notes, patch, "15x12x4").measurements).toEqual({ top: { pitToPit: "22" } });
  });
  it("overlays only edited fields and preserves stored metadata", () => {
    const result = detailNoteChanges(JSON.stringify({ sellerNotes: "Original", sku: "KEEP-123", knownFlaws: "Mark", categoryOverride: "Shirts" }),
      { sellerNotes: "Typed before loading finished" }, "15x12x4");
    expect(result).toMatchObject({ sellerNotes: "Typed before loading finished", packageDimensions: "15x12x4" });
    for (const key of ["sku", "knownFlaws", "categoryOverride"]) expect(result).not.toHaveProperty(key);
  });
  it("writes a legacy shorts set whole when it is first saved as pants", () => {
    const notes = JSON.stringify({ sku: "A1", measurements: { shorts: { waist: "15", inseam: "5" } } });
    expect(detailNoteChanges(notes, { measurements: { top: {}, pants: { inseam: "6" }, skirt: {} } }, "13x10x3").measurements)
      .toEqual({ pants: { waist: "15", inseam: "6" } });
  });
  it("keeps seller text notes as seller notes on the first save", () => {
    expect(detailNoteChanges("Stain on cuff", { sku: "A1" }, "13x10x3")).toMatchObject({ sellerNotes: "Stain on cuff", sku: "A1" });
  });
  it("preserves text from existing cloud drafts when opening and saving the full editor", () => {
    for (const notes of ["Keep measurements\nDo not remove", '"quoted text"', "42", "[1,2]", '{"note":"Keep this too"}']) {
      const details = parseNotes(notes);
      expect(details.sellerNotes).toBe(notes);
      expect(parseNotes(JSON.stringify(details)).sellerNotes).toBe(notes);
    }
  });
  it("does not show the server's own notes keys as seller text", () => {
    for (const notes of [
      JSON.stringify({ categoryOverride: "Clothing > Men > Shirts > T-Shirts", askingPrice: "25.00" }),
      JSON.stringify({ vendooItemId: "abc", vendooUrl: "https://web.vendoo.co/app/item/abc" }),
      JSON.stringify({ titlePinned: true }),
    ]) {
      expect(parseNotes(notes).sellerNotes).toBe("");
    }
  });
  it("keeps the structured item details and measurements", () => {
    const details = parseNotes(JSON.stringify({ sellerNotes: "Checked", sku: "COAT-1", garment: "shorts" }));
    expect(details.sellerNotes).toBe("Checked");
    expect(details.sku).toBe("COAT-1");
    expect(details.garment).toBe("pants");
  });
});

describe("the garment measured", () => {
  const jeans = { category: "Women > Jeans > Skinny", title: "Madewell skinny jeans" };
  it("follows the category until the seller types a measurement", () => {
    expect(parseNotes(null, undefined, jeans).garment).toBe("pants");
    expect(parseNotes(JSON.stringify({ sellerNotes: "x" }), undefined, { category: "Women > Skirts > Mini" }).garment).toBe("skirt");
    // A garment saved by an earlier notes edit, before any measurement, was only the default.
    expect(parseNotes(JSON.stringify({ garment: "top", measurements: {} }), undefined, jeans).garment).toBe("pants");
  });
  it("uses the title when there is no category", () => {
    expect(parseNotes(null, undefined, { title: "Levi's 505 jeans" }).garment).toBe("pants");
  });
  it("keeps a category that names a top, whatever the title says", () => {
    expect(parseNotes(null, undefined, { category: "Women > Tops & Blouses > Blouses", title: "Blouse to wear with jeans" }).garment).toBe("top");
  });
  it("keeps Top when nothing points elsewhere", () => {
    expect(parseNotes(null, undefined, { category: "Women > Dresses > Midi", title: "Floral dress" }).garment).toBe("top");
    expect(parseNotes(null).garment).toBe("top");
  });
  it("keeps the saved garment once measurements are typed", () => {
    const notes = JSON.stringify({ garment: "top", measurements: { top: { pitToPit: "20" } } });
    expect(parseNotes(notes, undefined, jeans).garment).toBe("top");
  });
  it("opens the garment that holds the typed measurements when none was saved", () => {
    expect(parseNotes(JSON.stringify({ measurements: { skirt: { waist: "14" } } }), undefined, jeans).garment).toBe("skirt");
  });
  it("saves the picked garment with the first measurement", () => {
    const before = parseNotes(null, undefined, jeans);
    const patch = changedDetails(before, { ...before, measurements: { top: {}, pants: { waist: "14" }, skirt: {} } }, null);
    expect(patch.garment).toBe("pants");
    expect(detailNoteChanges(null, patch, "13x10x3").garment).toBe("pants");
    expect(changedDetails(before, before, null)).toEqual({});
  });
  it("reports whether a garment was saved", () => {
    expect(savedGarment(null)).toBeNull();
    expect(savedGarment(JSON.stringify({ garment: "shorts" }))).toBe("pants");
    expect(savedGarment(JSON.stringify({ sellerNotes: "x" }))).toBeNull();
  });
});
