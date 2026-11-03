import { describe, expect, it } from "vitest";
import { changedDetails, mergeDetailNotes, parseNotes } from "./itemDetailsData";
describe("saved seller notes", () => {
  it("keeps measurements that arrive after typing into a different measurement", () => {
    const before = parseNotes(null);
    const patch = changedDetails(before, { ...before, measurements: { top: { pitToPit: "22" }, pants: {}, skirt: {} } });
    const notes = JSON.stringify({ measurements: { top: { length: "30" }, pants: { waist: "16" } } });
    expect(JSON.parse(mergeDetailNotes(notes, patch, "15x12x4")).measurements).toMatchObject({ top: { pitToPit: "22", length: "30" }, pants: { waist: "16" } });
  });
  it("overlays only edited fields and preserves stored metadata", () => {
    const result = JSON.parse(mergeDetailNotes(JSON.stringify({ sellerNotes: "Original", sku: "KEEP-123", knownFlaws: "Mark", categoryOverride: "Shirts" }),
      { sellerNotes: "Typed before loading finished" }, "15x12x4"));
    expect(result).toMatchObject({ sellerNotes: "Typed before loading finished", sku: "KEEP-123", knownFlaws: "Mark", categoryOverride: "Shirts", packageDimensions: "15x12x4" });
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
