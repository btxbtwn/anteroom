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
  it("keeps the structured item details and measurements", () => {
    const details = parseNotes(JSON.stringify({ sellerNotes: "Checked", sku: "COAT-1", garment: "shorts" }));
    expect(details.sellerNotes).toBe("Checked");
    expect(details.sku).toBe("COAT-1");
    expect(details.garment).toBe("pants");
  });
});
