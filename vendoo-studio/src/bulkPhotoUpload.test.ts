import { describe, expect, it, vi } from "vitest";
import { bulkListingNotes, createBulkPhotoListings, rotationForTurns } from "./bulkPhotoUpload";

function photo(name: string): File {
  return new File(["photo"], name, { type: "image/jpeg" });
}

describe("bulkListingNotes", () => {
  it("stores normalized COG and labels in Item Details notes", () => {
    expect(JSON.parse(bulkListingNotes({ cog: " 12.50 ", labels: " To List, Bin 4, " }) || "{}"))
      .toEqual({ cog: "12.50", vendooLabels: "To List, Bin 4" });
  });

  it("adds the item's own SKU and typed measurements", () => {
    const notes = bulkListingNotes(
      { cog: "8", labels: "" },
      { sku: " LEV-505 ", garment: "pants", measurements: { waist: "16", inseam: " ", rise: "11" } },
    );
    expect(JSON.parse(notes || "{}")).toEqual({
      cog: "8",
      vendooLabels: "",
      sku: "LEV-505",
      garment: "pants",
      measurements: { pants: { waist: "16", rise: "11" } },
    });
  });

  it("leaves the garment out when nothing was measured", () => {
    expect(bulkListingNotes({ cog: "", labels: "" }, { sku: "", garment: "top", measurements: { length: " " } }))
      .toBeUndefined();
  });

  it("omits notes when both shared fields are blank", () => {
    expect(bulkListingNotes({ cog: "", labels: " , " })).toBeUndefined();
  });
});

describe("createBulkPhotoListings", () => {
  it("applies the shared notes to every created draft", async () => {
    const create = vi.fn()
      .mockResolvedValueOnce({ id: "listing-a" })
      .mockResolvedValueOnce({ id: "listing-b" });
    const uploadPhotos = vi.fn().mockResolvedValue({ ok: true, count: 1, photos: [] });

    const result = await createBulkPhotoListings(
      [
        { folder: "upload/Item A", files: [photo("a.jpg")] },
        { folder: "upload/Item B", files: [photo("b.jpg")] },
      ],
      { cog: "8", labels: "To List, Shelf A" },
      { create, uploadPhotos, rotatePhoto: vi.fn() },
    );

    const expectedNotes = JSON.stringify({ cog: "8", vendooLabels: "To List, Shelf A" });
    expect(create).toHaveBeenNthCalledWith(1, { title: "Item A", notes: expectedNotes });
    expect(create).toHaveBeenNthCalledWith(2, { title: "Item B", notes: expectedNotes });
    expect(uploadPhotos).toHaveBeenNthCalledWith(1, "listing-a", expect.any(Array));
    expect(uploadPhotos).toHaveBeenNthCalledWith(2, "listing-b", expect.any(Array));
    expect(result.map((listing) => listing.convId)).toEqual(["listing-a", "listing-b"]);
  });

  it("keeps each item's SKU on its own draft", async () => {
    const create = vi.fn()
      .mockResolvedValueOnce({ id: "listing-a" })
      .mockResolvedValueOnce({ id: "listing-b" });
    const uploadPhotos = vi.fn().mockResolvedValue({ ok: true, count: 1, photos: [] });

    await createBulkPhotoListings(
      [
        { folder: "upload/Item A", files: [photo("a.jpg")], sku: "A1" },
        { folder: "upload/Item B", files: [photo("b.jpg")] },
      ],
      { cog: "", labels: "" },
      { create, uploadPhotos, rotatePhoto: vi.fn() },
    );

    expect(create).toHaveBeenNthCalledWith(1, { title: "Item A", notes: JSON.stringify({ sku: "A1" }) });
    expect(create).toHaveBeenNthCalledWith(2, { title: "Item B" });
  });

  it("puts every draft in the chosen box", async () => {
    const create = vi.fn().mockResolvedValue({ id: "listing-a" });
    const uploadPhotos = vi.fn().mockResolvedValue({ ok: true, count: 1, photos: [] });

    await createBulkPhotoListings(
      [{ folder: "upload/Item A", files: [photo("a.jpg")] }],
      { cog: "", labels: "", boxId: "box-1" },
      { create, uploadPhotos, rotatePhoto: vi.fn() },
    );

    expect(create).toHaveBeenCalledWith({ title: "Item A", box_id: "box-1" });
  });

  it("turns the photos rotated in the window once they are saved", async () => {
    const upright = photo("front.jpg");
    const sideways = photo("tag.jpg");
    const lost = photo("lost.jpg");
    const create = vi.fn().mockResolvedValue({ id: "listing-a" });
    // lost.jpg failed to upload, so it has no saved photo to turn.
    const uploadPhotos = vi.fn().mockResolvedValue({
      ok: true,
      count: 2,
      photos: [
        { id: "photo-1", original_filename: "front.jpg" },
        { id: "photo-2", original_filename: "tag.jpg" },
      ],
      errors: ["lost.jpg: File is not a valid image"],
    });
    const rotatePhoto = vi.fn().mockRejectedValue(new Error("This photo format cannot be rotated"));

    const result = await createBulkPhotoListings(
      [{
        folder: "upload/Item A",
        files: [upright, sideways, lost],
        rotations: new Map([[sideways, -90], [lost, 180]]),
      }],
      { cog: "", labels: "" },
      { create, uploadPhotos, rotatePhoto },
    );

    expect(rotatePhoto).toHaveBeenCalledTimes(1);
    expect(rotatePhoto).toHaveBeenCalledWith("listing-a", "photo-2", -90);
    expect(result[0]!.errors).toEqual([
      "lost.jpg: File is not a valid image",
      "tag.jpg: This photo format cannot be rotated",
    ]);
  });
});

describe("rotationForTurns", () => {
  it("maps quarter turns to the degrees the server rotates by", () => {
    expect([0, 1, 2, 3, 4, -1].map(rotationForTurns)).toEqual([null, 90, 180, -90, null, -90]);
  });
});
