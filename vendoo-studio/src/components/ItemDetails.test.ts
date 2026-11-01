import { describe, expect, it } from "vitest";
import { parseNotes } from "./ItemDetails";
describe("saved seller notes", () => {
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
