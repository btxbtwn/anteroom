import { describe, expect, it } from "vitest";
import { guessGarment, notesWithItemDetails, notesWithMeasurements, parseItemDetails, typedMeasurements } from "./garmentMeasurements";

describe("guessGarment", () => {
  it("reads pants from the folder name and defaults to a top", () => {
    expect(guessGarment("Levi's 505 Jeans")).toBe("pants");
    expect(guessGarment("nike shorts 2")).toBe("pants");
    expect(guessGarment("pleated midi skirt")).toBe("skirt");
    expect(guessGarment("Harley tee")).toBe("top");
    expect(guessGarment("Jeanswear logo hoodie")).toBe("top");
  });
});

describe("typedMeasurements", () => {
  it("keeps a skirt's waist, hip and length", () => {
    expect(typedMeasurements("skirt", { waist: "14", hip: "19", length: "22", sleeve: "9" })).toEqual({
      waist: "14",
      hip: "19",
      length: "22",
    });
  });

  it("keeps the garment's own filled fields only", () => {
    expect(typedMeasurements("top", { pitToPit: " 22.5 ", length: "", waist: "16" })).toEqual({ pitToPit: "22.5" });
  });
});

describe("notesWithMeasurements", () => {
  it("adds the garment and measurements without losing bulk details", () => {
    const notes = notesWithMeasurements(
      JSON.stringify({ cog: "4", vendooLabels: "Bin 4" }),
      "pants",
      { waist: "16", inseam: "30", pitToPit: "22" },
    );
    expect(JSON.parse(notes)).toEqual({
      cog: "4",
      vendooLabels: "Bin 4",
      garment: "pants",
      measurements: { pants: { waist: "16", inseam: "30" } },
    });
  });

  it("starts from empty notes and keeps the other garment's saved set", () => {
    expect(JSON.parse(notesWithMeasurements(null, "top", { length: "27" }))).toEqual({
      garment: "top",
      measurements: { top: { length: "27" } },
    });
    const saved = JSON.stringify({ garment: "pants", measurements: { pants: { waist: "15" } } });
    expect(JSON.parse(notesWithMeasurements(saved, "top", { length: "27" })).measurements).toEqual({
      pants: { waist: "15" },
      top: { length: "27" },
    });
  });
});

describe("parseItemDetails and notesWithItemDetails", () => {
  it("reads the saved garment, measurements, labels, COG and SKU", () => {
    const notes = JSON.stringify({
      garment: "pants",
      cog: "3.5",
      sku: "LV-1",
      vendooLabels: "To List, Bin 4",
      measurements: { pants: { waist: "16", inseam: 30 } },
    });
    expect(parseItemDetails(notes, "top")).toEqual({
      garment: "pants",
      measurements: { waist: "16", inseam: "30" },
      labels: "To List, Bin 4",
      cog: "3.5",
      sku: "LV-1",
    });
  });

  it("falls back to the guessed garment for empty or plain notes", () => {
    expect(parseItemDetails(null, "skirt")).toMatchObject({ garment: "skirt", measurements: {}, labels: "" });
    expect(parseItemDetails("just text", "top").garment).toBe("top");
  });

  it("writes the reviewed details and keeps other note fields", () => {
    const next = JSON.parse(notesWithItemDetails(JSON.stringify({ flaws: "small stain" }), {
      garment: "top",
      measurements: { pitToPit: "22", length: "" },
      labels: " Bin 2 ",
      cog: "4",
      sku: "",
    }));
    expect(next).toEqual({
      flaws: "small stain",
      garment: "top",
      measurements: { top: { pitToPit: "22" } },
      vendooLabels: "Bin 2",
      cog: "4",
      sku: "",
    });
  });
});
