import { describe, expect, it, vi } from "vitest";
import type { ComposerImage } from "./ComposerAttachments";
import { uploadComposerDraftPhotos } from "./composerDraftPhotos";

function makeImage(id: string, name: string): ComposerImage {
  return { id, file: new File(["img"], name, { type: "image/jpeg" }) };
}

describe("uploadComposerDraftPhotos", () => {
  it("uploads attached images as listing photos and generates when there is no text or listing yet", async () => {
    const attached = [makeImage("img-1", "front.jpg"), makeImage("img-2", "tag.jpg")];
    const uploadPhoto = vi.fn(async (_file: File) => {});
    const invalidatePhotos = vi.fn(async () => {});
    const clearImages = vi.fn();
    const setError = vi.fn();
    const sendMessage = vi.fn();
    const generate = vi.fn();

    const ok = await uploadComposerDraftPhotos(attached, "", false, {
      uploadPhoto,
      invalidatePhotos,
      clearImages,
      setError,
      sendMessage,
      generate,
    });

    expect(ok).toBe(true);
    expect(uploadPhoto.mock.calls).toEqual(attached.map(({ file }) => [file]));
    expect(invalidatePhotos).toHaveBeenCalledTimes(1);
    expect(clearImages).toHaveBeenCalledWith(["img-1", "img-2"]);
    expect(generate).toHaveBeenCalledTimes(1);
    expect(sendMessage).not.toHaveBeenCalled();
    expect(setError).not.toHaveBeenCalled();
  });

  it("uploads attached images and sends the seller's note without duplicating chat attachments", async () => {
    const attached = [makeImage("img-1", "front.jpg")];
    const sendMessage = vi.fn();
    const generate = vi.fn();

    const ok = await uploadComposerDraftPhotos(attached, "Size M, tiny pinhole on cuff", false, {
      uploadPhoto: vi.fn(async () => {}),
      invalidatePhotos: vi.fn(async () => {}),
      clearImages: vi.fn(),
      setError: vi.fn(),
      sendMessage,
      generate,
    });

    expect(ok).toBe(true);
    expect(sendMessage).toHaveBeenCalledWith("Size M, tiny pinhole on cuff");
    expect(generate).not.toHaveBeenCalled();
  });

  it("uploads images without overwriting an existing listing when text is empty", async () => {
    const attached = [makeImage("img-1", "front.jpg")];
    const sendMessage = vi.fn();
    const generate = vi.fn();

    const ok = await uploadComposerDraftPhotos(attached, "", true, {
      uploadPhoto: vi.fn(async () => {}),
      invalidatePhotos: vi.fn(async () => {}),
      clearImages: vi.fn(),
      setError: vi.fn(),
      sendMessage,
      generate,
    });

    expect(ok).toBe(true);
    expect(generate).not.toHaveBeenCalled();
    expect(sendMessage).not.toHaveBeenCalled();
  });

  it("clears only uploaded images and surfaces the error on partial upload failure", async () => {
    const attached = [makeImage("img-1", "image.jpg"), makeImage("img-2", "image.jpg")];
    const clearImages = vi.fn();
    const setError = vi.fn();
    const generate = vi.fn();
    const invalidatePhotos = vi.fn(async () => {});

    const ok = await uploadComposerDraftPhotos(attached, "", false, {
      uploadPhoto: vi.fn()
        .mockRejectedValueOnce(new Error("image.jpg: choose a photo smaller than 10 MB."))
        .mockResolvedValueOnce(undefined),
      invalidatePhotos,
      clearImages,
      setError,
      sendMessage: vi.fn(),
      generate,
    });

    expect(ok).toBe(false);
    expect(invalidatePhotos).toHaveBeenCalledTimes(1);
    expect(clearImages).toHaveBeenCalledWith(["img-2"]);
    expect(setError).toHaveBeenCalledWith("image.jpg: choose a photo smaller than 10 MB.");
    expect(generate).not.toHaveBeenCalled();
  });

  it("keeps images and surfaces the error when all uploads fail or throw", async () => {
    const attached = [makeImage("img-1", "bad.jpg")];
    const clearImages = vi.fn();
    const setError = vi.fn();
    const generate = vi.fn();

    const ok = await uploadComposerDraftPhotos(attached, "", false, {
      uploadPhoto: vi.fn(async () => {
        throw new Error("Network offline");
      }),
      invalidatePhotos: vi.fn(async () => {}),
      clearImages,
      setError,
      sendMessage: vi.fn(),
      generate,
    });

    expect(ok).toBe(false);
    expect(clearImages).not.toHaveBeenCalled();
    expect(setError).toHaveBeenCalledWith("Network offline");
    expect(generate).not.toHaveBeenCalled();
  });
});
