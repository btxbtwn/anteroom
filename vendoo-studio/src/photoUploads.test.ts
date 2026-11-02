import { expect, test } from "vitest";
import { photosUploading, trackPhotoUpload } from "./photoUploads";

test("generation waits for every overlapping batch on the same draft", async () => {
  let finish!: () => void;
  const first = trackPhotoUpload("coat", () => new Promise<void>(resolve => { finish = resolve; }));
  expect(photosUploading("coat")).toBe(true);
  expect(photosUploading("shoes")).toBe(false);
  await trackPhotoUpload("coat", async () => undefined);
  expect(photosUploading("coat")).toBe(true);
  finish();
  await first;
  expect(photosUploading("coat")).toBe(false);
});

test("failed uploads release the draft for retry", async () => {
  await expect(trackPhotoUpload("failed", async () => { throw new Error("offline"); })).rejects.toThrow("offline");
  expect(photosUploading("failed")).toBe(false);
});
