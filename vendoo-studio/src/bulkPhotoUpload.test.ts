import { describe, expect, it, vi } from "vitest";
import { bulkListingNotes, createBulkPhotoListings, rotationForTurns } from "./bulkPhotoUpload";

const noPhotos = vi.fn().mockResolvedValue([]);

function photo(name: string): File {
  return new File(["photo"], name, { type: "image/jpeg" });
}

describe("bulkListingNotes", () => {
  it("keeps the item's size tag in existing seller notes", () => {
    expect(JSON.parse(bulkListingNotes({ cog: "", labels: "" }, { sizeTag: " XL / TG " })!))
      .toEqual({ sellerNotes: "Size tag: XL / TG" });
    expect(bulkListingNotes({ cog: "", labels: "" }, { sizeTag: "  " })).toBeUndefined();
  });
  it("stores normalized COG and labels in Item Details notes", () => {
    expect(JSON.parse(bulkListingNotes({ cog: " 12.50 ", labels: " To List, Bin 4, " }) || "{}"))
      .toEqual({ cog: "12.50", vendooLabels: "To List, Bin 4" });
  });

  it("adds the item's own SKU and typed measurements", () => {
    const notes = bulkListingNotes(
      { cog: "8", labels: "" },
      { sku: " LEV-505 ", sizeTag: " 32 × 30 ", garment: "pants", measurements: { waist: "16", inseam: " ", rise: "11" } },
    );
    expect(JSON.parse(notes || "{}")).toEqual({
      cog: "8",
      vendooLabels: "",
      sku: "LEV-505",
      sellerNotes: "Size tag: 32 × 30",
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
  it.each(["batch/coat/", "batch\\coat\\"])("keeps folder rotations when uploaded names include %s", async (prefix) => {
    const upright = photo("front.jpg");
    const sideways = photo("tag.jpg");
    const lost = photo("lost.jpg");
    for (const file of [upright, sideways, lost]) {
      Object.defineProperty(file, "webkitRelativePath", { value: `batch/coat/${file.name}` });
    }
    const rotatePhoto = vi.fn().mockResolvedValue({ id: "saved-tag" });
    const result = await createBulkPhotoListings([
      { folder: "batch/coat", files: [lost, sideways, upright], rotations: new Map([[lost, 180], [sideways, 90]]) },
    ], { cog: "", labels: "" }, {
      create: vi.fn().mockResolvedValue({ id: "coat" }),
      uploadPhotos: vi.fn().mockResolvedValue({ count: 2, photos: [
        { id: "saved-tag", original_filename: `${prefix}tag.jpg` },
        { id: "saved-front", original_filename: `${prefix}front.jpg` },
      ], errors: ["lost.jpg: Invalid photo"] }),
      rotatePhoto,
      photos: noPhotos,
    });
    expect(rotatePhoto).toHaveBeenCalledExactlyOnceWith("coat", "saved-tag", 90);
    expect(result[0]!.errors).toEqual(["lost.jpg: Invalid photo"]);
  });

  it("keeps going when one folder's upload is refused, and names that folder by its path", async () => {
    const create = vi.fn()
      .mockResolvedValueOnce({ id: "coat" })
      .mockResolvedValueOnce({ id: "jeans" })
      .mockResolvedValueOnce({ id: "boots" });
    const uploadPhotos = vi.fn()
      .mockResolvedValueOnce({ count: 1, photos: [] })
      .mockRejectedValueOnce(new Error("Maximum 20 photos per listing"))
      .mockResolvedValueOnce({ count: 2, photos: [] });

    const result = await createBulkPhotoListings([
      { folder: "batch/A/Coat", files: [photo("a.jpg")] },
      { folder: "batch/B/Jeans", files: [photo("b.jpg")] },
      { folder: "batch/C/Boots", files: [photo("c.jpg"), photo("d.jpg")] },
    ], { cog: "", labels: "" }, { create, uploadPhotos, rotatePhoto: vi.fn(), photos: noPhotos });

    expect(create).toHaveBeenNthCalledWith(2, { title: "Jeans" });
    expect(result).toEqual([
      { convId: "coat", folder: "batch/A/Coat", count: 1, errors: [] },
      { convId: "jeans", folder: "batch/B/Jeans", count: 0, errors: ["batch/B/Jeans: Maximum 20 photos per listing"] },
      { convId: "boots", folder: "batch/C/Boots", count: 2, errors: [] },
    ]);
  });

  it("counts the photos a refused upload already saved, so the draft still generates", async () => {
    const photos = vi.fn().mockResolvedValue([{ id: "p1" }, { id: "p2" }]);
    const result = await createBulkPhotoListings([
      { folder: null, files: [photo("a.jpg"), photo("b.jpg"), photo("c.jpg")] },
    ], { cog: "", labels: "" }, {
      create: vi.fn().mockResolvedValue({ id: "coat" }),
      uploadPhotos: vi.fn().mockRejectedValue(new Error("Failed to fetch")),
      rotatePhoto: vi.fn(),
      photos,
    });

    expect(photos).toHaveBeenCalledWith("coat");
    expect(result).toEqual([{ convId: "coat", folder: null, count: 2, errors: ["New Listing: Failed to fetch"] }]);
  });

  it("says the saved photos are unknown when the refused draft's photos cannot be read either", async () => {
    const result = await createBulkPhotoListings([
      { folder: "batch/Coat", files: [photo("a.jpg")] },
    ], { cog: "", labels: "" }, {
      create: vi.fn().mockResolvedValue({ id: "coat" }),
      uploadPhotos: vi.fn().mockRejectedValue(new Error("Failed to fetch")),
      rotatePhoto: vi.fn(),
      photos: vi.fn().mockRejectedValue(new Error("Failed to fetch")),
    });

    expect(result[0]).toMatchObject({ count: 0, errors: [
      "batch/Coat: Failed to fetch Anteroom couldn't check which of its photos were saved; open the draft to check.",
    ] });
  });

  it("keeps same-named photos in different item folders independent", async () => {
    const first = photo("front.jpg");
    const second = photo("front.jpg");
    const rotatePhoto = vi.fn().mockResolvedValue({});
    const create = vi.fn().mockResolvedValueOnce({ id: "coat" }).mockResolvedValueOnce({ id: "jeans" });
    const uploadPhotos = vi.fn()
      .mockResolvedValueOnce({ count: 1, photos: [{ id: "coat-front", original_filename: "batch/coat/front.jpg" }] })
      .mockResolvedValueOnce({ count: 1, photos: [{ id: "jeans-front", original_filename: "batch/jeans/front.jpg" }] });
    await createBulkPhotoListings([
      { folder: "batch/coat", files: [first], rotations: new Map([[first, 90]]) },
      { folder: "batch/jeans", files: [second], rotations: new Map([[second, -90]]) },
    ], { cog: "", labels: "" }, { create, uploadPhotos, rotatePhoto, photos: noPhotos });
    expect(rotatePhoto.mock.calls).toEqual([["coat", "coat-front", 90], ["jeans", "jeans-front", -90]]);
  });

  it("keeps an exact filename containing a literal backslash distinct from its basename", async () => {
    const named = photo("tag\\front.jpg");
    const plain = photo("front.jpg");
    const rotatePhoto = vi.fn().mockResolvedValue({});
    await createBulkPhotoListings([
      { folder: null, files: [named, plain], rotations: new Map([[named, 90], [plain, -90]]) },
    ], { cog: "", labels: "" }, {
      create: vi.fn().mockResolvedValue({ id: "coat" }),
      uploadPhotos: vi.fn().mockResolvedValue({ count: 2, photos: [
        { id: "named", original_filename: "tag\\front.jpg" },
        { id: "plain", original_filename: "front.jpg" },
      ] }),
      rotatePhoto,
      photos: noPhotos,
    });
    expect(rotatePhoto.mock.calls).toEqual([["coat", "named", 90], ["coat", "plain", -90]]);
  });

  it.each([false, true])("does not rotate an untouched filename resembling a path (directory: %s)", async (directory) => {
    const untouched = photo("tag\\front.jpg");
    const turned = photo("front.jpg");
    if (directory) {
      for (const file of [untouched, turned]) {
        Object.defineProperty(file, "webkitRelativePath", { value: `batch/coat/${file.name}` });
      }
    }
    const rotatePhoto = vi.fn().mockResolvedValue({});
    await createBulkPhotoListings([
      { folder: null, files: [untouched, turned], rotations: new Map([[turned, -90]]) },
    ], { cog: "", labels: "" }, {
      create: vi.fn().mockResolvedValue({ id: "coat" }),
      uploadPhotos: vi.fn().mockResolvedValue({ count: 2, photos: [
        { id: "untouched", original_filename: directory ? "batch/coat/tag\\front.jpg" : "tag\\front.jpg" },
        { id: "turned", original_filename: directory ? "batch/coat/front.jpg" : "front.jpg" },
      ] }),
      rotatePhoto,
      photos: noPhotos,
    });
    expect(rotatePhoto.mock.calls).toEqual([["coat", "turned", -90]]);
  });

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
      { create, uploadPhotos, rotatePhoto: vi.fn(), photos: noPhotos },
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
      { create, uploadPhotos, rotatePhoto: vi.fn(), photos: noPhotos },
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
      { create, uploadPhotos, rotatePhoto: vi.fn(), photos: noPhotos },
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
      { create, uploadPhotos, rotatePhoto, photos: noPhotos },
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
